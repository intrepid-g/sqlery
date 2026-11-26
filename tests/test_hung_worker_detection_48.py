"""A hung worker is detected and killed with its job child (issue #48).

The daemon used to bump ``last_heartbeat`` for every worker whose PID existed, every cycle,
just before the safety net checked for stale beats: a deadlocked or frozen worker looked
alive forever, kept its slot and, with ``allow_parallel=False``, wedged its queue. The safety
net also read only "active" rows (beat within WORKER_ALIVE_TIMEOUT), so no row could ever
reach its 3x threshold, and it killed only the worker, not the job child in its own process
group. Now workers alone write their beats, the safety net looks at every row of a worker the
pool started, kills the job child's group first, and silence only counts while the daemon
can reach the database (no false kills after an outage).
"""

import os
import signal
import subprocess
import sys
import time
from datetime import timedelta

import pytest
from django.utils import timezone

from sqlery.core.daemon import DaemonManager
from sqlery.core.worker_pool import WorkerPoolManager
from tests.running_jobs import django_make_running

pytestmark = pytest.mark.django_db

IGNORE_USR1 = "import signal, time; signal.signal(signal.SIGUSR1, signal.SIG_IGN); time.sleep(120)"


def fast_task():
    return "ok"


@pytest.fixture
def backend():
    from sqlery.django_sqlery.backend import DjangoBackend

    return DjangoBackend()


@pytest.fixture
def procs():
    started = []

    def start(new_session=False):
        proc = subprocess.Popen([sys.executable, "-c", IGNORE_USR1], start_new_session=new_session)
        started.append(proc)
        time.sleep(0.2)  # let it install SIG_IGN before any SIGUSR1
        return proc

    yield start
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


@pytest.fixture
def pool(backend):
    pool = WorkerPoolManager(0, ["default"], backend=backend)
    yield pool
    pool._child_processes.clear()


def _worker_row(pool, pid, beat_age, status="busy", job=None):
    from sqlery.django_sqlery.models import Worker

    worker = Worker.objects.create(node_id=pool.node_id, pid=pid, status=status)
    Worker.objects.filter(id=worker.id).update(
        last_heartbeat=timezone.now() - timedelta(seconds=beat_age),
        current_job_id=job.id if job else None,
    )
    worker.refresh_from_db()
    return worker


def _running_job(worker, child_pid):
    from sqlery.django_sqlery.models import QueuedJob

    job = QueuedJob.objects.create(task_path="tests.test_hung_worker_detection_48.fast_task")
    return django_make_running(job, worker=worker, worker_pid=worker.pid, child_pid=child_pid)


def _daemon(tmp_path, pool):
    daemon = DaemonManager(pid_dir=tmp_path)
    assert daemon.node_id == pool.node_id
    return daemon


def _gone(proc, timeout=10):
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def test_daemon_signals_but_never_writes_a_workers_heartbeat(tmp_path, backend, pool, procs):
    """A worker that ignores SIGUSR1 keeps its old beat: the daemon no longer fakes one."""
    proc = procs()
    pool._child_processes.append(proc)
    worker = _worker_row(pool, proc.pid, beat_age=20)
    before = worker.last_heartbeat

    _daemon(tmp_path, pool)._heartbeat_workers(backend, pool)

    worker.refresh_from_db()
    assert worker.last_heartbeat == before
    assert proc.poll() is None  # signalled (and ignored), not killed


def test_daemon_still_asks_a_silent_worker_it_started_to_beat(tmp_path, backend, pool, monkeypatch):
    """After an outage longer than WORKER_ALIVE_TIMEOUT an idle worker beats only on
    SIGUSR1, so the daemon must signal it although its row is no longer 'active'."""
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(DaemonManager, "is_process_running", lambda self, pid: True)
    monkeypatch.setattr(pool, "started_pids", lambda: {424242})
    _worker_row(pool, 424242, beat_age=500, status="idle")
    _worker_row(pool, 434343, beat_age=500, status="idle")  # not ours: PID may be reused

    _daemon(tmp_path, pool)._heartbeat_workers(backend, pool)

    assert sent == [(424242, signal.SIGUSR1)]


def test_hung_worker_and_its_job_child_are_killed(backend, pool, procs):
    worker_proc = procs()
    child_proc = procs(new_session=True)  # a job child leads its own process group
    pool._child_processes.append(worker_proc)
    worker = _worker_row(pool, worker_proc.pid, beat_age=200)
    job = _running_job(worker, child_proc.pid)
    worker.current_job_id = job.id
    worker.save(update_fields=["current_job_id"])

    found = pool.detect_and_fix_irregularities()

    assert found["stalled_workers_killed"] == 1
    assert found["details"][0]["job_child_killed"] == child_proc.pid
    assert _gone(child_proc)
    assert _gone(worker_proc)
    worker.refresh_from_db()
    assert worker.status == "dead"


def test_hung_worker_already_marked_dead_is_still_killed(backend, pool, procs):
    """The reaper marks a silent worker dead after WORKER_ALIVE_TIMEOUT; its process lives."""
    worker_proc = procs()
    pool._child_processes.append(worker_proc)
    _worker_row(pool, worker_proc.pid, beat_age=200, status="dead")

    found = pool.detect_and_fix_irregularities()

    assert found["stalled_workers_killed"] == 1
    assert _gone(worker_proc)


def test_reaper_leaves_a_live_worker_it_started_to_the_safety_net(backend, pool, procs):
    """Marked dead at 30s, its job would be failed (and retried) while it still runs."""
    worker_proc = procs()
    pool._child_processes.append(worker_proc)
    worker = _worker_row(pool, worker_proc.pid, beat_age=60)

    assert pool.cleanup_dead_workers() == 0

    worker.refresh_from_db()
    assert worker.status == "busy"


def test_a_process_the_pool_did_not_start_is_never_killed(backend, pool, procs):
    """The PID of an old row may belong to an unrelated process by now."""
    stranger = procs()
    _worker_row(pool, stranger.pid, beat_age=200)

    found = pool.detect_and_fix_irregularities()

    assert found["stalled_workers_killed"] == 0
    assert stranger.poll() is None


def test_no_worker_is_killed_right_after_a_database_outage(backend, pool, procs):
    """Every beat is old once the database is back; workers get the watch window to beat."""
    worker_proc = procs()
    pool._child_processes.append(worker_proc)
    worker = _worker_row(pool, worker_proc.pid, beat_age=500)
    pool.watched_since = timezone.now() - timedelta(seconds=5)

    assert pool.detect_and_fix_irregularities()["stalled_workers_killed"] == 0
    assert worker_proc.poll() is None

    pool.watched_since = timezone.now() - timedelta(seconds=200)
    assert pool.detect_and_fix_irregularities()["stalled_workers_killed"] == 1
    assert _gone(worker_proc)
    worker.refresh_from_db()
    assert worker.status == "dead"


def test_zombie_sweep_spares_jobs_right_after_a_database_outage(backend):
    from sqlery.django_sqlery.models import JobAll, Worker

    worker = Worker.objects.create(node_id="elsewhere", pid=5, status="busy")
    Worker.objects.filter(id=worker.id).update(
        last_heartbeat=timezone.now() - timedelta(seconds=500)
    )
    job = _running_job(worker, child_pid=None)
    Worker.objects.filter(id=worker.id).update(current_job_id=job.id)

    recent = timezone.now() - timedelta(seconds=5)
    assert DaemonManager._fail_zombie_running_jobs(backend, watched_since=recent) == 0
    assert JobAll.objects.get(id=job.id).status == "running"

    assert DaemonManager._fail_zombie_running_jobs(backend) == 1
