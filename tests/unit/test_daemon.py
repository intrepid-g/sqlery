"""Focused unit tests for `sqlery.core.daemon` (TEST-07).

The :class:`DaemonManager` is a large class that mixes file-based PID
handling with DB-backed lease management. This module isolates the
testable units:

* PID file lifecycle (``read_pid`` / ``write_pid`` / ``remove_pid``)
* Process liveness probe (``is_process_running`` / ``is_running``)
* ``status()`` aggregation
* DB-backed lease acquire / renew / expire (via the FakeBackend's
  in-memory ``_leases`` dict)
* Zombie-job detection sequence (mode-agnostic, over RunningJobLiveness)
* Stop / restart / cleanup-stale plumbing (mocked ``os.kill``)
"""

from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from sqlery.core import daemon as daemon_module
from sqlery.core.daemon import DaemonManager, _should_run_cleanup

from .conftest import make_worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def dm(tmp_path) -> DaemonManager:
    return DaemonManager(pid_dir=tmp_path)


# ---------------------------------------------------------------------------
# TestPidLifecycle
# ---------------------------------------------------------------------------


class TestPidLifecycle:
    def test_read_pid_returns_none_when_missing(self, dm):
        assert dm.read_pid() is None

    def test_write_then_read_roundtrip(self, dm):
        dm.write_pid(9999)
        assert dm.read_pid() == 9999

    def test_read_pid_returns_none_on_invalid_file(self, dm):
        dm.pid_file.write_text("not-a-number")
        assert dm.read_pid() is None

    def test_remove_pid_is_idempotent(self, dm):
        dm.write_pid(1)
        dm.remove_pid()
        dm.remove_pid()  # second call must not raise
        assert dm.read_pid() is None


# ---------------------------------------------------------------------------
# TestProcessLiveness
# ---------------------------------------------------------------------------


class TestProcessLiveness:
    def test_is_process_running_true_when_kill_zero_succeeds(self, dm, monkeypatch):
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        assert dm.is_process_running(123) is True

    def test_is_process_running_false_when_kill_raises(self, dm, monkeypatch):
        def boom(pid, sig):
            raise OSError("no such process")

        monkeypatch.setattr(os, "kill", boom)
        assert dm.is_process_running(123) is False

    def test_is_running_false_when_no_pid_file(self, dm):
        assert dm.is_running() is False

    def test_is_running_true_when_pid_alive(self, dm, monkeypatch):
        dm.write_pid(os.getpid())
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        assert dm.is_running() is True


# ---------------------------------------------------------------------------
# TestStatusAggregation
# ---------------------------------------------------------------------------


class TestStatusAggregation:
    def test_status_reports_not_running_when_no_pid(self, dm, fake_backend, monkeypatch):
        # Replace get_backend with one that returns fake_backend.
        monkeypatch.setattr(daemon_module, "get_backend", lambda: fake_backend)
        st = dm.status()
        assert st["running"] is False
        assert st["pid"] is None
        assert st["worker_count"] == 0

    def test_status_reports_worker_count_when_running(self, dm, fake_backend, monkeypatch):
        dm.write_pid(os.getpid())
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(daemon_module, "get_backend", lambda: fake_backend)
        fake_backend.add_worker(make_worker(worker_id="w1"))
        fake_backend.add_worker(make_worker(worker_id="w2"))
        st = dm.status()
        assert st["running"] is True
        assert st["worker_count"] == 2

    def test_status_flags_stale_heartbeat(self, dm):
        dm.heartbeat_file.write_text(str(int(time.time()) - 1000))
        st = dm.status()
        assert st["stale"] is True
        assert st["heartbeat_age"] is not None and st["heartbeat_age"] > 300


# ---------------------------------------------------------------------------
# TestShouldRunCleanup
# ---------------------------------------------------------------------------


