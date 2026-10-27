"""#163: daemon workers forked from a preloaded launcher (Celery-style memory sharing).

Also the standalone daemon regressions found while measuring it: the daemon's own heartbeat
row carried its real PID, so the daemon counted itself as a worker and sent itself SIGUSR1.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from sqlery.core.job import job
from sqlery.core.launcher import (
    START_FORK,
    START_SPAWN,
    LaunchedWorker,
    WorkerLauncher,
    exit_code,
    load_start_method,
    parse_module_list,
    preload_module_names,
    resolve_start_method,
)
from sqlery.core.worker_pool import WorkerPoolManager


@job
def whoami():
    return os.getpid()


@pytest.fixture
def backend(tmp_path, monkeypatch):
    from sqlery import compat
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    database._engine = None
    database.init_database(f"sqlite:///{tmp_path / 'launcher.db'}")
    b = SQLAlchemyBackend()
    compat._backend = b
    yield b
    compat._backend = None
    database._engine = None


@pytest.fixture
def launcher():
    started = []

    def make(**kwargs):
        lau = WorkerLauncher(**kwargs)
        started.append(lau)
        return lau

    yield make
    for lau in started:
        for worker in list(lau._workers.values()):
            try:
                os.kill(worker.pid, signal.SIGKILL)
            except OSError:
                pass
        lau.close()


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


def parent_of(pid):
    with open(f"/proc/{pid}/stat") as f:
        return int(f.read().rsplit(")", 1)[1].split()[1])


# --- settings -----------------------------------------------------------------------------


def test_fork_is_the_default_and_spawn_is_settable(monkeypatch):
    monkeypatch.delenv("SQLERY_WORKER_START_METHOD", raising=False)
    assert load_start_method() == START_FORK
    monkeypatch.setenv("SQLERY_WORKER_START_METHOD", "spawn")
    assert load_start_method() == START_SPAWN
    assert resolve_start_method("FORK") == START_FORK
    assert resolve_start_method("forkserver") == START_FORK  # unknown: Celery's behaviour
    assert resolve_start_method("fork", can_fork=False) == START_SPAWN


def test_preload_modules_setting(monkeypatch):
    assert parse_module_list(None) is None
    assert parse_module_list("a.tasks, b.tasks,") == ["a.tasks", "b.tasks"]
    assert parse_module_list([]) == []
    monkeypatch.setenv("SQLERY_WORKER_PRELOAD_MODULES", "json,os.path")
    assert preload_module_names() == ["json", "os.path"]


def test_django_autodiscovers_installed_apps_tasks_modules(monkeypatch):
    from django.apps import apps
    from django.utils.module_loading import module_has_submodule

    monkeypatch.delenv("SQLERY_WORKER_PRELOAD_MODULES", raising=False)
    expected = [
        f"{a.name}.tasks" for a in apps.get_app_configs() if module_has_submodule(a.module, "tasks")
    ]
    assert preload_module_names() == expected


def test_exit_codes_match_popen():
    assert exit_code(0) == 0
    assert exit_code(3 << 8) == 3
    assert exit_code(signal.SIGKILL) == -signal.SIGKILL


# --- the launcher -------------------------------------------------------------------------


def test_forked_worker_runs_jobs_and_is_the_launchers_child(backend, launcher):
    lau = launcher()
    queued = whoami.enqueue()
    worker = lau.spawn(["default"])
    assert isinstance(worker, LaunchedWorker)
    assert parent_of(worker.pid) == lau.pid  # not the daemon's: the launcher reaps it
    row = wait_for(lambda: (j := backend.get_job_by_id(queued.id)) and j.status == "success" and j)
    assert int(row.output) not in (os.getpid(), lau.pid)  # ran in the worker's job child
    assert worker.poll() is None


def test_exit_is_reported_to_the_daemon(backend, launcher):
    lau = launcher()
    worker = lau.spawn(["default"])
    wait_for(lambda: backend.get_worker_heartbeats(active_only=True))
    worker.terminate()
    assert worker.wait(timeout=15) == 0  # graceful SIGTERM shutdown
    killed = lau.spawn(["default"])
    killed.kill()
    assert killed.wait(timeout=15) == -signal.SIGKILL


def test_preloaded_once_in_the_launcher_and_shared(backend, launcher, tmp_path, monkeypatch):
    marker = tmp_path / "imports.txt"
    (tmp_path / "preloaded_mod_163.py").write_text(textwrap.dedent(f"""
        import os
        with open({str(marker)!r}, "a") as f:
            f.write(f"{{os.getpid()}}\\n")
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("SQLERY_WORKER_PRELOAD_MODULES", "preloaded_mod_163")
    lau = launcher()
    first = lau.spawn(["default"])
    second = lau.spawn(["default"])
    wait_for(lambda: len(backend.get_worker_heartbeats(active_only=True)) >= 2)
    importers = marker.read_text().split()
    assert importers == [str(lau.pid)]  # imported once, by the launcher, before forking
    assert {parent_of(first.pid), parent_of(second.pid)} == {lau.pid}


PG_URL = os.environ.get("SQLERY_TEST_PG_URL")


@job
def pg_backend_pid():
    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database

    with database.get_engine().connect() as conn:
        return conn.execute(text("SELECT pg_backend_pid()")).scalar()


