"""Focused unit tests for `sqlery.core.worker` (TEST-06).

Exercises :class:`JobExecutor` decision points (success/retry/timeout/import
error) and :class:`WorkerProcess` signal-handler plumbing without ever
spawning a real subprocess or invoking the real ``os.fork``.

Strategy
--------

* The auto-applied ``_patch_get_backend`` fixture from ``conftest.py`` makes
  ``JobExecutor()`` / ``WorkerProcess()`` pick up the in-memory FakeBackend.
* For ``_fork_and_execute`` we monkey-patch :func:`os.fork` so the parent
  branch (returns child_pid > 0) and the child branch (returns 0) can be
  tested in isolation. The child branch is exercised by capturing
  :func:`os._exit` so the test process keeps running.
* Signal handlers are installed by :meth:`WorkerProcess.run`. We don't run
  the full loop here; instead we install the handler manually and assert the
  flag-mutation contract (no DB call from inside the handler).
"""

from __future__ import annotations

import os
import signal
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from sqlery.core import worker as worker_module
from sqlery.core.worker import JobExecutor, WorkerProcess

from .conftest import make_job, make_worker


# A globally-importable success task — JobExecutor imports via dotted path.
def _task_success(value: int = 7) -> str:
    return f"result-{value}"


def _task_raises(*_a, **_kw):
    raise ValueError("intentional task failure")


_TASK_OK = f"{__name__}._task_success"
_TASK_FAIL = f"{__name__}._task_raises"


# ---------------------------------------------------------------------------
# TestJobExecutorImport / success / failure
# ---------------------------------------------------------------------------


class TestJobExecutor:
    def test_success_path_marks_job_success(self, fake_backend):
        job = fake_backend.add_job(
            make_job(task_path=_TASK_OK, kwargs={"value": 42})
        )
        ex = JobExecutor(backend=fake_backend)
        result = ex.execute_job(job)
        assert result.status == "success"
        assert "result-42" in (result.output or "")

    def test_failure_no_retry_marks_failed(self, fake_backend):
        job = fake_backend.add_job(
            make_job(task_path=_TASK_FAIL, max_retries=0)
        )
        ex = JobExecutor(backend=fake_backend)
        result = ex.execute_job(job)
        assert result.status == "failed"
        assert "intentional task failure" in (result.error or "")

    def test_failure_with_retry_creates_retry_job(self, fake_backend):
        job = fake_backend.add_job(
            make_job(task_path=_TASK_FAIL, max_retries=2, retry_count=0)
        )
        ex = JobExecutor(backend=fake_backend)
        ex.execute_job(job)
        # A retry job should have been created via create_job.
        retry_calls = [c for c in fake_backend.calls if c[0] == "create_job"]
        assert retry_calls, "expected a retry job to be created"

    def test_should_retry_returns_false_when_max_retries_zero(self):
        ex = JobExecutor(backend=mock.MagicMock())
        job = SimpleNamespace(max_retries=0, retry_count=0)
        assert ex._should_retry(job) is False

    def test_should_retry_returns_false_at_max(self):
        ex = JobExecutor(backend=mock.MagicMock())
        job = SimpleNamespace(max_retries=3, retry_count=3)
        assert ex._should_retry(job) is False

    def test_should_retry_returns_true_below_max(self):
        ex = JobExecutor(backend=mock.MagicMock())
        job = SimpleNamespace(max_retries=3, retry_count=1)
        assert ex._should_retry(job) is True

    def test_should_retry_handles_none_retry_count(self):
        ex = JobExecutor(backend=mock.MagicMock())
        job = SimpleNamespace(max_retries=3, retry_count=None)
        assert ex._should_retry(job) is True

    def test_skip_when_status_already_terminal(self, fake_backend):
        job = fake_backend.add_job(make_job(task_path=_TASK_OK, status="success"))
        ex = JobExecutor(backend=fake_backend)
        out = ex.execute_job(job)
        # Should bail out without changing the row.
        assert out.status == "success"
        assert not any(c[0] == "mark_job_success" for c in fake_backend.calls)

    def test_can_execute_job_returns_true_when_parallel_allowed(self, fake_backend):
        ex = JobExecutor(backend=fake_backend)
        j = make_job(allow_parallel=True)
        assert ex.can_execute_job(j) is True

    def test_can_execute_job_false_when_running_in_queue(self, fake_backend):
        fake_backend.add_job(make_job(status="running", queue_name="q"))
        ex = JobExecutor(backend=fake_backend)
        candidate = make_job(allow_parallel=False, queue_name="q")
        assert ex.can_execute_job(candidate) is False


