"""Tests for daemon recovery, deadline watchdog, and manual intervention.

Covers:
  - deadlines.py: write/clear/enforce/rebuild deadline files
  - intervention.py: diagnose_system_health, do_manual_intervention, do_manual_intervention_direct
  - api_views.py: api_manual_intervention (gate logic)
  - cleanup.py: failure_ttl=-1 means keep forever
"""

import json
import os
import signal
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from sqlery.django_sqlery.models import QueuedJob, Worker, DaemonCommand


# ── Helpers ────────────────────────────────────────────────────────

def _create_worker(status="idle", heartbeat_age_seconds=0, current_job=None, pid=None):
    """Create a Worker row with a specific heartbeat age."""
    import socket
    from uuid6 import uuid7
    w = Worker.objects.create(
        id=uuid7(),
        node_id=socket.gethostname(),
        pid=pid or os.getpid(),
        status=status,
        queues=["default"],
    )
    if heartbeat_age_seconds > 0:
        stale_time = timezone.now() - timedelta(seconds=heartbeat_age_seconds)
        Worker.objects.filter(id=w.id).update(last_heartbeat=stale_time)
        w.refresh_from_db()
    if current_job:
        w.current_job = current_job
        w.save(update_fields=["current_job"])
    return w


def _create_running_job(started_seconds_ago=10, timeout_seconds=None, worker=None):
    """Create a QueuedJob in running state."""
    job = QueuedJob.objects.create(
        task_path="tests.test_intervention.dummy_task",
        queue_name="default",
        status="running",
        started_at=timezone.now() - timedelta(seconds=started_seconds_ago),
        timeout_seconds=timeout_seconds,
        worker_pid=os.getpid(),
    )
    if worker:
        job.worker = worker
        job.save(update_fields=["worker"])
    return job


def dummy_task():
    pass


# ── Deadline file tests ────────────────────────────────────────────

@pytest.mark.django_db
class TestDeadlineFiles:
    """Test write_deadline / clear_deadline filesystem operations."""

    def setup_method(self):
        from sqlery.django_sqlery.deadlines import DEADLINE_DIR
        self.deadline_dir = DEADLINE_DIR
        # Clean up any leftover files
        if self.deadline_dir.exists():
            for f in self.deadline_dir.glob("worker-test-*"):
                f.unlink(missing_ok=True)

    def teardown_method(self):
        if self.deadline_dir.exists():
            for f in self.deadline_dir.glob("worker-test-*"):
                f.unlink(missing_ok=True)

    def test_write_and_clear_deadline(self):
        from sqlery.django_sqlery.deadlines import write_deadline, clear_deadline

        job = _create_running_job(timeout_seconds=3600)
        write_deadline("test-worker-1", job)

        path = self.deadline_dir / "worker-test-worker-1.json"
        assert path.exists()

        data = json.loads(path.read_text())
        assert data["job_id"] == job.id
        assert data["timeout_seconds"] == 3600
        assert "deadline" in data

        clear_deadline("test-worker-1")
        assert not path.exists()

    def test_clear_also_removes_sigterm_marker(self):
        from sqlery.django_sqlery.deadlines import write_deadline, clear_deadline, SIGTERM_SUFFIX

        job = _create_running_job(timeout_seconds=60)
        write_deadline("test-worker-2", job)

        path = self.deadline_dir / "worker-test-worker-2.json"
        sigterm_path = path.with_suffix(SIGTERM_SUFFIX)
        sigterm_path.write_text("2026-01-01T00:00:00+00:00")

        clear_deadline("test-worker-2")
        assert not path.exists()
        assert not sigterm_path.exists()

    def test_write_deadline_uses_default_timeout_when_none(self):
        from sqlery.django_sqlery.deadlines import write_deadline, DEFAULT_JOB_TIMEOUT

        job = _create_running_job(timeout_seconds=None)
        write_deadline("test-worker-3", job)

        path = self.deadline_dir / "worker-test-worker-3.json"
        data = json.loads(path.read_text())
        assert data["timeout_seconds"] == DEFAULT_JOB_TIMEOUT

        path.unlink(missing_ok=True)


