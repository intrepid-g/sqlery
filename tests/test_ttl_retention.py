"""Tests for TTL & Retention system.

Covers:
1. expire_ttl_jobs() in core/claiming.py -- marks queued jobs as failed when TTL expires
2. CleanupManager in core/cleanup.py -- cleanup_old_jobs(), cleanup_by_count(), auto_cleanup()
3. Job-level TTL fields -- ttl, result_ttl, failure_ttl on QueuedJob model
"""

import pytest
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from sqlery.models import QueuedJob
from sqlery.core.claiming import expire_ttl_jobs
from sqlery.core.cleanup import CleanupManager
from sqlery.django_sqlery.backend import DjangoBackend


def _create_job(**kwargs):
    """Create a QueuedJob with sensible defaults, allowing overrides."""
    defaults = {
        "task_path": "tests.test_ttl_retention.dummy_task",
        "queue_name": "default",
        "priority": 0,
        "status": "queued",
    }
    defaults.update(kwargs)
    return QueuedJob.objects.create(**defaults)


def dummy_task():
    """Placeholder task for tests."""
    return "ok"


# ---------------------------------------------------------------------------
# 1. Job-level TTL fields on QueuedJob model
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestJobTTLFields:
    """Test that TTL-related fields are stored correctly on QueuedJob."""

    def test_ttl_field_defaults_to_none(self):
        """Job TTL should default to None (no limit)."""
        job = _create_job()
        assert job.ttl is None

    def test_result_ttl_field_defaults_to_none(self):
        """result_ttl should default to None (use global)."""
        job = _create_job()
        assert job.result_ttl is None

    def test_failure_ttl_field_defaults_to_none(self):
        """failure_ttl should default to None (use global)."""
        job = _create_job()
        assert job.failure_ttl is None

    def test_ttl_field_stored_correctly(self):
        """Job TTL should be persisted to the database."""
        job = _create_job(ttl=60)
        job.refresh_from_db()
        assert job.ttl == 60

    def test_result_ttl_stored_correctly(self):
        """result_ttl should be persisted to the database."""
        job = _create_job(result_ttl=3600)
        job.refresh_from_db()
        assert job.result_ttl == 3600

    def test_failure_ttl_stored_correctly(self):
        """failure_ttl should be persisted to the database."""
        job = _create_job(failure_ttl=86400)
        job.refresh_from_db()
        assert job.failure_ttl == 86400

    def test_result_ttl_negative_one_means_forever(self):
        """result_ttl=-1 should be a valid value meaning 'keep forever'."""
        job = _create_job(result_ttl=-1)
        job.refresh_from_db()
        assert job.result_ttl == -1

    def test_all_ttl_fields_set_together(self):
        """All three TTL fields should coexist on one job."""
        job = _create_job(ttl=30, result_ttl=600, failure_ttl=1200)
        job.refresh_from_db()
        assert job.ttl == 30
        assert job.result_ttl == 600
        assert job.failure_ttl == 1200


