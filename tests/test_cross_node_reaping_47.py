"""Daemonless reaping never acts on another node's PIDs (issue #47).

With ENABLE_DAEMON=False, `manage.py run_jobs` claims a job with no worker row and the attempt
records only its PID. On 2+ web containers sharing PostgreSQL, container B's sweep probed
container A's PID in its own PID namespace, found it missing, failed A's live job and ran the
retry alongside it; its stale pass could also SIGTERM/SIGKILL an unrelated local process that
happened to have the same PID. Every attempt now records the claiming node, and PIDs are only
probed or killed on that node (clause ZS-1a).
"""

import os
import subprocess
import sys
from datetime import timedelta

import pytest
from django.utils import timezone

from sqlery.core.claiming import get_node_id
from sqlery.executor import TaskExecutor
from tests.running_jobs import django_running_job

OTHER_NODE = "another-container"
DEAD_PID = 999999  # no such process here


def fast_task():
    return "ok"


@pytest.fixture
def live_process():
    """A real local process that must survive the sweep (stands in for a web worker)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait()


@pytest.fixture
def kills(monkeypatch):
    calls = []
    monkeypatch.setattr(TaskExecutor, "_kill_worker_process", lambda self, pid: calls.append(pid))
    return calls


def _running(**fields):
    return django_running_job(
        task_path="tests.test_cross_node_reaping_47.fast_task", queue_name="sync", **fields
    )


@pytest.mark.django_db
class TestOrphanReaperStaysOnItsNode:
    def test_live_job_on_another_node_is_not_reaped(self):
        """The issue's scenario: A's PID is missing in B's namespace, the job is A's and live."""
        job = _running(started_at=timezone.now(), worker_pid=DEAD_PID, node_id=OTHER_NODE)

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "running"

    def test_dead_claimer_on_this_node_is_still_reaped_at_once(self):
        job = _running(started_at=timezone.now(), worker_pid=DEAD_PID, node_id=get_node_id())

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "failed"
        assert "no longer exists" in job.error

    def test_attempt_without_a_node_is_never_probed(self):
        """Rows claimed before nodes were recorded fall back to the time threshold."""
        job = _running(started_at=timezone.now(), worker_pid=DEAD_PID, node_id=None)

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "running"


@pytest.mark.django_db
class TestStalePassKillsOnlyOnItsNode:
    def test_stale_job_of_another_node_is_failed_without_signalling(self, live_process, kills):
        job = _running(
            timeout_seconds=60,
            started_at=timezone.now() - timedelta(minutes=5),
            worker_pid=live_process.pid,
            node_id=OTHER_NODE,
        )

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "failed"
        assert kills == []
        assert live_process.poll() is None  # the unrelated local process is untouched

    def test_stale_job_of_this_node_still_kills_its_hung_claimer(self, live_process, kills):
        job = _running(
            timeout_seconds=60,
            started_at=timezone.now() - timedelta(minutes=5),
            worker_pid=live_process.pid,
            node_id=get_node_id(),
        )

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "failed"
        assert kills == [live_process.pid]

    def test_stale_pass_never_kills_its_own_process(self, kills):
        job = _running(
            timeout_seconds=60,
            started_at=timezone.now() - timedelta(minutes=5),
            worker_pid=os.getpid(),
            node_id=get_node_id(),
        )

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "failed"
        assert kills == []

    def test_stale_job_without_a_node_is_failed_without_signalling(self, live_process, kills):
        job = _running(
            timeout_seconds=60,
            started_at=timezone.now() - timedelta(minutes=5),
            worker_pid=live_process.pid,
            node_id=None,
        )

        TaskExecutor()._cleanup_stale_jobs()

        job.refresh_from_db()
        assert job.status == "failed"
        assert kills == []


@pytest.mark.django_db
def test_django_claim_records_the_node():
    from sqlery.django_sqlery.models import JobAttempt, QueuedJob

    job = QueuedJob.objects.create(task_path="tests.test_cross_node_reaping_47.fast_task")
    job.mark_running()

    assert JobAttempt.open_for(job.id).node_id == get_node_id()


def test_standalone_claim_records_the_node():
    from sqlery.fastapi_sqlery import attempt_sql

    row = attempt_sql.new_attempt(1, None, timezone.now())

    assert row.node_id == get_node_id()
    assert row.worker_pid == os.getpid()


def test_node_id_env_var_names_the_node(monkeypatch):
    """Replicas that share a hostname must set NODE_ID (documented in ENVIRONMENT_VARIABLES)."""
    monkeypatch.setenv("NODE_ID", "web-2")
    assert get_node_id() == "web-2"