@pytest.mark.postgres
@pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")
def test_worker_never_shares_the_daemons_pg_connection(tmp_path, launcher, monkeypatch):
    from sqlalchemy import text

    from sqlery import compat
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend
    from tests.pg_url import sqlalchemy_pg_url

    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    database._engine = None
    database.init_database(sqlalchemy_pg_url(PG_URL))
    backend = compat._backend = SQLAlchemyBackend()
    try:
        with database.get_engine().connect() as held:  # the "daemon" connection, kept open
            daemon_pid = held.execute(text("SELECT pg_backend_pid()")).scalar()
            queued = pg_backend_pid.enqueue()
            launcher().spawn(["default"])
            row = wait_for(
                lambda: (j := backend.get_job_by_id(queued.id)) and j.status == "success" and j
            )
            assert int(row.output) != daemon_pid
            assert held.execute(text("SELECT 1")).scalar() == 1  # and it still works
    finally:
        engine = database.get_engine()
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
            conn.commit()
        engine.dispose()
        compat._backend = None
        database._engine = None


def test_a_dead_launcher_is_restarted_and_orphans_are_still_tracked(backend, launcher):
    lau = launcher()
    orphan = lau.spawn(["default"])
    old_launcher = lau.pid
    os.kill(old_launcher, signal.SIGKILL)
    wait_for(lambda: not lau.alive())
    assert orphan.poll() is None  # still running, now reparented
    replacement = lau.spawn(["default"])
    assert lau.pid != old_launcher and parent_of(replacement.pid) == lau.pid
    os.kill(orphan.pid, signal.SIGKILL)
    assert wait_for(lambda: orphan.poll() is not None) and orphan.returncode == -1


def test_close_stops_the_launcher_not_the_workers(backend, launcher):
    lau = launcher()
    worker = lau.spawn(["default"])
    pid = lau.pid
    lau.close()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    assert worker.poll() is None
    os.kill(worker.pid, signal.SIGTERM)


# --- the pool -----------------------------------------------------------------------------


def test_pool_forks_by_default_and_spawns_when_asked(backend, monkeypatch):
    monkeypatch.delenv("SQLERY_WORKER_START_METHOD", raising=False)
    pool = WorkerPoolManager(1, ["default"], backend=backend)
    try:
        forked = pool.spawn_worker()
        assert isinstance(forked, LaunchedWorker)
        assert parent_of(forked.pid) == pool._launcher.pid
        monkeypatch.setenv("SQLERY_WORKER_START_METHOD", "spawn")
        spawned = pool.spawn_worker()
        assert isinstance(spawned, subprocess.Popen)
        assert pool.stop_all_workers() >= 2
        for handle in (forked, spawned):
            handle.wait(timeout=15)
        pool.reap_children()
        assert pool._child_processes == []
    finally:
        for handle in pool._child_processes:
            handle.kill()
        pool.close()


def test_pool_falls_back_to_spawn_if_the_launcher_fails(backend, monkeypatch):
    pool = WorkerPoolManager(1, ["default"], backend=backend)

    def broken(self, queues):
        raise RuntimeError("no fork here")

    monkeypatch.setattr(WorkerLauncher, "spawn", broken)
    popen = []
    monkeypatch.setattr(
        "sqlery.core.worker_pool.subprocess.Popen",
        lambda *a, **k: popen.append(k) or type("P", (), {"pid": 4321})(),
    )
    pool.spawn_worker()
    assert popen and popen[0]["start_new_session"]


# --- standalone daemon regressions --------------------------------------------------------


def test_standalone_daemon_row_is_marked_pid_zero(backend):
    backend.update_worker_heartbeat(worker_id="daemon_testnode", status="idle", current_job_id=None)
    rows = [w for w in backend.get_worker_heartbeats(active_only=True) if w.pid == 0]
    assert len(rows) == 1


@pytest.mark.parametrize("start_method", [START_FORK, START_SPAWN])
def test_standalone_daemon_runs_n_workers_and_survives(tmp_path, start_method):
    """It used to SIGUSR1 itself (exit 138) and count itself as one of its workers."""
    url = f"sqlite:///{tmp_path / 'daemon.db'}"
    script = textwrap.dedent(f"""
        import os, sys, time
        from sqlery.compat import initialize, get_backend
        initialize(database_url={url!r}, enable_daemon=False)
        from sqlery.core.daemon import DaemonManager
        from sqlery.core import worker_pool
        pools = []
        real = worker_pool.WorkerPoolManager.__init__
        def track(self, *a, **k):
            real(self, *a, **k); pools.append(self)
        worker_pool.WorkerPoolManager.__init__ = track
        import sqlery.core.daemon as d
        d.WorkerPoolManager = worker_pool.WorkerPoolManager
        DaemonManager()._run_daemon(max_workers=2, once=True)
        print("spawned", len(pools[0]._child_processes))
    """)
    env = dict(
        os.environ,
        SQLERY_DATABASE_URL=url,
        LOG_DIR=str(tmp_path),
        SQLERY_WORKER_START_METHOD=start_method,
    )
    env.pop("DJANGO_SETTINGS_MODULE", None)
    done = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "spawned 2" in done.stdout


def test_stopping_workers_never_signals_the_process_group(backend, monkeypatch):
    """The daemon's row has PID 0; os.kill(0, SIGTERM) used to hit the caller's whole group."""
    import socket

    backend.update_worker_heartbeat(
        worker_id=f"daemon_{socket.gethostname()}", status="idle", current_job_id=None
    )
    sent = []
    monkeypatch.setattr("sqlery.core.worker_pool.os.kill", lambda pid, sig: sent.append(pid))
    pool = WorkerPoolManager(1, ["default"], backend=backend)
    assert pool.stop_all_workers() == 0
    assert pool._kill_worker_process(0) is False
    assert sent == []