class TestShouldRunCleanup:
    def test_true_when_never_run(self):
        assert _should_run_cleanup(None) is True

    def test_true_when_interval_elapsed(self):
        past = datetime.now(timezone.utc) - timedelta(hours=10)
        assert _should_run_cleanup(past, interval_hours=6) is True

    def test_false_when_within_interval(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _should_run_cleanup(recent, interval_hours=6) is False


# ---------------------------------------------------------------------------
# TestLeaseLifecycle — exercised against FakeBackend's _leases dict
# ---------------------------------------------------------------------------


class TestLeaseLifecycle:
    def test_acquire_when_lease_free(self, fake_backend):
        owned = fake_backend.claim_queue_leases(
            queues=["q1", "q2"], daemon_id="d1", node_id="n1", pid=1, lease_secs=30
        )
        assert set(owned) == {"q1", "q2"}

    def test_acquire_skips_live_lease_held_by_other_daemon(self, fake_backend):
        fake_backend.claim_queue_leases(
            queues=["q1"], daemon_id="other", node_id="n1", pid=1, lease_secs=300
        )
        owned = fake_backend.claim_queue_leases(
            queues=["q1"], daemon_id="self", node_id="n2", pid=2, lease_secs=30
        )
        assert owned == []

    def test_renew_extends_expiry(self, fake_backend):
        fake_backend.claim_queue_leases(
            queues=["q1"], daemon_id="d1", node_id="n1", pid=1, lease_secs=10
        )
        original = fake_backend._leases["q1"]["expires_at"]
        time.sleep(0.001)
        fake_backend.renew_queue_leases(["q1"], "d1", lease_secs=60)
        assert fake_backend._leases["q1"]["expires_at"] > original

    def test_release_deletes_owned_leases(self, fake_backend):
        fake_backend.claim_queue_leases(
            queues=["q1", "q2"], daemon_id="d1", node_id="n1", pid=1, lease_secs=30
        )
        fake_backend.release_queue_leases(["q1", "q2"], "d1")
        assert "q1" not in fake_backend._leases
        assert "q2" not in fake_backend._leases

    def test_expired_lease_can_be_reclaimed(self, fake_backend):
        # Seed an already-expired lease held by another daemon.
        fake_backend._leases["q1"] = {
            "daemon_id": "other",
            "node_id": "n1",
            "pid": 1,
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=10),
        }
        owned = fake_backend.claim_queue_leases(
            queues=["q1"], daemon_id="self", node_id="n2", pid=2, lease_secs=30
        )
        assert owned == ["q1"]
        assert fake_backend._leases["q1"]["daemon_id"] == "self"


# ---------------------------------------------------------------------------
# TestZombieDetection — mode-agnostic _fail_zombie_running_jobs over
# backend-supplied RunningJobLiveness records (works for Django + standalone).
# ---------------------------------------------------------------------------


def socket_hostname():
    import socket

    return os.environ.get("NODE_ID", socket.gethostname())


def _liveness(**overrides):
    """Build a healthy RunningJobLiveness record, overridable per-check."""
    from sqlery.core.liveness import RunningJobLiveness

    now = datetime.now(timezone.utc)
    base = dict(
        job_id=101,
        started_at=now,
        worker_pid=os.getpid(),  # alive PID on this node
        worker_node_id=socket_hostname(),
        worker_status="busy",
        worker_current_job_id=101,  # points at this job → healthy
        worker_last_heartbeat=now,
        worker_friendly_name="wise-builder-1a",
        has_worker=True,
    )
    base.update(overrides)
    return RunningJobLiveness(**base)


class TestZombieDetection:
    def _run(self, fake_backend, records):
        fake_backend.liveness_records = records
        DaemonManager._fail_zombie_running_jobs(fake_backend, queue_names=["default"])
        return [c for c in fake_backend.calls if c[0] == "fail_zombie_job"]

    def test_no_op_when_no_running_jobs(self, fake_backend):
        """Empty sweep (e.g. standalone with no records) does nothing."""
        fake_backend.liveness_records = []
        DaemonManager._fail_zombie_running_jobs(fake_backend, queue_names=["default"])
        assert [c for c in fake_backend.calls if c[0] == "fail_zombie_job"] == []

    def test_healthy_worker_not_failed(self, fake_backend):
        calls = self._run(fake_backend, [_liveness()])
        assert calls == [], "healthy running job must not be failed"

    def test_check1_pid_gone(self, fake_backend):
        rec = _liveness(worker_pid=999_999)  # PID almost certainly absent
        calls = self._run(fake_backend, [rec])
        assert len(calls) == 1
        assert calls[0][1][0] == rec.job_id
        assert "no longer exists" in calls[0][1][1]

    def test_check2_no_worker(self, fake_backend):
        rec = _liveness(
            has_worker=False,
            worker_pid=None,
            worker_node_id=None,
            worker_status=None,
            worker_current_job_id=None,
            worker_last_heartbeat=None,
            worker_friendly_name=None,
        )
        calls = self._run(fake_backend, [rec])
        assert len(calls) == 1
        assert calls[0][1][1] == "Running job has no worker assigned"

    def test_check3_worker_dead(self, fake_backend):
        rec = _liveness(worker_pid=None, worker_status="dead")
        calls = self._run(fake_backend, [rec])
        assert len(calls) == 1
        assert "is dead" in calls[0][1][1]

    def test_check4_worker_moved_on(self, fake_backend):
        rec = _liveness(worker_pid=None, worker_current_job_id=999)
        calls = self._run(fake_backend, [rec])
        assert len(calls) == 1
        assert "moved on to job #999" in calls[0][1][1]

    def test_check4_worker_idle_past_grace(self, fake_backend):
        old = datetime.now(timezone.utc) - timedelta(seconds=300)
        rec = _liveness(worker_pid=None, worker_current_job_id=None, started_at=old)
        calls = self._run(fake_backend, [rec])
        assert len(calls) == 1
        assert "is idle but job has been running" in calls[0][1][1]

    def test_check5_heartbeat_stale(self, fake_backend):
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        rec = _liveness(worker_pid=None, worker_last_heartbeat=stale)
        calls = self._run(fake_backend, [rec])
        assert len(calls) == 1
        assert "heartbeat stale" in calls[0][1][1]


