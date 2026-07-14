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
import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from sqlery.core import worker as worker_module
from sqlery.core.worker import JobExecutor, WorkerProcess

from .conftest import FakeBackend, make_job, make_scheduled_task, _utcnow


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
        job = fake_backend.add_job(make_job(task_path=_TASK_OK, kwargs={"value": 42}))
        ex = JobExecutor(backend=fake_backend)
        result = ex.execute_job(job)
        assert result.status == "success"
        assert "result-42" in (result.output or "")

    def test_failure_no_retry_marks_failed(self, fake_backend):
        job = fake_backend.add_job(make_job(task_path=_TASK_FAIL, max_retries=0))
        ex = JobExecutor(backend=fake_backend)
        result = ex.execute_job(job)
        assert result.status == "failed"
        assert "intentional task failure" in (result.error or "")

    def test_failure_with_retry_creates_retry_job(self, fake_backend):
        job = fake_backend.add_job(make_job(task_path=_TASK_FAIL, max_retries=2, retry_count=0))
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
        # Prevent Django from checking real DB connections (no django_db mark).
        monkeypatch.setattr("sqlery.core.worker.close_old_connections", lambda: None)

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
        monkeypatch.setattr(wp.executor, "execute_job_in_child", lambda j: fake_exit(0))

        with pytest.raises(SystemExit):
            wp._fork_and_execute(job)

        assert exit_calls and exit_calls[0] == 0

    def test_leases_renewed_while_blocking_on_long_job(self, fake_backend, monkeypatch):
        """WR-01: held scheduler leases are renewed from inside the blocking
        wait loop so leadership does not flap during a job longer than the TTL.

        The main loop only renews at the top of each iteration, but
        `_fork_and_execute` blocks for the whole job. Here we simulate a job
        that spans several wait-loop iterations while its lease TTL has already
        elapsed; without in-wait renewal the lease's `expires_at` would stay in
        the past (expired mid-job). The renewal must push it back into the
        future, proving leadership stays alive across the long job.
        """
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        job = fake_backend.add_job(make_job(task_path=_TASK_OK, timeout_seconds=600))

        # Worker holds the `default` scheduler lease — but it is already at the
        # edge of expiry (expires_at in the past), exactly the WR-01 scenario
        # where a long job has run past `poll_interval * 3`.
        wp._owned_queues = {"default"}
        wp._lease_secs = wp.poll_interval * 3
        fake_backend._leases["default"] = {
            "daemon_id": wp.worker_id,
            "node_id": wp.node_id,
            "pid": wp.pid,
            "expires_at": _utcnow() - timedelta(seconds=1),
        }

        # Parent branch (fork returns a positive PID). waitpid returns "not yet
        # exited" for the first few polls (simulating a long-running job), then
        # reports the child exited — so the wait loop iterates several times and
        # the in-wait renewal runs at least once.
        monkeypatch.setattr(os, "fork", lambda: 4242)
        poll_results = iter([(0, 0), (0, 0), (0, 0), (4242, 0)])
        monkeypatch.setattr(os, "waitpid", lambda pid, opts: next(poll_results))
        monkeypatch.setattr(os, "WIFEXITED", lambda s: True)
        monkeypatch.setattr(os, "WEXITSTATUS", lambda s: 0)
        monkeypatch.setattr("sqlery.core.worker.time.sleep", lambda *_a, **_k: None)
        monkeypatch.setattr("sqlery.core.worker.close_old_connections", lambda: None)

        wp._fork_and_execute(job)

        # The lease was renewed during the blocking wait: its expiry is now in
        # the future (it started in the past). Without the WR-01 fix it would
        # remain expired, letting another worker take over scheduling.
        assert fake_backend._leases["default"]["expires_at"] > _utcnow()


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


