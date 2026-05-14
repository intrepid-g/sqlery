# #CLEANUP 2026-05-14: dead — superseded by test_subprocess_chaos.py and test_lease_zombie.py.
# API drifted (TaskExecutor.claim_job removed; multiprocessing.Process + local-func pickling
# broken under pytest — RESEARCH Pitfall #2). Preserved for historical reference per
# CLAUDE.md feedback_dead_code policy. Remove no earlier than Phase 4 cleanup pass.
import pytest

pytest.skip(
    "Legacy chaos suite; superseded by test_subprocess_chaos / test_lease_zombie",
    allow_module_level=True,
)

"""Chaos tests that deliberately kill workers and corrupt state.

FAILING TESTS EXPLANATION:
These chaos tests are failing for multiple reasons:

1. TaskExecutor API changes: The feature branch's TaskExecutor doesn't have a `claim_job()`
   method. It uses `run_queue_workers()` with atomic claiming via select_for_update.

2. Multiprocessing + local functions: Tests define functions inside test methods
   (e.g., `run_worker_until_killed`) which cannot be pickled for multiprocessing.
   Error: "Can't get local object 'TestWorkerKillChaos.test_...<locals>.run_worker_until_killed'"

3. Worker class confusion: Tests import both `Worker` from django_sqlery.models (Django model)
   and `Worker` from core.worker (now called WorkerProcess), causing potential conflicts.

To fix:
- Update tests to use new TaskExecutor API (run_queue_workers with once=True)
- Move local task functions to module level so they can be pickled
- Use correct Worker class imports
"""

import pytest
import time
import signal
import os
import multiprocessing
from django.utils import timezone
from datetime import timedelta

from sqlery.django_sqlery.models import QueuedJob, Worker
from sqlery.core.worker import TaskExecutor
from sqlery.core.worker import Worker as CoreWorker


def long_running_task():
    """A task that runs for a long time."""
    time.sleep(10)
    return "completed"


def fast_task():
    """A fast task."""
    return "success"


def failing_task():
    """A task that always fails."""
    raise ValueError("This task always fails")


