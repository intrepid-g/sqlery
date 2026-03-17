"""Tests for optimistic locking with version field.

Tests ensure 100% reliable atomic job claiming across both SQLite and PostgreSQL
using version-based optimistic locking.
"""

import pytest
from django.test import TestCase
from django.utils import timezone
from unittest.mock import Mock
from sqlery.models import QueuedJob, Worker, ConcurrentModificationError
from sqlery.db_compat import atomic_claim_job, is_sqlite


class TestVersionBasedLocking(TestCase):
    """Test version field optimistic locking for atomic job claiming."""

    def setUp(self):
        """Create test worker and job."""
        self.worker = Worker.objects.create(
            node_id="test-node",
            pid=12345,
            queues=["default"]
        )
        self.job = QueuedJob.objects.create(
            task_path="tests.tasks.dummy_task",
            queue_name="default",
            status="queued",
            priority=0
        )

    def test_version_field_defaults_to_zero(self):
        """New jobs should have version=0."""
        assert self.job.version == 0

    def test_version_increments_on_claim(self):
        """Version should increment when job is claimed."""
        initial_version = self.job.version

        success = atomic_claim_job(self.job, self.worker)

        assert success is True
        assert self.job.version == initial_version + 1
        assert self.job.status == "running"

    def test_concurrent_claim_only_one_succeeds(self):
        """When two workers claim same job, only one should succeed."""
        worker2 = Worker.objects.create(
            node_id="test-node-2",
            pid=12346,
            queues=["default"]
        )

        # Both workers read the job (same version)
        job_for_worker1 = QueuedJob.objects.get(id=self.job.id)
        job_for_worker2 = QueuedJob.objects.get(id=self.job.id)

        assert job_for_worker1.version == job_for_worker2.version

        # First worker claims
        success1 = atomic_claim_job(job_for_worker1, self.worker)

        # Second worker tries to claim (version now mismatched)
        success2 = atomic_claim_job(job_for_worker2, worker2)

        # Only first worker should succeed
        assert success1 is True
        assert success2 is False

        # Verify job was claimed by first worker only
        job = QueuedJob.objects.get(id=self.job.id)
        assert job.worker == self.worker
        assert job.status == "running"
        assert job.version == 1  # Incremented once

    def test_mark_success_increments_version(self):
        """mark_success should increment version."""
        # Claim the job first
        atomic_claim_job(self.job, self.worker)
        version_after_claim = self.job.version

        # Mark as success
        self.job.mark_success(output="Test output")

        assert self.job.version == version_after_claim + 1
        assert self.job.status == "success"

    def test_mark_failed_increments_version(self):
        """mark_failed should increment version."""
        # Claim the job first
        atomic_claim_job(self.job, self.worker)
        version_after_claim = self.job.version

        # Mark as failed
        self.job.mark_failed(error="Test error", traceback="Test traceback")

        assert self.job.version == version_after_claim + 1
        assert self.job.status == "failed"

    def test_version_conflict_raises_error_on_mark_success(self):
        """Concurrent modification during mark_success should raise error."""
        # Claim the job
        atomic_claim_job(self.job, self.worker)

        # Simulate another process modifying the job
        QueuedJob.objects.filter(id=self.job.id).update(version=999)

        # mark_success should detect version conflict
        with pytest.raises(ConcurrentModificationError):
            self.job.mark_success(output="Test")

    def test_version_conflict_raises_error_on_mark_failed(self):
        """Concurrent modification during mark_failed should raise error."""
        # Claim the job
        atomic_claim_job(self.job, self.worker)

        # Simulate another process modifying the job
        QueuedJob.objects.filter(id=self.job.id).update(version=999)

        # mark_failed should detect version conflict
        with pytest.raises(ConcurrentModificationError):
            self.job.mark_failed(error="Test error")

    def test_multiple_claim_attempts_all_fail_except_first(self):
        """Simulate 5 workers trying to claim same job."""
        workers = [
            Worker.objects.create(
                node_id=f"node-{i}",
                pid=10000 + i,
                queues=["default"]
            )
            for i in range(5)
        ]

        # All workers read the job (same version)
        job_copies = [
            QueuedJob.objects.get(id=self.job.id)
            for _ in range(5)
        ]

        # All workers try to claim
        results = [
            atomic_claim_job(job_copy, worker)
            for job_copy, worker in zip(job_copies, workers)
        ]

        # Only first should succeed
        assert results[0] is True
        assert all(result is False for result in results[1:])

        # Verify final state
        job = QueuedJob.objects.get(id=self.job.id)
        assert job.worker == workers[0]
        assert job.status == "running"
        assert job.version == 1

    def test_version_persists_across_job_lifecycle(self):
        """Version should continuously increment through job lifecycle."""
        # Initial state
        assert self.job.version == 0

        # Claim
        atomic_claim_job(self.job, self.worker)
        self.job.refresh_from_db()
        assert self.job.version == 1

        # Mark success
        self.job.mark_success(output="Done")
        self.job.refresh_from_db()
        assert self.job.version == 2
        assert self.job.status == "success"

    def test_stale_job_object_cannot_update(self):
        """Stale job object (old version) should fail to update."""
        # Get two references to same job
        job1 = QueuedJob.objects.get(id=self.job.id)
        job2 = QueuedJob.objects.get(id=self.job.id)

        # job1 claims it
        success1 = atomic_claim_job(job1, self.worker)
        assert success1 is True

        # job2 has stale version, should fail to claim
        worker2 = Worker.objects.create(
            node_id="node-2",
            pid=99999,
            queues=["default"]
        )
        success2 = atomic_claim_job(job2, worker2)
        assert success2 is False


class TestSQLiteSpecificBehavior(TestCase):
    """Test SQLite-specific version locking behavior."""

    @pytest.mark.skipif(not is_sqlite(), reason="SQLite-specific test")
    def test_sqlite_uses_version_based_claiming(self):
        """Verify SQLite code path uses version-based UPDATE."""
        from sqlery.db_compat import atomic_claim_job_sqlite

        worker = Worker.objects.create(
            node_id="sqlite-test",
            pid=11111,
            queues=["default"]
        )
        job = QueuedJob.objects.create(
            task_path="tests.tasks.dummy",
            queue_name="default",
            status="queued"
        )

        # SQLite should use version-based claiming
        success = atomic_claim_job_sqlite(job, worker)

        assert success is True
        assert job.version == 1
        assert job.worker == worker


class TestPostgreSQLCompatibility(TestCase):
    """Test that PostgreSQL still works with version field."""

    @pytest.mark.skipif(is_sqlite(), reason="PostgreSQL-specific test")
    def test_postgres_claims_with_version_check(self):
        """PostgreSQL should also use version field for consistency."""
        from sqlery.db_compat import atomic_claim_job_postgres

        worker = Worker.objects.create(
            node_id="postgres-test",
            pid=22222,
            queues=["default"]
        )
        job = QueuedJob.objects.create(
            task_path="tests.tasks.dummy",
            queue_name="default",
            status="queued"
        )

        # Postgres should also check version
        success = atomic_claim_job_postgres(job, worker)

        assert success is True
        assert job.version == 1
        assert job.worker == worker
