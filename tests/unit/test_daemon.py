"""Focused unit tests for `sqlery.core.daemon` (TEST-07).

The :class:`DaemonManager` is a large class that mixes file-based PID
handling with DB-backed lease management. This module isolates the
testable units:

* PID file lifecycle (``read_pid`` / ``write_pid`` / ``remove_pid``)
* Process liveness probe (``is_process_running`` / ``is_running``)
* ``status()`` aggregation
* DB-backed lease acquire / renew / expire (via the FakeBackend's
  in-memory ``_leases`` dict)
* Zombie-job detection sequence skips when QueuedJob model is absent
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
# TestZombieDetection — _fail_zombie_running_jobs is a no-op without Django.
# ---------------------------------------------------------------------------


class TestZombieDetection:
    def test_fail_zombie_no_op_when_queued_job_absent(self, fake_backend, monkeypatch):
        """Without the Django QueuedJob model the routine returns silently."""
        monkeypatch.setattr(daemon_module, "QueuedJob", None)
        # Should not raise.
        DaemonManager._fail_zombie_running_jobs(fake_backend, queue_names=["default"])


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