# ── Enforce deadlines tests ───────────────────────────────────────

@pytest.mark.django_db
class TestEnforceDeadlines:
    """Test the two-phase non-blocking deadline enforcement."""

    def setup_method(self):
        from sqlery.django_sqlery.deadlines import DEADLINE_DIR
        self.deadline_dir = DEADLINE_DIR
        self.deadline_dir.mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        if self.deadline_dir.exists():
            for f in self.deadline_dir.glob("worker-enforce-*"):
                f.unlink(missing_ok=True)

    def test_enforce_skips_non_overdue_deadlines(self):
        """Deadline in the future should not be enforced."""
        from sqlery.django_sqlery.deadlines import enforce_deadlines
        from datetime import datetime, timezone as dt_tz

        job = _create_running_job(timeout_seconds=3600)
        future = (datetime.now(dt_tz.utc) + timedelta(hours=2)).isoformat()
        path = self.deadline_dir / "worker-enforce-future.json"
        path.write_text(json.dumps({
            "job_id": job.id,
            "worker_pid": 99999,
            "timeout_seconds": 3600,
            "started_at": datetime.now(dt_tz.utc).isoformat(),
            "deadline": future,
        }))

        enforced = enforce_deadlines()
        assert enforced == 0
        assert path.exists()  # file not removed
        job.refresh_from_db()
        assert job.status == "running"  # not touched

        path.unlink()

    def test_enforce_dead_process_reconciles_immediately(self):
        """Overdue deadline + dead process → reconcile DB in one cycle."""
        from sqlery.django_sqlery.deadlines import enforce_deadlines
        from datetime import datetime, timezone as dt_tz

        job = _create_running_job(timeout_seconds=60)
        past = (datetime.now(dt_tz.utc) - timedelta(minutes=5)).isoformat()
        path = self.deadline_dir / "worker-enforce-dead.json"
        path.write_text(json.dumps({
            "job_id": job.id,
            "worker_pid": 99999,  # PID that doesn't exist
            "timeout_seconds": 60,
            "started_at": past,
            "deadline": past,
        }))

        enforced = enforce_deadlines()
        assert enforced == 1
        assert not path.exists()  # cleaned up
        job.refresh_from_db()
        assert job.status == "failed"
        assert "Daemon watchdog" in job.error
        assert "already dead" in job.error

    def test_enforce_alive_process_phase1_sends_sigterm(self):
        """Overdue deadline + alive process → SIGTERM + .sigterm marker."""
        from sqlery.django_sqlery.deadlines import enforce_deadlines, SIGTERM_SUFFIX
        from datetime import datetime, timezone as dt_tz

        job = _create_running_job(timeout_seconds=60)
        past = (datetime.now(dt_tz.utc) - timedelta(minutes=5)).isoformat()
        path = self.deadline_dir / "worker-enforce-alive.json"
        path.write_text(json.dumps({
            "job_id": job.id,
            "worker_pid": os.getpid(),  # Current process — alive
            "timeout_seconds": 60,
            "started_at": past,
            "deadline": past,
        }))

        with patch("sqlery.django_sqlery.deadlines.os.kill") as mock_kill:
            with patch("sqlery.django_sqlery.deadlines._pid_is_sqlery_worker", return_value=True):
                enforced = enforce_deadlines()

        # Phase 1: SIGTERM sent, marker written, but NOT reconciled yet
        assert enforced == 0  # not counted until phase 2
        assert path.exists()  # deadline file still present
        sigterm_path = path.with_suffix(SIGTERM_SUFFIX)
        assert sigterm_path.exists()  # marker written
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

        job.refresh_from_db()
        assert job.status == "running"  # not failed yet

        # Cleanup
        path.unlink(missing_ok=True)
        sigterm_path.unlink(missing_ok=True)

    def test_enforce_phase2_sigkill_after_sigterm(self):
        """Phase 2: .sigterm exists + process still alive → SIGKILL + reconcile."""
        from sqlery.django_sqlery.deadlines import enforce_deadlines, SIGTERM_SUFFIX
        from datetime import datetime, timezone as dt_tz

        job = _create_running_job(timeout_seconds=60)
        past = (datetime.now(dt_tz.utc) - timedelta(minutes=5)).isoformat()
        path = self.deadline_dir / "worker-enforce-phase2.json"
        path.write_text(json.dumps({
            "job_id": job.id,
            "worker_pid": os.getpid(),
            "timeout_seconds": 60,
            "started_at": past,
            "deadline": past,
        }))
        sigterm_path = path.with_suffix(SIGTERM_SUFFIX)
        sigterm_path.write_text(datetime.now(dt_tz.utc).isoformat())

        with patch("sqlery.django_sqlery.deadlines.os.kill") as mock_kill:
            with patch("sqlery.django_sqlery.deadlines._pid_is_sqlery_worker", return_value=True):
                enforced = enforce_deadlines()

        assert enforced == 1
        assert not path.exists()
        assert not sigterm_path.exists()
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGKILL)

        job.refresh_from_db()
        assert job.status == "failed"
        assert "SIGKILL" in job.error