# ---------------------------------------------------------------------------
# 2. expire_ttl_jobs() in core/claiming.py
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpireTTLJobs:
    """Test expire_ttl_jobs() marks queued jobs as failed when TTL expires."""

    def test_expired_job_is_marked_failed(self):
        """A queued job past its TTL should be marked as failed."""
        backend = DjangoBackend()
        job = _create_job(ttl=60)

        # Backdate created_at so the job is past its TTL.
        # Re-fetch after update because created_at is part of the composite PK —
        # changing it makes the in-memory object's PK stale.
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(seconds=120)
        )
        job = QueuedJob.objects.get(id=job.id)

        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 1
        job = QueuedJob.objects.filter(id=job.id).first()
        assert job is not None
        assert job.status == "failed"
        assert "expired" in job.error.lower()
        assert job.termination_reason == "expired"

    def test_non_expired_job_left_alone(self):
        """A queued job within its TTL should remain queued."""
        backend = DjangoBackend()
        job = _create_job(ttl=600)

        # Job was just created so it is well within its TTL
        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 0
        job.refresh_from_db()
        assert job.status == "queued"

    def test_job_without_ttl_is_never_expired(self):
        """A queued job with ttl=None should never be expired."""
        backend = DjangoBackend()
        job = _create_job(ttl=None)

        # Backdate created_at far into the past.
        # Re-fetch after update because created_at is part of the composite PK.
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        job = QueuedJob.objects.get(id=job.id)

        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 0
        job.refresh_from_db()
        assert job.status == "queued"

    def test_only_queued_jobs_are_expired(self):
        """Running or finished jobs should not be expired even if past TTL."""
        backend = DjangoBackend()

        past_time = timezone.now() - timedelta(seconds=120)

        running_job = _create_job(ttl=60, status="queued")
        running_job.mark_running()
        # Re-fetch after update because created_at is part of the composite PK.
        QueuedJob.objects.filter(id=running_job.id).update(created_at=past_time)
        running_job = QueuedJob.objects.get(id=running_job.id)

        success_job = _create_job(ttl=60, status="queued")
        success_job.mark_running()
        success_job.mark_success(output="done")
        QueuedJob.objects.filter(id=success_job.id).update(created_at=past_time)
        success_job = QueuedJob.objects.get(id=success_job.id)

        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 0
        running_job.refresh_from_db()
        assert running_job.status == "running"
        success_job.refresh_from_db()
        assert success_job.status == "success"

    def test_multiple_expired_jobs(self):
        """Multiple expired jobs should all be marked failed."""
        backend = DjangoBackend()
        past_time = timezone.now() - timedelta(seconds=120)

        jobs = [_create_job(ttl=60) for _ in range(5)]
        job_ids = [j.id for j in jobs]
        # Re-fetch after update because created_at is part of the composite PK.
        QueuedJob.objects.filter(id__in=job_ids).update(created_at=past_time)
        jobs = list(QueuedJob.objects.filter(id__in=job_ids))

        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 5
        for job in jobs:
            job.refresh_from_db()
            assert job.status == "failed"
            assert job.termination_reason == "expired"

    def test_mix_of_expired_and_fresh_jobs(self):
        """Only expired jobs should be marked failed; fresh ones stay queued."""
        backend = DjangoBackend()

        expired_job = _create_job(ttl=60)
        expired_job_id = expired_job.id
        # Re-fetch after update because created_at is part of the composite PK.
        QueuedJob.objects.filter(id=expired_job_id).update(
            created_at=timezone.now() - timedelta(seconds=120)
        )
        expired_job = QueuedJob.objects.get(id=expired_job_id)

        fresh_job = _create_job(ttl=60)
        # fresh_job keeps its default created_at (just now)

        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 1
        expired_job = QueuedJob.objects.filter(id=expired_job_id).first()
        assert expired_job is not None
        assert expired_job.status == "failed"
        fresh_job.refresh_from_db()
        assert fresh_job.status == "queued"

    def test_boundary_ttl_not_yet_expired(self):
        """A job exactly at its TTL boundary should not be expired yet."""
        backend = DjangoBackend()
        # Set created_at to 59 seconds ago with a 60-second TTL.
        # Re-fetch after update because created_at is part of the composite PK.
        job = _create_job(ttl=60)
        job_id = job.id
        QueuedJob.objects.filter(id=job_id).update(
            created_at=timezone.now() - timedelta(seconds=59)
        )
        job = QueuedJob.objects.get(id=job_id)

        expired_count = expire_ttl_jobs(backend)

        assert expired_count == 0
        job.refresh_from_db()
        assert job.status == "queued"

    def test_expire_returns_zero_when_no_jobs(self):
        """expire_ttl_jobs should return 0 when no jobs exist."""
        backend = DjangoBackend()
        expired_count = expire_ttl_jobs(backend)
        assert expired_count == 0