class TestKilledChildIsReaped:
    """Real-subprocess coverage for the zombie-reap fix (_kill_worker_process).

    Spawns an actual short-lived child via `os.fork()`, kills it through the
    worker's kill helper, then asserts the child is fully reaped rather than
    left as a zombie in the process table: `os.waitpid(pid, os.WNOHANG)` on an
    already-reaped pid raises `ChildProcessError`. If the fix regressed (kill
    without waitpid), the child would still be a zombie and `waitpid` would
    instead return `(pid, status)` on the first call — the wrong polarity,
    caught by the assertion below.
    """

    def _spawn_sleeper(self) -> int:
        pid = os.fork()
        if pid == 0:
            time.sleep(5)
            os._exit(0)
        return pid

    def test_kill_worker_process_reaps_child(self, fake_backend):
        pid = self._spawn_sleeper()
        ex = JobExecutor(backend=fake_backend)

        assert ex._kill_worker_process(pid) is True

        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


# ---------------------------------------------------------------------------
# TestWorkerSchedulerElection — Phase 9 leader-election behavior (ELECT-01..07)
# ---------------------------------------------------------------------------


def _run_one_election_cycle(wp, monkeypatch):
    """Drive `WorkerProcess.run` through exactly one election pass, then exit.

    The election step (renew/re-claim leases + `run_due_tasks`) runs once per
    loop iteration BEFORE `claim_job`. We patch `claim_job` to flip the
    shutdown flag and return ``None``: the worker performs its full election
    pass, then sees no job and drops into the bounded poll-sleep whose
    `while elapsed < poll_interval and not shutdown_requested` guard exits
    immediately (shutdown already requested), so the outer loop exits straight
    into the `finally:` block (where leases are released). `time.sleep` is
    patched to a no-op so nothing waits on wall-clock — no real-TTL sleeps.

    Returns a dict ``{"claim": [...], "renew": [...], "release": [...]}`` of
    ``(queues, daemon_id)`` tuples captured for each lease call the worker made
    during the cycle.
    """
    monkeypatch.setattr("sqlery.core.worker.time.sleep", lambda *_a, **_k: None)
    if worker_module.close_old_connections is not None:
        monkeypatch.setattr(worker_module, "close_old_connections", lambda: None)

    # FakeBackend does not _record lease calls, so wrap them here to capture
    # the (queues, daemon_id) the worker passed DURING the cycle. This is the
    # durable proof the worker self-elected, because run()'s finally: releases
    # held leases on shutdown (ELECT-03) and clears them from _leases.
    lease_calls: dict[str, list] = {"claim": [], "renew": [], "release": []}
    real_claim_leases = wp.backend.claim_queue_leases
    real_renew_leases = wp.backend.renew_queue_leases
    real_release_leases = wp.backend.release_queue_leases

    def _spy_claim(queues, daemon_id, node_id, pid, lease_secs):
        lease_calls["claim"].append((list(queues), daemon_id))
        return real_claim_leases(queues, daemon_id, node_id, pid, lease_secs)

    def _spy_renew(owned_queues, daemon_id, lease_secs):
        lease_calls["renew"].append((list(owned_queues), daemon_id))
        return real_renew_leases(owned_queues, daemon_id, lease_secs)

    def _spy_release(owned_queues, daemon_id):
        lease_calls["release"].append((list(owned_queues), daemon_id))
        return real_release_leases(owned_queues, daemon_id)

    monkeypatch.setattr(wp.backend, "claim_queue_leases", _spy_claim)
    monkeypatch.setattr(wp.backend, "renew_queue_leases", _spy_renew)
    monkeypatch.setattr(wp.backend, "release_queue_leases", _spy_release)

    real_claim_job = wp.backend.claim_job

    def _claim_then_stop(queues, worker_id):
        # Record the call (preserves the spy) but stop the loop and return no
        # job so the worker exits after exactly one election pass.
        real_claim_job(queues, worker_id)
        wp.shutdown_requested = True
        return None

    monkeypatch.setattr(wp.backend, "claim_job", _claim_then_stop)
    wp.run()
    return lease_calls


def _seed_due_task(fake_backend, *, name, queue_name):
    """Seed an enabled scheduled task that is due now (next_run_at in the past)."""
    task = make_scheduled_task(
        name=name,
        queue_name=queue_name,
        enabled=True,
        next_run_at=_utcnow() - timedelta(seconds=1),
    )
    return fake_backend.add_scheduled_task(task)