# ── Rebuild deadlines tests ───────────────────────────────────────

@pytest.mark.django_db
class TestRebuildDeadlines:
    """Test rebuild_deadlines() reconstructing files from DB."""

    def setup_method(self):
        from sqlery.django_sqlery.deadlines import DEADLINE_DIR
        self.deadline_dir = DEADLINE_DIR
        # Clean directory
        if self.deadline_dir.exists():
            for f in self.deadline_dir.glob("worker-*.json"):
                f.unlink(missing_ok=True)

    def teardown_method(self):
        if self.deadline_dir.exists():
            for f in self.deadline_dir.glob("worker-*.json"):
                f.unlink(missing_ok=True)

    def test_rebuild_creates_deadline_for_running_job(self):
        from sqlery.django_sqlery.deadlines import rebuild_deadlines

        worker = _create_worker(status="busy", pid=12345)
        job = _create_running_job(started_seconds_ago=60, timeout_seconds=300, worker=worker)
        worker.current_job = job
        worker.save(update_fields=["current_job"])

        rebuilt = rebuild_deadlines()
        assert rebuilt == 1

        path = self.deadline_dir / f"worker-{worker.id}.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["job_id"] == job.id
        assert data["timeout_seconds"] == 300

    def test_rebuild_skips_existing_deadline_files(self):
        from sqlery.django_sqlery.deadlines import rebuild_deadlines

        worker = _create_worker(status="busy", pid=12345)
        job = _create_running_job(started_seconds_ago=60, timeout_seconds=300, worker=worker)
        worker.current_job = job
        worker.save(update_fields=["current_job"])

        # Pre-create the deadline file
        self.deadline_dir.mkdir(parents=True, exist_ok=True)
        path = self.deadline_dir / f"worker-{worker.id}.json"
        path.write_text('{"existing": true}')

        rebuilt = rebuild_deadlines()
        assert rebuilt == 0  # skipped

        data = json.loads(path.read_text())
        assert data.get("existing") is True  # not overwritten


# ── diagnose_system_health tests ──────────────────────────────────

