"""Tests for atomic job claiming with SELECT FOR UPDATE SKIP LOCKED.

FAILING TESTS EXPLANATION:
These tests are failing because SQLite (used in tests) doesn't support
SELECT FOR UPDATE SKIP LOCKED, which is a PostgreSQL/MySQL feature.

Specific issues:
1. SQLite doesn't support row-level locking - it uses file-level locking instead.
2. Threading tests may cause "database table is locked" errors with SQLite.
3. The `select_for_update_skip_locked` attribute works differently in SQLite.

The tests pass with PostgreSQL but fail with SQLite's in-memory database.

To fix: Either:
- Skip these tests when using SQLite: @pytest.mark.skipif(connection.vendor == 'sqlite', ...)
- Run tests against PostgreSQL
- Use Django's test isolation instead of threading for concurrency tests
"""

import pytest
import threading
import time
from django.db import connection, transaction
from sqlery.models import QueuedJob
from sqlery.executor import TaskExecutor

skip_on_sqlite = pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason="SQLite does not support SELECT FOR UPDATE SKIP LOCKED"
)


# Test task
def test_task():
    """Simple test task."""
    time.sleep(0.1)  # Simulate some work
    return "completed"


@pytest.mark.django_db(transaction=True)
class TestAtomicJobClaiming:
    """Test atomic job claiming to prevent duplicate execution."""

    @skip_on_sqlite
    def test_select_for_update_applied(self):
        """Test that get_queued_jobs uses select_for_update."""
        job = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )

        executor = TaskExecutor()

        # Get queued jobs - should use select_for_update
        with transaction.atomic():
            queryset = executor.get_queued_jobs()

            # Verify the queryset has select_for_update applied
            # Check the SQL query contains "FOR UPDATE"
            sql = str(queryset.query)
            # Django's select_for_update adds FOR UPDATE to the query
            # We can verify by checking the queryset's query attributes
            assert queryset.query.select_for_update is True
            assert queryset.query.select_for_update_skip_locked is True

    @skip_on_sqlite
    def test_skip_locked_prevents_blocking(self):
        """Test that SKIP LOCKED prevents workers from blocking on locked rows."""
        job1 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )

        executor = TaskExecutor()
        claimed_jobs = []

        def claim_job(worker_id):
            """Claim a job in a transaction."""
            with transaction.atomic():
                jobs = list(executor.get_queued_jobs(limit=1))
                if jobs:
                    job = jobs[0]
                    job.mark_running()
                    claimed_jobs.append((worker_id, job.id))
                    time.sleep(0.2)  # Hold the lock

        # Worker 1 claims job1
        thread1 = threading.Thread(target=claim_job, args=("worker1",))
        thread1.start()

        time.sleep(0.05)  # Ensure thread1 acquires lock first

        # Worker 2 should skip locked job1 and claim job2
        thread2 = threading.Thread(target=claim_job, args=("worker2",))
        thread2.start()

        thread1.join()
        thread2.join()

        # Both workers should have claimed different jobs
        assert len(claimed_jobs) == 2
        claimed_job_ids = [job_id for _, job_id in claimed_jobs]
        assert job1.id in claimed_job_ids
        assert job2.id in claimed_job_ids
        # Verify they're different
        assert claimed_job_ids[0] != claimed_job_ids[1]

    @skip_on_sqlite
    def test_concurrent_workers_no_duplicate_execution(self):
        """Test that concurrent workers don't execute the same job twice."""
        # Create multiple jobs
        jobs = [
            QueuedJob.objects.create(
                task_path="tests.test_atomic_claiming.test_task"
            )
            for _ in range(5)
        ]

        executed_jobs = []
        execution_lock = threading.Lock()

        def worker_process_jobs():
            """Worker that processes jobs."""
            executor = TaskExecutor()
            # Process jobs one at a time
            while True:
                with transaction.atomic():
                    queued_jobs = executor.get_queued_jobs(limit=1)
                    if not queued_jobs.exists():
                        break

                    job = queued_jobs.first()
                    job.mark_running()

                # Execute outside transaction
                job.refresh_from_db()
                with execution_lock:
                    executed_jobs.append(job.id)

                # Simulate execution
                time.sleep(0.01)
                job.mark_success("done")

        # Start multiple workers concurrently
        threads = []
        for i in range(3):
            thread = threading.Thread(target=worker_process_jobs)
            thread.start()
            threads.append(thread)

        # Wait for all workers to finish
        for thread in threads:
            thread.join()

        # Verify each job was executed exactly once
        assert len(executed_jobs) == 5
        assert len(set(executed_jobs)) == 5  # No duplicates

    def test_atomic_status_update_within_transaction(self):
        """Test that status update happens within the claiming transaction."""
        job = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )

        executor = TaskExecutor()

        # Claim job atomically
        with transaction.atomic():
            queued_jobs = executor.get_queued_jobs(limit=1)
            assert queued_jobs.exists()

            claimed_job = queued_jobs.first()
            assert claimed_job.status == "queued"

            # Mark as running
            claimed_job.mark_running()

            # Within transaction, status should be running
            assert claimed_job.status == "running"

        # After transaction commit, status should still be running
        job.refresh_from_db()
        assert job.status == "running"

    def test_execute_job_handles_already_running(self):
        """Test that execute_job handles jobs already marked as running."""
        job = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )

        # Manually mark as running (simulating atomic claim)
        job.mark_running()

        executor = TaskExecutor()

        # execute_job should handle already-running job
        result = executor.execute_job(job)

        # Job should complete successfully
        result.refresh_from_db()
        assert result.status == "success"
        assert result.output == "completed"

    def test_run_queue_workers_uses_atomic_claiming(self):
        """Test that run_queue_workers properly uses atomic claiming."""
        # Create multiple jobs
        for i in range(3):
            QueuedJob.objects.create(
                task_path="tests.test_atomic_claiming.test_task"
            )

        executor = TaskExecutor()

        # Run queue workers
        processed = executor.run_queue_workers(once=True)

        # All jobs should be processed
        assert len(processed) == 3

        # All should be successful
        for job in processed:
            job.refresh_from_db()
            assert job.status == "success"

    def test_direct_execute_job_still_works(self):
        """Test that direct execute_job calls (non-atomic) still work."""
        job = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )

        executor = TaskExecutor()

        # Direct call to execute_job (without atomic claiming)
        result = executor.execute_job(job)

        # Job should complete successfully
        result.refresh_from_db()
        assert result.status == "success"

    def test_can_execute_job_still_prevents_duplicate_task_execution(self):
        """Test that queue-level concurrency control works.

        can_execute_job checks queue-level concurrency (not task-level):
        if allow_parallel=False (default), no other job in the same queue
        can run while one is running.
        """
        # Create two jobs in the same queue
        job1 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task",
            queue_name="test-queue",
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task",
            queue_name="test-queue",
        )

        # Mark job1 as running
        job1.mark_running()

        executor = TaskExecutor()

        # can_execute_job should return False for job2 (same queue, allow_parallel=False)
        assert executor.can_execute_job(job2) is False

        # Job in a DIFFERENT queue should be allowed
        job3 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.different_task",
            queue_name="other-queue",
        )
        assert executor.can_execute_job(job3) is True