def memory_hog_task():
    """A task that tries to consume lots of memory."""
    # Allocate ~100MB of memory
    data = [0] * (100 * 1024 * 1024 // 8)
    return f"allocated {len(data)} integers"


@pytest.mark.django_db
class TestWorkerKillChaos:
    """Tests that deliberately kill workers to test recovery."""

    def test_worker_killed_mid_job_sigkill(self):
        """Test: Worker receives SIGKILL while executing job.

        Expected: Job should eventually be detected as orphaned and reclaimed.
        """
        # Create a long-running job
        job = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.long_running_task",
            queue_name="test",
        )

        def run_worker_until_killed():
            """Run worker in subprocess, will be killed externally."""
            executor = TaskExecutor()
            # This will run until killed
            while True:
                claimed = executor.claim_job("test")
                if claimed:
                    executor.execute_job(claimed)
                time.sleep(0.1)

        # Start worker in subprocess
        worker_process = multiprocessing.Process(target=run_worker_until_killed)
        worker_process.start()

        # Wait for job to be claimed and start running
        time.sleep(0.5)

        # Refresh job state
        job.refresh_from_db()
        assert job.status == "running"

        # SIGKILL the worker (simulate crash)
        os.kill(worker_process.pid, signal.SIGKILL)
        worker_process.join(timeout=1)

        # Job should still show as "running" immediately after kill
        job.refresh_from_db()
        assert job.status == "running"

        # Simulate orphan detection (normally done by worker or scheduler)
        # Jobs running for too long without heartbeat should be detected
        stale_threshold = timezone.now() - timedelta(seconds=30)

        # Fast-forward: mark job as stale by backdating its updated_at
        job.updated_at = stale_threshold - timedelta(seconds=60)
        job.save(update_fields=['updated_at'])

        # New worker should detect and reclaim stale job
        executor = TaskExecutor()

        # Check for stale jobs (this would normally be done periodically)
        stale_jobs = QueuedJob.objects.filter(
            status="running",
            updated_at__lt=stale_threshold
        )

        assert stale_jobs.count() == 1
        assert stale_jobs.first().id == job.id

        # Stale job should be reclaimable (reset to pending)
        for stale_job in stale_jobs:
            stale_job.status = "pending"
            stale_job.save()

        # New worker can now claim and execute it
        claimed = executor.claim_job("test")
        assert claimed is not None
        assert claimed.id == job.id

    def test_worker_sigterm_graceful_shutdown(self):
        """Test: Worker receives SIGTERM and should shutdown gracefully.

        Expected: Current job should complete, no new jobs claimed.
        """
        # Create multiple jobs
        job1 = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.fast_task",
            queue_name="test",
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.fast_task",
            queue_name="test",
        )

        shutdown_flag = multiprocessing.Value('i', 0)

        def run_worker_with_graceful_shutdown():
            """Worker that respects shutdown signal."""
            executor = TaskExecutor()

            def signal_handler(signum, frame):
                shutdown_flag.value = 1

            signal.signal(signal.SIGTERM, signal_handler)

            while shutdown_flag.value == 0:
                claimed = executor.claim_job("test")
                if claimed:
                    executor.execute_job(claimed)
                time.sleep(0.1)

        worker_process = multiprocessing.Process(target=run_worker_with_graceful_shutdown)
        worker_process.start()

        # Wait for first job to be claimed
        time.sleep(0.5)

        # Send SIGTERM
        os.kill(worker_process.pid, signal.SIGTERM)

        # Worker should shutdown gracefully
        worker_process.join(timeout=5)
        assert not worker_process.is_alive()

        # At least one job should have completed
        job1.refresh_from_db()
        assert job1.status in ["completed", "failed"]

    def test_multiple_workers_same_job_race_condition(self):
        """Test: Multiple workers try to claim same job simultaneously.

        Expected: Only one worker should successfully claim the job (atomic operation).
        """
        # Create a single job
        job = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.fast_task",
            queue_name="test",
        )

        results = multiprocessing.Manager().list()

        def try_claim_job():
            """Worker tries to claim the job."""
            executor = TaskExecutor()
            claimed = executor.claim_job("test")
            if claimed:
                results.append(claimed.id)

        # Start 10 workers simultaneously trying to claim the same job
        processes = []
        for _ in range(10):
            p = multiprocessing.Process(target=try_claim_job)
            p.start()
            processes.append(p)

        # Wait for all workers to finish
        for p in processes:
            p.join(timeout=2)

        # Only ONE worker should have successfully claimed the job
        assert len(results) == 1
        assert results[0] == job.id

        # Job should be in running or completed state, not duplicated
        job.refresh_from_db()
        assert job.status in ["running", "completed"]


@pytest.mark.django_db
class TestDatabaseChaos:
    """Tests that simulate database failures."""

    def test_job_completes_but_status_update_fails(self):
        """Test: Job executes successfully but database update fails.

        This simulates the case where job completes but status update doesn't persist.
        Expected: Job should be detectable as stale and handled correctly.
        """
        job = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.fast_task",
            queue_name="test",
        )

        executor = TaskExecutor()

        # Claim and execute job
        claimed = executor.claim_job("test")
        assert claimed is not None

        # Execute the job
        result = executor.execute_job(claimed)

        # Simulate partial failure: job executed but status update lost
        # Force job back to "running" state as if update failed
        job.refresh_from_db()
        if job.status == "completed":
            job.status = "running"
            job.updated_at = timezone.now() - timedelta(minutes=10)  # Make it stale
            job.save()

        # Job appears to be running but actually completed
        # This is a dangerous state - job might be re-executed

        # Orphan detection should catch this
        stale_threshold = timezone.now() - timedelta(minutes=5)
        stale_jobs = QueuedJob.objects.filter(
            status="running",
            updated_at__lt=stale_threshold
        )

        assert stale_jobs.count() == 1

        # System should handle this by either:
        # 1. Re-executing (safe if job is idempotent)
        # 2. Marking as failed with clear error
        # 3. Logging for manual investigation

    def test_slow_database_query(self):
        """Test: Database queries are slow, simulating connection issues.

        Expected: Operations should timeout gracefully, not hang forever.
        """
        # This test would need to mock database connection with delays
        # For now, just test that claiming many jobs doesn't hang

        # Create 100 jobs
        jobs = []
        for i in range(100):
            job = QueuedJob.objects.create(
                task_path="tests.chaos.test_worker_chaos.fast_task",
                queue_name="test",
            )
            jobs.append(job)

        executor = TaskExecutor()

        # Claim jobs one by one - should not hang
        claimed_count = 0
        start_time = time.time()

        for _ in range(100):
            claimed = executor.claim_job("test")
            if claimed:
                claimed_count += 1
            if time.time() - start_time > 10:  # 10 second timeout
                break

        # Should have claimed at least some jobs within timeout
        assert claimed_count > 0
        assert time.time() - start_time < 10