# ---------------------------------------------------------------------------
# 3. get_expired_ttl_jobs() on DjangoBackend
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetExpiredTTLJobs:
    """Test the backend method that retrieves expired TTL jobs."""

    def test_returns_expired_queued_jobs(self):
        """Backend should return queued jobs whose TTL has elapsed."""
        backend = DjangoBackend()
        job = _create_job(ttl=10)
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(seconds=20)
        )

        expired = backend.get_expired_ttl_jobs()
        assert len(expired) == 1
        assert expired[0].id == job.id

    def test_does_not_return_jobs_without_ttl(self):
        """Backend should not return jobs where ttl is None."""
        backend = DjangoBackend()
        _create_job(ttl=None)

        expired = backend.get_expired_ttl_jobs()
        assert len(expired) == 0

    def test_does_not_return_non_queued_jobs(self):
        """Backend should only consider jobs in 'queued' status."""
        backend = DjangoBackend()
        job = _create_job(ttl=10)
        job.mark_running()
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(seconds=20)
        )

        expired = backend.get_expired_ttl_jobs()
        assert len(expired) == 0


# ---------------------------------------------------------------------------
# 4. CleanupManager.cleanup_old_jobs()
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCleanupOldJobs:
    """Test CleanupManager.cleanup_old_jobs() removes jobs based on age."""

    def _make_old_job(self, status, days_old):
        """Create a job and backdate it."""
        job = _create_job(status="queued")
        # Transition to requested status
        if status == "running":
            job.mark_running()
        elif status == "success":
            job.mark_running()
            job.mark_success(output="done")
        elif status == "failed":
            job.mark_running()
            job.mark_failed(error="boom")
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(days=days_old)
        )
        return job

    def test_cleanup_deletes_old_failed_jobs(self):
        """Old failed jobs should be deleted."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        old_job = self._make_old_job("failed", days_old=40)

        result = manager.cleanup_old_jobs(status="failed", max_age_days=30)

        assert result["deleted"] == 1
        assert not QueuedJob.objects.filter(id=old_job.id).exists()

    def test_cleanup_deletes_old_success_jobs(self):
        """Old successful jobs should be deleted."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        old_job = self._make_old_job("success", days_old=40)

        result = manager.cleanup_old_jobs(status="success", max_age_days=30)

        assert result["deleted"] == 1
        assert not QueuedJob.objects.filter(id=old_job.id).exists()

    def test_cleanup_keeps_recent_jobs(self):
        """Jobs younger than max_age_days should not be deleted."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        recent_job = self._make_old_job("failed", days_old=5)

        result = manager.cleanup_old_jobs(status="failed", max_age_days=30)

        assert result["deleted"] == 0
        assert QueuedJob.objects.filter(id=recent_job.id).exists()

    def test_dry_run_does_not_delete(self):
        """dry_run=True should count but not delete."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        old_job = self._make_old_job("failed", days_old=40)

        result = manager.cleanup_old_jobs(
            status="failed", max_age_days=30, dry_run=True
        )

        assert result["deleted"] == 0
        assert result["would_delete"] == 1
        assert QueuedJob.objects.filter(id=old_job.id).exists()

    def test_cleanup_filters_by_queue_name(self):
        """cleanup_old_jobs should respect queue_name filter."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)

        job_email = _create_job(queue_name="email", status="queued")
        job_email.mark_running()
        job_email.mark_failed(error="boom")
        QueuedJob.objects.filter(id=job_email.id).update(
            created_at=timezone.now() - timedelta(days=40)
        )

        job_default = _create_job(queue_name="default", status="queued")
        job_default.mark_running()
        job_default.mark_failed(error="boom")
        QueuedJob.objects.filter(id=job_default.id).update(
            created_at=timezone.now() - timedelta(days=40)
        )

        result = manager.cleanup_old_jobs(
            status="failed", max_age_days=30, queue_name="email"
        )

        assert result["deleted"] == 1
        assert not QueuedJob.objects.filter(id=job_email.id).exists()
        assert QueuedJob.objects.filter(id=job_default.id).exists()

    def test_cleanup_all_statuses_when_status_is_none(self):
        """When status=None, cleanup should delete jobs regardless of status."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        old_failed = self._make_old_job("failed", days_old=40)
        old_success = self._make_old_job("success", days_old=40)

        result = manager.cleanup_old_jobs(status=None, max_age_days=30)

        assert result["deleted"] == 2
        assert not QueuedJob.objects.filter(
            id__in=[old_failed.id, old_success.id]
        ).exists()