@pytest.mark.django_db
class TestDiagnoseSystemHealth:
    """Test the read-only diagnosis function."""

    def test_healthy_system_returns_empty(self):
        """No problems detected when system is healthy."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        # Create a healthy worker with fresh heartbeat
        _create_worker(status="idle", heartbeat_age_seconds=5)

        problems = diagnose_system_health(check_os=False)
        assert problems == []

    def test_stale_busy_worker_detected_db_only(self):
        """DB-only mode detects stale busy worker heartbeat."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        job = _create_running_job()
        _create_worker(status="busy", heartbeat_age_seconds=200, current_job=job)

        problems = diagnose_system_health(check_os=False)
        kinds = [p['kind'] for p in problems]
        assert 'stale_busy_workers' in kinds

    def test_stale_idle_worker_not_a_problem(self):
        """An idle worker with stale heartbeat is not flagged (no job at risk)."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        _create_worker(status="idle", heartbeat_age_seconds=200)

        problems = diagnose_system_health(check_os=False)
        kinds = [p['kind'] for p in problems]
        assert 'stale_busy_workers' not in kinds

    def test_ghost_running_job_detected(self):
        """Job in running state with no worker pointing to it."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        # Create a running job with no worker
        _create_running_job(started_seconds_ago=60)
        # Create an idle worker (not pointing to the job)
        _create_worker(status="idle", heartbeat_age_seconds=5)

        problems = diagnose_system_health(check_os=False)
        kinds = [p['kind'] for p in problems]
        assert 'ghost_running_jobs' in kinds

    def test_no_workers_with_queued_jobs_detected(self):
        """Queued jobs but zero active workers."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        QueuedJob.objects.create(
            task_path="tests.test_intervention.dummy_task",
            queue_name="default",
            status="queued",
        )

        problems = diagnose_system_health(check_os=False)
        kinds = [p['kind'] for p in problems]
        assert 'no_workers' in kinds

    def test_no_workers_no_jobs_is_fine(self):
        """No workers and no jobs is not a problem."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        problems = diagnose_system_health(check_os=False)
        assert problems == []

    def test_check_os_detects_dead_pid(self):
        """OS mode detects worker with dead PID."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        _create_worker(status="idle", heartbeat_age_seconds=5, pid=99999)

        with patch("sqlery.django_sqlery.intervention._pid_is_sqlery_worker", return_value=False):
            problems = diagnose_system_health(check_os=True)

        kinds = [p['kind'] for p in problems]
        assert 'dead_workers' in kinds

    def test_check_os_false_does_not_check_pids(self):
        """DB-only mode never calls _pid_is_sqlery_worker."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        _create_worker(status="idle", heartbeat_age_seconds=5, pid=99999)

        with patch("sqlery.django_sqlery.intervention._pid_is_sqlery_worker") as mock_pid:
            problems = diagnose_system_health(check_os=False)

        mock_pid.assert_not_called()

    def test_timed_out_job_detected(self):
        """Job past started_at + timeout_seconds is flagged."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        # Job started 120s ago with 60s timeout → 60s overdue
        worker = _create_worker(status="busy", heartbeat_age_seconds=5)
        job = _create_running_job(started_seconds_ago=120, timeout_seconds=60, worker=worker)
        worker.current_job = job
        worker.save(update_fields=["current_job"])

        problems = diagnose_system_health(check_os=False)
        kinds = [p['kind'] for p in problems]
        assert 'timed_out_jobs' in kinds

    def test_job_within_timeout_not_flagged(self):
        """Job still within its timeout is fine."""
        from sqlery.django_sqlery.intervention import diagnose_system_health

        worker = _create_worker(status="busy", heartbeat_age_seconds=5)
        job = _create_running_job(started_seconds_ago=30, timeout_seconds=3600, worker=worker)
        worker.current_job = job
        worker.save(update_fields=["current_job"])

        problems = diagnose_system_health(check_os=False)
        kinds = [p['kind'] for p in problems]
        assert 'timed_out_jobs' not in kinds


# ── do_manual_intervention tests ──────────────────────────────────

@pytest.mark.django_db
class TestDoManualIntervention:
    """Test the daemon-side intervention logic."""

    def test_refuses_when_healthy(self):
        """Returns immediately if no problems detected."""
        from sqlery.django_sqlery.intervention import do_manual_intervention

        _create_worker(status="idle", heartbeat_age_seconds=5)

        with patch("sqlery.django_sqlery.intervention._pid_is_sqlery_worker", return_value=True):
            with patch("sqlery.django_sqlery.intervention.ensure_worker_pool", return_value={"spawned": 0}):
                result = do_manual_intervention()

        assert "No issues found" in result['diagnosed'][0]
        assert result['jobs_failed'] == 0
        assert result['workers_killed'] == 0

    def test_fails_ghost_jobs(self):
        """Ghost running jobs get marked as failed."""
        from sqlery.django_sqlery.intervention import do_manual_intervention

        ghost_job = _create_running_job(started_seconds_ago=60)
        # No worker points to this job

        with patch("sqlery.django_sqlery.intervention._pid_is_sqlery_worker", return_value=True):
            with patch("sqlery.django_sqlery.intervention.ensure_worker_pool", return_value={"spawned": 0}):
                result = do_manual_intervention()

        assert result['jobs_failed'] >= 1
        ghost_job.refresh_from_db()
        assert ghost_job.status == "failed"
        assert "ghost" in ghost_job.error.lower()

    def test_cleans_dead_workers(self):
        """Workers with dead PIDs get marked as dead."""
        from sqlery.django_sqlery.intervention import do_manual_intervention

        job = _create_running_job()
        worker = _create_worker(status="busy", heartbeat_age_seconds=5, pid=99999, current_job=job)

        with patch("sqlery.django_sqlery.intervention._pid_is_sqlery_worker", return_value=False):
            with patch("sqlery.django_sqlery.intervention.ensure_worker_pool", return_value={"spawned": 1}):
                result = do_manual_intervention()

        assert result['stale_workers_cleaned'] >= 1
        worker.refresh_from_db()
        assert worker.status == "dead"
        job.refresh_from_db()
        assert job.status == "failed"


# ── do_manual_intervention_direct tests ───────────────────────────

@pytest.mark.django_db
class TestDoManualInterventionDirect:
    """Test the web-server fallback intervention (DB-only)."""

    def test_marks_stale_workers_dead(self):
        from sqlery.django_sqlery.intervention import do_manual_intervention_direct

        job = _create_running_job()
        worker = _create_worker(status="busy", heartbeat_age_seconds=120, current_job=job)

        result = do_manual_intervention_direct()

        assert result['stale_workers_cleaned'] >= 1
        worker.refresh_from_db()
        assert worker.status == "dead"
        job.refresh_from_db()
        assert job.status == "failed"

    def test_fresh_workers_not_touched(self):
        from sqlery.django_sqlery.intervention import do_manual_intervention_direct

        worker = _create_worker(status="idle", heartbeat_age_seconds=5)

        result = do_manual_intervention_direct()

        worker.refresh_from_db()
        assert worker.status == "idle"  # not touched

    def test_includes_daemon_restart_note(self):
        from sqlery.django_sqlery.intervention import do_manual_intervention_direct

        result = do_manual_intervention_direct()
        assert "daemon" in result['note'].lower()


# ── DaemonCommand model tests ─────────────────────────────────────

@pytest.mark.django_db
class TestDaemonCommand:
    """Test the DaemonCommand model."""

    def test_create_command(self):
        cmd = DaemonCommand.objects.create(
            command="manual_intervention",
            payload={"triggered_by": "test"},
        )
        assert cmd.status == "pending"
        assert cmd.processed_at is None

    def test_complete_command(self):
        cmd = DaemonCommand.objects.create(command="manual_intervention")
        cmd.status = "completed"
        cmd.result = {"jobs_failed": 2}
        cmd.processed_at = timezone.now()
        cmd.save()

        cmd.refresh_from_db()
        assert cmd.status == "completed"
        assert cmd.result["jobs_failed"] == 2


# ── API endpoint tests ────────────────────────────────────────────

@pytest.mark.django_db
class TestApiManualIntervention:
    """Test the POST /admin/api/sqlery/intervene/ endpoint."""

    def setup_method(self):
        from django.contrib.auth.models import User
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def test_rejects_get(self):
        from sqlery.django_sqlery.api_views import api_manual_intervention

        request = self.factory.get("/admin/api/sqlery/intervene/")
        request.user = self.user
        response = api_manual_intervention(request)
        assert response.status_code == 405

    def test_rejects_when_healthy(self):
        from sqlery.django_sqlery.api_views import api_manual_intervention

        # Create a healthy worker
        _create_worker(status="idle", heartbeat_age_seconds=5)

        request = self.factory.post("/admin/api/sqlery/intervene/")
        request.user = self.user
        response = api_manual_intervention(request)

        assert response.status_code == 409
        data = json.loads(response.content)
        assert data["status"] == "rejected"
        assert "healthy" in data["message"].lower()

    def test_accepts_when_ghost_jobs_exist(self):
        """When problems exist, the command should be created."""
        from sqlery.django_sqlery.api_views import api_manual_intervention

        # Create a ghost running job (no worker)
        _create_running_job(started_seconds_ago=60)

        request = self.factory.post("/admin/api/sqlery/intervene/")
        request.user = self.user

        # Mock the polling loop to avoid 15s wait
        with patch("sqlery.django_sqlery.api_views.time.sleep"):
            with patch.object(DaemonCommand, "refresh_from_db") as mock_refresh:
                # Simulate daemon completing the command
                def complete_cmd(self_cmd=None):
                    cmd = DaemonCommand.objects.filter(command="manual_intervention").first()
                    if cmd:
                        cmd.status = "completed"
                        cmd.result = {"jobs_failed": 1, "diagnosed": ["1 ghost job"]}
                        cmd.processed_at = timezone.now()
                        cmd.save()

                mock_refresh.side_effect = complete_cmd
                response = api_manual_intervention(request)

        data = json.loads(response.content)
        # Should have created and processed the command
        assert data["status"] == "completed" or data["status"] == "pending"

    def test_deduplicates_pending_commands(self):
        """Second request while first is pending returns 202."""
        from sqlery.django_sqlery.api_views import api_manual_intervention

        # Create a problem so gate passes
        _create_running_job(started_seconds_ago=60)

        # Pre-create a pending command
        DaemonCommand.objects.create(
            command="manual_intervention",
            status="pending",
        )

        request = self.factory.post("/admin/api/sqlery/intervene/")
        request.user = self.user
        response = api_manual_intervention(request)

        assert response.status_code == 202
        data = json.loads(response.content)
        assert data["status"] == "pending"
        assert "already queued" in data["message"].lower()


# ── failure_ttl=-1 keeps forever ──────────────────────────────────

@pytest.mark.django_db
class TestFailureTtlKeepForever:
    """Test that failure_ttl=-1 means the job is never cleaned up."""

    def test_failure_ttl_minus_one_excluded_from_cleanup(self):
        from sqlery.django_sqlery.cleanup import CleanupManager

        # Create a failed job with failure_ttl=-1 (keep forever)
        job_keep = QueuedJob.objects.create(
            task_path="tests.test_intervention.dummy_task",
            queue_name="default",
            status="failed",
            failure_ttl=-1,
            finished_at=timezone.now() - timedelta(days=365),  # 1 year old
        )

        # Create a failed job with failure_ttl=1 (expire after 1 second)
        job_expire = QueuedJob.objects.create(
            task_path="tests.test_intervention.dummy_task",
            queue_name="default",
            status="failed",
            failure_ttl=1,
            finished_at=timezone.now() - timedelta(days=1),  # 1 day old
        )

        manager = CleanupManager()
        result = manager.cleanup_per_job_ttl()

        # job_expire should be deleted
        assert not QueuedJob.objects.filter(id=job_expire.id).exists()

        # job_keep should still exist (failure_ttl=-1)
        assert QueuedJob.objects.filter(id=job_keep.id).exists()

    def test_failure_ttl_none_uses_global(self):
        """failure_ttl=None should not be touched by per-job TTL cleanup."""
        from sqlery.django_sqlery.cleanup import CleanupManager

        job = QueuedJob.objects.create(
            task_path="tests.test_intervention.dummy_task",
            queue_name="default",
            status="failed",
            failure_ttl=None,
            finished_at=timezone.now() - timedelta(days=365),
        )

        manager = CleanupManager()
        result = manager.cleanup_per_job_ttl()

        # Should still exist — per-job TTL only applies when failure_ttl is set
        assert QueuedJob.objects.filter(id=job.id).exists()