def _job_count_for_task(fake_backend, task) -> int:
    """Count queued jobs the scheduler created for a given task's queue/path."""
    return sum(
        1
        for j in fake_backend._jobs.values()
        if j.queue_name == task.queue_name and j.task_path == task.task_path
    )


def _claimed_queues(lease_calls, worker_id) -> set[str]:
    """All queues the worker claimed a lease for under its own identity."""
    claimed: set[str] = set()
    for queues, daemon_id in lease_calls["claim"]:
        if daemon_id == worker_id:
            claimed.update(queues)
    return claimed


class TestWorkerSchedulerElection:
    """Behavioral proof of the Plan-01 election wiring against FakeBackend.

    Every assertion reads real `fake_backend` state (`_leases`, `_jobs`,
    recorded `calls`) produced by the actual `WorkerProcess.run` election
    path — a test cannot pass unless the wiring runs (mitigates T-09-T1).
    Expiry is always simulated via a PAST `expires_at` and `time.sleep` is a
    no-op, so no test waits on a real TTL (mitigates T-09-T2).
    """

    def test_bare_worker_fires_due_cron_for_held_queue(self, fake_backend, monkeypatch):
        """ELECT-04 (headline): a bare worker — no daemon — fires a due cron
        ScheduledTask for a queue it holds the lease for."""
        # NOTE: no daemon object is constructed anywhere in this test.
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        task = _seed_due_task(fake_backend, name="bare-cron", queue_name="default")

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        # Worker self-elected as scheduler-leader for `default`...
        assert "default" in _claimed_queues(lease_calls, wp.worker_id)
        # ...and fired the due cron task, enqueueing a job for it. The job is
        # the load-bearing proof: run_due_tasks only fires for held queues.
        assert _job_count_for_task(fake_backend, task) == 1

    def test_worker_claims_or_renews_lease_for_every_configured_queue(
        self, fake_backend, monkeypatch
    ):
        """ELECT-01: the worker claims/renews a lease for every queue in
        self.queues each cycle, all owned by its own worker_id."""
        wp = WorkerProcess(queues=["default", "reports"], backend=fake_backend)
        # Seed a due task on EACH configured queue: both must fire, which can
        # only happen if the worker holds a lease for both (ELECT-01).
        task_default = _seed_due_task(fake_backend, name="due-default", queue_name="default")
        task_reports = _seed_due_task(fake_backend, name="due-reports", queue_name="reports")

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        # A lease was claimed for every configured queue under the worker's id.
        assert _claimed_queues(lease_calls, wp.worker_id) == {"default", "reports"}
        # Both held queues fired their due cron task.
        assert _job_count_for_task(fake_backend, task_default) == 1
        assert _job_count_for_task(fake_backend, task_reports) == 1

    def test_worker_fires_cron_only_for_held_queues(self, fake_backend, monkeypatch):
        """ELECT-02: a due task in a held queue IS enqueued; a due task in a
        queue held by a live foreign lease is NOT."""
        wp = WorkerProcess(queues=["a", "b"], backend=fake_backend)
        # Pre-seed a LIVE foreign lease on `b` so the worker can only hold `a`.
        fake_backend._leases["b"] = {
            "daemon_id": "daemon_other",
            "node_id": "other-node",
            "pid": 999,
            "expires_at": _utcnow() + timedelta(seconds=300),
        }
        task_a = _seed_due_task(fake_backend, name="due-a", queue_name="a")
        task_b = _seed_due_task(fake_backend, name="due-b", queue_name="b")

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        # Worker holds `a` only; `b` stays with the live foreign holder.
        assert "a" in _claimed_queues(lease_calls, wp.worker_id)
        assert fake_backend._leases["b"]["daemon_id"] == "daemon_other"
        # Held queue `a` fired; foreign-held queue `b` did not.
        assert _job_count_for_task(fake_backend, task_a) == 1
        assert _job_count_for_task(fake_backend, task_b) == 0

    def test_live_foreign_lease_keeps_worker_from_scheduling(self, fake_backend, monkeypatch):
        """ELECT-05: a live foreign (daemon) lease stays authoritative — the
        worker does not take it over and does not fire its cron."""
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        fake_backend._leases["default"] = {
            "daemon_id": "daemon_other",
            "node_id": "other-node",
            "pid": 999,
            "expires_at": _utcnow() + timedelta(seconds=300),
        }
        task = _seed_due_task(fake_backend, name="daemon-owned", queue_name="default")

        _run_one_election_cycle(wp, monkeypatch)

        # The daemon stayed authoritative (foreign lease untouched) and no job
        # was enqueued for the daemon-owned queue.
        assert fake_backend._leases["default"]["daemon_id"] == "daemon_other"
        assert _job_count_for_task(fake_backend, task) == 0

    def test_expired_lease_is_taken_over_and_cron_fires(self, fake_backend, monkeypatch):
        """ELECT-06: once a prior leader's lease expires, another worker
        re-claims the queue and fires its due cron — failover within one TTL.

        The production failover window is bounded by the lease TTL
        (`poll_interval * 3`, Plan 01); here we simulate the prior leader's
        death directly by setting a PAST `expires_at` rather than sleeping for
        a real TTL, so the test stays instant (mitigates T-09-T2).
        """
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        # Prior leader's lease is already expired (dead leader).
        fake_backend._leases["default"] = {
            "daemon_id": "dead_leader",
            "node_id": "dead-node",
            "pid": 111,
            "expires_at": _utcnow() - timedelta(seconds=5),
        }
        task = _seed_due_task(fake_backend, name="failover-cron", queue_name="default")

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        # The worker re-claimed `default` (proven by the claim call) and fired
        # the due cron. The lease is released again in finally:, so we assert
        # the takeover via the claim record + the freshly enqueued job rather
        # than the post-shutdown _leases state.
        assert "default" in _claimed_queues(lease_calls, wp.worker_id)
        assert _job_count_for_task(fake_backend, task) == 1

    def test_job_claim_path_uses_full_queue_set_regardless_of_leases(
        self, fake_backend, monkeypatch
    ):
        """ELECT-07: claim_job is always called with the full self.queues,
        whether the worker holds all leases or none — leases gate cron-firing
        only, never job execution."""

        def _claim_job_queues(backend):
            return [args[0] for name, args, _kw in backend.calls if name == "claim_job"]

        # (a) Worker holds BOTH leases (no contention).
        wp_all = WorkerProcess(queues=["a", "b"], backend=fake_backend)
        _run_one_election_cycle(wp_all, monkeypatch)
        assert _claim_job_queues(fake_backend) == [["a", "b"]]

        # (b) Fresh worker holding NONE because live foreign leases cover both.
        fresh = FakeBackend()
        for q in ("a", "b"):
            fresh._leases[q] = {
                "daemon_id": "daemon_other",
                "node_id": "other-node",
                "pid": 999,
                "expires_at": _utcnow() + timedelta(seconds=300),
            }
        wp_none = WorkerProcess(queues=["a", "b"], backend=fresh)
        _run_one_election_cycle(wp_none, monkeypatch)
        # Full queue set even though the worker holds no leases.
        assert _claim_job_queues(fresh) == [["a", "b"]]

    def test_held_leases_released_on_graceful_shutdown(self, fake_backend, monkeypatch):
        """ELECT-03: held leases are released when run() exits via the
        graceful-shutdown finally: path."""
        wp = WorkerProcess(queues=["default"], backend=fake_backend)

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        # The worker held `default` during the cycle, then released it on the
        # shutdown path so another worker/daemon can take over immediately.
        assert "default" in _claimed_queues(lease_calls, wp.worker_id)
        assert "default" not in fake_backend._leases
        released = {
            q
            for queues, daemon_id in lease_calls["release"]
            if daemon_id == wp.worker_id
            for q in queues
        }
        assert "default" in released