@pytest.mark.django_db(transaction=True)
class TestAtomicClaimingEdgeCases:
    """Test edge cases for atomic job claiming."""

    def test_empty_queue_doesnt_block(self):
        """Test that empty queue doesn't cause issues."""
        executor = TaskExecutor()

        # No jobs in queue
        with transaction.atomic():
            queued_jobs = executor.get_queued_jobs()
            assert not queued_jobs.exists()

    def test_multiple_queues_independent_claiming(self):
        """Test that jobs in different queues are claimed independently."""
        job1 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task",
            queue_name="queue1"
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task",
            queue_name="queue2"
        )

        executor = TaskExecutor()

        # Claim from queue1
        with transaction.atomic():
            jobs_q1 = executor.get_queued_jobs(queue_name="queue1")
            assert jobs_q1.count() == 1
            jobs_q1.first().mark_running()

        # Claim from queue2 should work independently
        with transaction.atomic():
            jobs_q2 = executor.get_queued_jobs(queue_name="queue2")
            assert jobs_q2.count() == 1
            jobs_q2.first().mark_running()

        # Both should be running
        job1.refresh_from_db()
        job2.refresh_from_db()
        assert job1.status == "running"
        assert job2.status == "running"

    def test_priority_ordering_with_atomic_claiming(self):
        """Test that jobs are still claimed in priority order."""
        job_low = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task",
            priority=1
        )
        job_high = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task",
            priority=10
        )

        executor = TaskExecutor()

        # Claim first job - should be high priority
        with transaction.atomic():
            queued_jobs = executor.get_queued_jobs(limit=1)
            claimed = queued_jobs.first()
            assert claimed.id == job_high.id
            claimed.mark_running()

        # Claim second job - should be low priority
        with transaction.atomic():
            queued_jobs = executor.get_queued_jobs(limit=1)
            claimed = queued_jobs.first()
            assert claimed.id == job_low.id


@skip_on_sqlite
@pytest.mark.django_db(transaction=True)
class TestAtomicClaimingPerformance:
    """Test performance characteristics of atomic claiming."""

    def test_skip_locked_doesnt_wait(self):
        """Test that SKIP LOCKED returns immediately without waiting."""
        import time

        job = QueuedJob.objects.create(
            task_path="tests.test_atomic_claiming.test_task"
        )

        executor = TaskExecutor()

        # Worker 1 locks the job
        def hold_lock():
            with transaction.atomic():
                jobs = list(executor.get_queued_jobs(limit=1))
                if jobs:
                    time.sleep(0.5)  # Hold lock for 500ms

        thread1 = threading.Thread(target=hold_lock)
        thread1.start()

        time.sleep(0.1)  # Ensure thread1 has lock

        # Worker 2 tries to get jobs - should return immediately (not wait)
        start = time.time()
        with transaction.atomic():
            jobs = list(executor.get_queued_jobs(limit=1))
        elapsed = time.time() - start

        # Should be fast (< 100ms), not wait for lock (500ms)
        assert elapsed < 0.2
        # Should return empty since job is locked
        assert len(jobs) == 0

        thread1.join()