# ---------------------------------------------------------------------------
# 5. CleanupManager.cleanup_by_count()
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCleanupByCount:
    """Test CleanupManager.cleanup_by_count() keeps only N most recent jobs."""

    def test_keeps_only_n_most_recent_jobs(self):
        """Should delete older jobs beyond keep_count."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)

        # Create 5 failed jobs with staggered creation times
        jobs = []
        for i in range(5):
            job = _create_job(status="queued")
            job.mark_running()
            job.mark_failed(error="fail")
            QueuedJob.objects.filter(id=job.id).update(
                created_at=timezone.now() - timedelta(days=5 - i)
            )
            jobs.append(job)

        result = manager.cleanup_by_count(status="failed", keep_count=2)

        assert result["deleted"] == 3
        # The 2 most recent should remain
        remaining_ids = set(
            QueuedJob.objects.filter(status="failed").values_list("id", flat=True)
        )
        assert jobs[3].id in remaining_ids
        assert jobs[4].id in remaining_ids

    def test_no_deletion_when_within_limit(self):
        """Should not delete anything if count is within keep_count."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)

        for _ in range(3):
            job = _create_job(status="queued")
            job.mark_running()
            job.mark_failed(error="fail")

        result = manager.cleanup_by_count(status="failed", keep_count=10)

        assert result["deleted"] == 0

    def test_dry_run_does_not_delete(self):
        """dry_run=True should count but not delete."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)

        for i in range(5):
            job = _create_job(status="queued")
            job.mark_running()
            job.mark_failed(error="fail")
            QueuedJob.objects.filter(id=job.id).update(
                created_at=timezone.now() - timedelta(days=5 - i)
            )

        result = manager.cleanup_by_count(
            status="failed", keep_count=2, dry_run=True
        )

        assert result["deleted"] == 0
        assert result["would_delete"] == 3
        assert QueuedJob.objects.filter(status="failed").count() == 5

    def test_cleanup_by_count_filters_by_queue(self):
        """cleanup_by_count should respect queue_name filter."""
        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)

        # 3 jobs in 'email' queue
        for i in range(3):
            job = _create_job(queue_name="email", status="queued")
            job.mark_running()
            job.mark_failed(error="fail")
            QueuedJob.objects.filter(id=job.id).update(
                created_at=timezone.now() - timedelta(days=3 - i)
            )

        # 3 jobs in 'default' queue
        for i in range(3):
            job = _create_job(queue_name="default", status="queued")
            job.mark_running()
            job.mark_failed(error="fail")
            QueuedJob.objects.filter(id=job.id).update(
                created_at=timezone.now() - timedelta(days=3 - i)
            )

        result = manager.cleanup_by_count(
            status="failed", keep_count=1, queue_name="email"
        )

        assert result["deleted"] == 2
        # 'email' should have 1 remaining, 'default' should have 3
        assert QueuedJob.objects.filter(
            status="failed", queue_name="email"
        ).count() == 1
        assert QueuedJob.objects.filter(
            status="failed", queue_name="default"
        ).count() == 3


# ---------------------------------------------------------------------------
# 6. CleanupManager.auto_cleanup()
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAutoCleanup:
    """Test CleanupManager.auto_cleanup() runs configured retention policies."""

    def _make_old_job_for_status(self, status, days_old):
        """Create a job with given final status, backdated."""
        job = _create_job(status="queued")
        if status in ("success", "failed"):
            job.mark_running()
        if status == "success":
            job.mark_success(output="done")
        elif status == "failed":
            job.mark_failed(error="boom")
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(days=days_old)
        )
        return job

    @patch("sqlery.compat.get_config")
    def test_auto_cleanup_by_age(self, mock_get_config):
        """auto_cleanup should delete old jobs when age limits are configured."""
        mock_get_config.side_effect = lambda key, default=None: {
            "JOB_RETENTION": {
                "success_max_age_days": 7,
                "failed_max_age_days": 14,
            },
            "AUTO_CLEANUP_REGISTRIES": False,
            "REGISTRY_RETENTION": {},
        }.get(key, default)

        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        # Override the retention_config that was set in __init__
        manager.retention_config = {
            "success_max_age_days": 7,
            "failed_max_age_days": 14,
        }

        old_success = self._make_old_job_for_status("success", days_old=10)
        recent_success = self._make_old_job_for_status("success", days_old=3)
        old_failed = self._make_old_job_for_status("failed", days_old=20)
        recent_failed = self._make_old_job_for_status("failed", days_old=5)

        results = manager.auto_cleanup(dry_run=False)

        assert results["dry_run"] is False
        assert len(results["actions"]) >= 1

        # Old success should be deleted, recent kept
        assert not QueuedJob.objects.filter(id=old_success.id).exists()
        assert QueuedJob.objects.filter(id=recent_success.id).exists()
        # Old failed should be deleted, recent kept
        assert not QueuedJob.objects.filter(id=old_failed.id).exists()
        assert QueuedJob.objects.filter(id=recent_failed.id).exists()

    @patch("sqlery.compat.get_config")
    def test_auto_cleanup_dry_run(self, mock_get_config):
        """auto_cleanup with dry_run=True should not delete anything."""
        mock_get_config.side_effect = lambda key, default=None: {
            "JOB_RETENTION": {"success_max_age_days": 7},
            "AUTO_CLEANUP_REGISTRIES": False,
            "REGISTRY_RETENTION": {},
        }.get(key, default)

        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        manager.retention_config = {"success_max_age_days": 7}

        old_job = self._make_old_job_for_status("success", days_old=10)

        results = manager.auto_cleanup(dry_run=True)

        assert results["dry_run"] is True
        # Job should still exist
        assert QueuedJob.objects.filter(id=old_job.id).exists()

    @patch("sqlery.compat.get_config")
    def test_auto_cleanup_with_count_limits(self, mock_get_config):
        """auto_cleanup should apply count-based limits when configured."""
        mock_get_config.side_effect = lambda key, default=None: {
            "JOB_RETENTION": {"failed_max_count": 2},
            "AUTO_CLEANUP_REGISTRIES": False,
            "REGISTRY_RETENTION": {},
        }.get(key, default)

        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        manager.retention_config = {"failed_max_count": 2}

        jobs = []
        for i in range(5):
            job = self._make_old_job_for_status("failed", days_old=5 - i)
            jobs.append(job)

        results = manager.auto_cleanup(dry_run=False)

        # Should keep only the 2 most recent failed jobs
        remaining = QueuedJob.objects.filter(status="failed").count()
        assert remaining == 2

    @patch("sqlery.compat.get_config")
    def test_auto_cleanup_no_config_does_nothing(self, mock_get_config):
        """auto_cleanup with empty config should produce no actions."""
        mock_get_config.side_effect = lambda key, default=None: {
            "JOB_RETENTION": {},
            "AUTO_CLEANUP_REGISTRIES": False,
            "REGISTRY_RETENTION": {},
        }.get(key, default)

        backend = DjangoBackend()
        manager = CleanupManager(backend=backend)
        manager.retention_config = {}

        self._make_old_job_for_status("failed", days_old=100)

        results = manager.auto_cleanup(dry_run=False)

        assert len(results["actions"]) == 0
        # Job should still exist
        assert QueuedJob.objects.filter(status="failed").count() == 1


# ---------------------------------------------------------------------------
# 7. Backend cleanup_jobs() and cleanup_jobs_by_count() directly
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBackendCleanupMethods:
    """Test DjangoBackend cleanup methods directly."""

    def test_cleanup_jobs_by_status_and_age(self):
        """Backend cleanup_jobs should delete by status and age."""
        backend = DjangoBackend()
        job = _create_job(status="queued")
        job.mark_running()
        job.mark_failed(error="err")
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        result = backend.cleanup_jobs(status="failed", max_age_days=5)
        assert result["deleted"] == 1

    def test_cleanup_jobs_dry_run(self):
        """Backend cleanup_jobs dry_run should count without deleting."""
        backend = DjangoBackend()
        job = _create_job(status="queued")
        job.mark_running()
        job.mark_failed(error="err")
        QueuedJob.objects.filter(id=job.id).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        result = backend.cleanup_jobs(status="failed", max_age_days=5, dry_run=True)
        assert result["count"] == 1
        assert QueuedJob.objects.filter(id=job.id).exists()

    def test_cleanup_jobs_by_count_keeps_most_recent(self):
        """Backend cleanup_jobs_by_count should keep the N most recent."""
        backend = DjangoBackend()
        jobs = []
        for i in range(4):
            job = _create_job(status="queued")
            job.mark_running()
            job.mark_success(output="ok")
            QueuedJob.objects.filter(id=job.id).update(
                created_at=timezone.now() - timedelta(days=4 - i)
            )
            jobs.append(job)

        result = backend.cleanup_jobs_by_count(status="success", keep_count=2)
        assert result["deleted"] == 2
        assert result["kept"] == 2

        remaining_ids = set(
            QueuedJob.objects.filter(status="success").values_list("id", flat=True)
        )
        # The two most recent should survive
        assert jobs[2].id in remaining_ids
        assert jobs[3].id in remaining_ids

    def test_cleanup_jobs_by_count_dry_run(self):
        """Backend cleanup_jobs_by_count dry_run should count without deleting."""
        backend = DjangoBackend()
        for i in range(3):
            job = _create_job(status="queued")
            job.mark_running()
            job.mark_success(output="ok")

        result = backend.cleanup_jobs_by_count(
            status="success", keep_count=1, dry_run=True
        )
        assert result["count"] == 2
        assert QueuedJob.objects.filter(status="success").count() == 3


# ---------------------------------------------------------------------------
# 8. Backend create_job() respects TTL fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBackendCreateJobWithTTL:
    """Test that DjangoBackend.create_job() correctly persists TTL fields.

    The public enqueue() API does not yet expose ttl/result_ttl/failure_ttl
    as explicit parameters, but the backend layer does accept them.
    """

    def test_create_job_with_ttl(self):
        """Backend create_job should store ttl on the job."""
        backend = DjangoBackend()
        job = backend.create_job(
            task_path="tests.test_ttl_retention.dummy_task",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
            ttl=120,
        )
        job.refresh_from_db()
        assert job.ttl == 120

    def test_create_job_with_result_ttl(self):
        """Backend create_job should store result_ttl on the job."""
        backend = DjangoBackend()
        job = backend.create_job(
            task_path="tests.test_ttl_retention.dummy_task",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
            result_ttl=3600,
        )
        job.refresh_from_db()
        assert job.result_ttl == 3600

    def test_create_job_with_failure_ttl(self):
        """Backend create_job should store failure_ttl on the job."""
        backend = DjangoBackend()
        job = backend.create_job(
            task_path="tests.test_ttl_retention.dummy_task",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
            failure_ttl=7200,
        )
        job.refresh_from_db()
        assert job.failure_ttl == 7200

    def test_create_job_with_all_ttl_fields(self):
        """Backend create_job should store all TTL fields together."""
        backend = DjangoBackend()
        job = backend.create_job(
            task_path="tests.test_ttl_retention.dummy_task",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
            ttl=30,
            result_ttl=600,
            failure_ttl=1200,
        )
        job.refresh_from_db()
        assert job.ttl == 30
        assert job.result_ttl == 600
        assert job.failure_ttl == 1200