# ---------------------------------------------------------------------------
# TestStop — graceful shutdown forwards signals
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_returns_false_when_no_pid_file(self, dm):
        assert dm.stop() is False

    def test_stop_returns_false_when_process_already_gone(self, dm, monkeypatch):
        dm.write_pid(99999)
        # kill(0) raises → not running.
        monkeypatch.setattr(os, "kill", mock.MagicMock(side_effect=OSError("gone")))
        assert dm.stop() is False

    def test_stop_sends_sigterm_then_returns_true(self, dm, monkeypatch):
        dm.write_pid(99999)
        calls: list[tuple[int, int]] = []

        # First kill(0) probe → alive; first SIGTERM → ok; subsequent kill(0)
        # probes → dead (so we exit early).
        kill_counter = {"n": 0}

        def fake_kill(pid, sig):
            kill_counter["n"] += 1
            # The first call is is_process_running(pid) before signaling.
            # Allow that and the SIGTERM, then start raising to simulate exit.
            if kill_counter["n"] <= 2:
                if sig != 0:
                    calls.append((pid, sig))
                return
            raise OSError("dead")

        monkeypatch.setattr(os, "kill", fake_kill)
        monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
        result = dm.stop()
        assert result is True
        assert calls and calls[0] == (99999, signal.SIGTERM)


# ---------------------------------------------------------------------------
# TestCleanupStale
# ---------------------------------------------------------------------------


class TestCleanupStale:
    def test_cleanup_stale_returns_true_when_no_pid_file(self, dm):
        assert dm.cleanup_stale() is True

    def test_cleanup_stale_returns_false_when_process_alive(self, dm, monkeypatch):
        dm.write_pid(os.getpid())
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        assert dm.cleanup_stale() is False

    def test_cleanup_stale_removes_files_when_process_dead(self, dm, monkeypatch):
        dm.write_pid(99999)
        dm.heartbeat_file.write_text("1")
        monkeypatch.setattr(os, "kill", mock.MagicMock(side_effect=OSError("dead")))
        assert dm.cleanup_stale() is True
        assert not dm.pid_file.exists()
        assert not dm.heartbeat_file.exists()


# ---------------------------------------------------------------------------
# TestNodeIdProperty
# ---------------------------------------------------------------------------


class TestNodeIdProperty:
    def test_node_id_returns_hostname(self, dm):
        nid = dm.node_id
        assert isinstance(nid, str) and nid


# ---------------------------------------------------------------------------
# TestPromoteDueScheduledJobs (14-02) — mock cursor, no live DB
# ---------------------------------------------------------------------------