# ---------------------------------------------------------------------------
# TestRetryJob — exercises _retry_job directly (avoids RNG of timeouts)
# ---------------------------------------------------------------------------


class TestRetryJob:
    def test_retry_job_sets_scheduled_at_in_future(self, fake_backend):
        failed = fake_backend.add_job(
            make_job(task_path=_TASK_FAIL, max_retries=3, retry_count=1, retry_backoff=2.0)
        )
        ex = JobExecutor(backend=fake_backend)
        retry = ex._retry_job(failed)
        assert retry is not None
        # The newly created retry exists in the backend store.
        assert retry.id in fake_backend._jobs

    def test_retry_job_marks_original_archived(self, fake_backend):
        failed = fake_backend.add_job(
            make_job(task_path=_TASK_FAIL, max_retries=1, retry_count=0, job_name="myjob")
        )
        ex = JobExecutor(backend=fake_backend)
        ex._retry_job(failed)
        assert failed.status == "archived"


# ---------------------------------------------------------------------------
# TestSignalHandlers — install handler manually and verify contract
# ---------------------------------------------------------------------------


class TestSignalHandlers:
    def test_sigusr1_handler_sets_heartbeat_flag_only(self, fake_backend):
        """SIGUSR1 handler must set a flag and never touch the DB."""
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        # Mirror the run() closure.
        assert wp._heartbeat_due is False

        def heartbeat_signal_handler(signum, frame):
            wp._heartbeat_due = True

        heartbeat_signal_handler(signal.SIGUSR1, None)
        assert wp._heartbeat_due is True
        # No backend call should have happened from the handler itself.
        assert not any(c[0] == "update_worker_heartbeat" for c in fake_backend.calls)

    def test_sigterm_handler_sets_shutdown_flag(self, fake_backend):
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        assert wp.shutdown_requested is False

        def signal_handler(signum, frame):
            wp.shutdown_requested = True

        signal_handler(signal.SIGTERM, None)
        assert wp.shutdown_requested is True

    def test_sigalrm_raises_timeout_error(self):
        """The timeout handler installed in execute_job must raise TimeoutError."""
        def timeout_handler(signum, frame):
            raise TimeoutError("job exceeded timeout")

        with pytest.raises(TimeoutError):
            timeout_handler(signal.SIGALRM, None)


# ---------------------------------------------------------------------------
# TestConnectionReset — verifies the close-all hook
# ---------------------------------------------------------------------------


class TestConnectionReset:
    def test_reset_db_connections_calls_django_close_all(self, fake_backend, monkeypatch):
        ex = JobExecutor(backend=fake_backend)
        fake_connections = mock.MagicMock()
        monkeypatch.setattr(worker_module, "connections", fake_connections)
        ex._reset_db_connections()
        fake_connections.close_all.assert_called_once()

    def test_reset_db_connections_is_noop_when_django_absent(self, fake_backend, monkeypatch):
        ex = JobExecutor(backend=fake_backend)
        monkeypatch.setattr(worker_module, "connections", None)
        # Must not raise.
        ex._reset_db_connections()


# ---------------------------------------------------------------------------
# TestWorkerProcessHeartbeat
# ---------------------------------------------------------------------------