@pytest.mark.django_db
class TestResourceExhaustionChaos:
    """Tests that simulate resource exhaustion."""

    @pytest.mark.timeout(10)
    def test_memory_hog_job(self):
        """Test: Job tries to allocate excessive memory.

        Expected: Job should either complete or fail, not crash entire system.
        """
        job = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.memory_hog_task",
            queue_name="test",
        )

        executor = TaskExecutor()
        claimed = executor.claim_job("test")
        assert claimed is not None

        # Execute - might fail due to memory, but shouldn't hang
        try:
            result = executor.execute_job(claimed)
            # If it succeeds, job should be completed
            job.refresh_from_db()
            assert job.status in ["completed", "failed"]
        except MemoryError:
            # This is an acceptable failure mode
            job.refresh_from_db()
            assert job.status in ["failed", "running"]

    def test_connection_pool_exhaustion(self):
        """Test: Create more jobs than database connection pool size.

        Expected: Should handle gracefully with queuing or clear errors.
        """
        # Create many jobs very quickly
        jobs = []
        for i in range(200):
            try:
                job = QueuedJob.objects.create(
                    task_path="tests.chaos.test_worker_chaos.fast_task",
                    queue_name=f"test-{i % 10}",
                )
                jobs.append(job)
            except Exception as e:
                # Connection pool exhaustion should give clear error
                assert "connection" in str(e).lower() or "pool" in str(e).lower()
                break

        # Should have created at least some jobs
        assert len(jobs) > 0

        # All created jobs should be retrievable
        assert QueuedJob.objects.filter(id__in=[j.id for j in jobs]).count() == len(jobs)


@pytest.mark.django_db
class TestStateCorruptionChaos:
    """Tests that simulate corrupted state."""

    def test_invalid_status_transition(self):
        """Test: Job has invalid status value in database.

        Expected: System should detect and handle invalid states gracefully.
        """
        job = QueuedJob.objects.create(
            task_path="tests.chaos.test_worker_chaos.fast_task",
            queue_name="test",
        )

        # Manually corrupt the status in database
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sqlery_queuedjob SET status = %s WHERE id = %s",
                ["INVALID_STATUS", job.id]
            )

        # Try to claim jobs - should handle invalid state
        executor = TaskExecutor()

        # System should either:
        # 1. Skip invalid jobs
        # 2. Log error and continue
        # 3. Raise clear error (not crash)
        try:
            claimed = executor.claim_job("test")
            # If it claims a job, shouldn't be the corrupted one
            if claimed:
                assert claimed.id != job.id
        except Exception as e:
            # Should be a clear error about invalid state
            assert "status" in str(e).lower() or "invalid" in str(e).lower()

    def test_missing_task_path(self):
        """Test: Job has None or empty task_path.

        Expected: Should fail gracefully with clear error message.
        """
        # Try to create job with invalid task_path
        try:
            job = QueuedJob.objects.create(
                task_path="",  # Empty task path
                queue_name="test",
            )

            executor = TaskExecutor()
            claimed = executor.claim_job("test")

            if claimed and claimed.id == job.id:
                # Try to execute - should fail clearly
                result = executor.execute_job(claimed)
                job.refresh_from_db()
                assert job.status == "failed"
                assert "task_path" in (job.error_message or "").lower()

        except Exception as e:
            # Should fail at creation or execution with clear error
            assert "task" in str(e).lower() or "path" in str(e).lower()