class TestPromoteDueScheduledJobs:
    """Tests for promote_due_scheduled_jobs with mock cursor (D9).

    Uses a mock psycopg cursor so tests pass on SQLite without SKIP LOCKED.
    The advisory-lock try/finally pattern mirrors partitioning.py.
    """

    def _make_cursor(self, lock_acquired=True, staged_rows=None):
        """Build a mock psycopg cursor that mimics advisory lock + DELETE RETURNING."""
        from unittest.mock import MagicMock, call
        cursor = MagicMock()
        fetch_sequence = []
        # First fetchone: pg_try_advisory_lock result
        fetch_sequence.append((lock_acquired,))
        # fetchall: rows returned by DELETE ... FOR UPDATE SKIP LOCKED RETURNING
        cursor.fetchall.return_value = staged_rows or []
        # fetchone is consumed once for the advisory lock result
        cursor.fetchone.side_effect = iter(fetch_sequence)
        return cursor

    def test_returns_zero_when_advisory_lock_not_acquired(self):
        """Test 1: pg_try_advisory_lock returns False → return 0 immediately."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs
        cursor = self._make_cursor(lock_acquired=False)
        result = promote_due_scheduled_jobs(cursor)
        assert result == 0
        # DELETE should NOT have been called
        for call_args in cursor.execute.call_args_list:
            sql = call_args[0][0] if call_args[0] else ""
            assert "DELETE" not in str(sql).upper(), "DELETE must not run without advisory lock"

    def test_promotes_rows_with_lock_acquired(self):
        """Test 2: lock acquired → DELETE RETURNING + INSERT for each row → returns count."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs
        from datetime import datetime, timezone
        import uuid
        now = datetime.now(timezone.utc)
        fake_rows = [
            (1, "default", "myapp.tasks.hello", {"x": 1}, now, 0, 0, now),
        ]
        cursor = self._make_cursor(lock_acquired=True, staged_rows=fake_rows)
        result = promote_due_scheduled_jobs(cursor)
        assert result == 1
        # Verify advisory unlock was called in finally
        execute_sqls = [str(c[0][0]).upper() for c in cursor.execute.call_args_list if c[0]]
        assert any("ADVISORY_UNLOCK" in s for s in execute_sqls), (
            "pg_advisory_unlock must be called in finally block"
        )

    def test_releases_lock_in_finally_even_on_insert_error(self):
        """Test 3: advisory lock released in finally block even if INSERT raises."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs
        from datetime import datetime, timezone
        from unittest.mock import MagicMock, patch
        now = datetime.now(timezone.utc)
        fake_rows = [
            (1, "default", "myapp.tasks.hello", {"x": 1}, now, 0, 0, now),
        ]
        cursor = MagicMock()
        cursor.fetchone.side_effect = iter([(True,)])
        cursor.fetchall.return_value = fake_rows
        # Make INSERT raise on second execute call
        call_count = {"n": 0}
        original_execute = cursor.execute
        def failing_execute(sql, params=None):
            call_count["n"] += 1
            sql_str = str(sql).upper()
            if call_count["n"] >= 3 and "INSERT" in sql_str:
                raise RuntimeError("simulated INSERT failure")
        cursor.execute.side_effect = failing_execute
        try:
            promote_due_scheduled_jobs(cursor)
        except RuntimeError:
            pass
        # Advisory unlock MUST still be called
        unlock_calls = [
            c for c in cursor.execute.call_args_list
            if c[0] and "ADVISORY_UNLOCK" in str(c[0][0]).upper()
        ]
        assert len(unlock_calls) >= 1, "pg_advisory_unlock must fire even after INSERT error"

    def test_returns_zero_when_no_rows_due(self):
        """Test 4: DELETE RETURNING returns empty set → return 0 (still unlock in finally)."""
        from sqlery.core.scheduler import promote_due_scheduled_jobs
        cursor = self._make_cursor(lock_acquired=True, staged_rows=[])
        result = promote_due_scheduled_jobs(cursor)
        assert result == 0
        execute_sqls = [str(c[0][0]).upper() for c in cursor.execute.call_args_list if c[0]]
        assert any("ADVISORY_UNLOCK" in s for s in execute_sqls), (
            "pg_advisory_unlock must be called even when no rows are promoted"
        )


# ---------------------------------------------------------------------------
# TestValidateStagingConfig (14-02)
# ---------------------------------------------------------------------------


class TestValidateStagingConfig:
    """Tests for _validate_staging_config (config invariant D1)."""

    def test_raises_when_retention_equals_threshold(self):
        """Test 5: retention_days == threshold_days raises ValueError."""
        from sqlery.core.daemon import _validate_staging_config
        with pytest.raises(ValueError, match="must be greater than"):
            _validate_staging_config(30, "30 days")

    def test_raises_when_retention_less_than_threshold(self):
        """Test 5b: retention_days < threshold_days also raises ValueError."""
        from sqlery.core.daemon import _validate_staging_config
        with pytest.raises(ValueError, match="must be greater than"):
            _validate_staging_config(30, "7 days")

    def test_passes_when_retention_greater_than_threshold(self):
        """Test 6: retention_days > threshold_days → no exception."""
        from sqlery.core.daemon import _validate_staging_config
        # Should not raise
        _validate_staging_config(1, "30 days")

    def test_passes_with_hour_unit(self):
        """Test 6b: retention in hours (e.g., '48 hours') > 1-day threshold → OK."""
        from sqlery.core.daemon import _validate_staging_config
        # 48 hours = 2 days > 1 day threshold
        _validate_staging_config(1, "48 hours")