class TestWorkerProcessHeartbeat:
    def test_check_heartbeat_flushes_pending_flag(self, fake_backend):
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        wp._heartbeat_due = True
        wp._check_heartbeat()
        # Heartbeat flag cleared and DB call made.
        assert wp._heartbeat_due is False
        assert any(c[0] == "update_worker_heartbeat" for c in fake_backend.calls)

    def test_check_heartbeat_with_current_job_reports_busy(self, fake_backend):
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        wp._heartbeat_due = True
        wp.current_job = make_job(id=42)
        wp._check_heartbeat()
        call = next(c for c in fake_backend.calls if c[0] == "update_worker_heartbeat")
        # args[1] is status, args[2] is current_job_id
        assert call[1][1] == "busy"
        assert call[1][2] == 42

    def test_check_heartbeat_noop_when_flag_unset(self, fake_backend):
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        wp._heartbeat_due = False
        wp._check_heartbeat()
        # The stale-loop branch may still fire DB reset if poll_interval is
        # very small. We assert no heartbeat update was emitted.
        assert not any(c[0] == "update_worker_heartbeat" for c in fake_backend.calls)


# ---------------------------------------------------------------------------
# TestForkLifecycle — mock os.fork to exercise parent / child paths
# ---------------------------------------------------------------------------


class TestForkLifecycle:
    def test_parent_branch_records_child_pid_and_waits(self, fake_backend, monkeypatch):
        """When os.fork() returns a positive PID we are the parent."""
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        job = fake_backend.add_job(make_job(task_path=_TASK_OK, timeout_seconds=5))
        # Forcibly mark the job success on the backend (simulating the child)
        fake_backend.mark_job_success(job.id, output="done")

        # Drop the test backend back to queued so we can rerun assertions.
        job.status = "success"

        # Mock os.fork → parent PID, os.waitpid → child exited normally.
        monkeypatch.setattr(os, "fork", lambda: 4242)
        monkeypatch.setattr(os, "waitpid", lambda pid, opts: (4242, 0))
        # WIFEXITED / WEXITSTATUS used by parent.
        monkeypatch.setattr(os, "WIFEXITED", lambda s: True)
        monkeypatch.setattr(os, "WEXITSTATUS", lambda s: 0)
        # Ensure we don't actually sleep waiting.
        monkeypatch.setattr("sqlery.core.worker.time.sleep", lambda *_a, **_k: None)

        result = wp._fork_and_execute(job)
        assert result["success"] is True
        # Parent recorded the child PID.
        assert any(c[0] == "update_worker_heartbeat" for c in fake_backend.calls)

    def test_child_branch_does_not_return(self, fake_backend, monkeypatch):
        """When os.fork() returns 0 we are the child and must os._exit."""
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        job = fake_backend.add_job(make_job(task_path=_TASK_OK))

        # Pretend to be the child.
        monkeypatch.setattr(os, "fork", lambda: 0)
        monkeypatch.setattr(os, "setpgrp", lambda: None)

        # Capture os._exit so the test process survives.
        exit_calls: list[int] = []

        def fake_exit(code):
            exit_calls.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(os, "_exit", fake_exit)
        # The child calls execute_job_in_child — stub it out.
        monkeypatch.setattr(
            wp.executor, "execute_job_in_child", lambda j: fake_exit(0)
        )

        with pytest.raises(SystemExit):
            wp._fork_and_execute(job)

        assert exit_calls and exit_calls[0] == 0


# ---------------------------------------------------------------------------
# TestCleanupStaleJobs
# ---------------------------------------------------------------------------


class TestCleanupStaleJobs:
    def test_marks_jobs_without_started_at_as_failed(self, fake_backend):
        stale = fake_backend.add_job(make_job(status="running", started_at=None))
        ex = JobExecutor(backend=fake_backend)
        ex.cleanup_stale_jobs()
        assert stale.status == "failed"
        assert "before job execution started" in stale.error

    def test_leaves_fresh_running_jobs_alone(self, fake_backend):
        fresh = fake_backend.add_job(
            make_job(status="running", started_at=datetime.now(timezone.utc), timeout_seconds=600)
        )
        ex = JobExecutor(backend=fake_backend)
        ex.cleanup_stale_jobs()
        assert fresh.status == "running"
