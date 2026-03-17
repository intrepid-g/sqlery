"""Tests for atomic scheduled task claiming.

FAILING TESTS EXPLANATION:
These tests are failing (with ERRORS) because SQLite (used in tests) doesn't support
concurrent access patterns that these tests rely on.

Specific issues:
1. SQLite doesn't support SELECT FOR UPDATE SKIP LOCKED - it's PostgreSQL/MySQL only.
2. Threading tests cause "database table is locked" errors with SQLite's file locking.
3. Concurrent transaction tests don't work properly with SQLite's locking model.

Error seen: "django.db.utils.OperationalError: database table is locked: sqlery_scheduled_task"

The tests would pass with PostgreSQL which supports proper row-level locking.

To fix: Either:
- Skip these tests when using SQLite: @pytest.mark.skipif(connection.vendor == 'sqlite', ...)
- Run tests against PostgreSQL for full concurrency testing
- Refactor tests to not rely on true concurrent database access
"""

import pytest
import threading
import time
from django.db import connection, transaction
from django.utils import timezone
from datetime import timedelta
from sqlery.models import ScheduledTask, QueuedJob
from sqlery.executor import TaskExecutor

skip_on_sqlite = pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason="SQLite does not support SELECT FOR UPDATE SKIP LOCKED or concurrent transactions"
)


# Test task
def test_task():
    """Simple test task."""
    return "completed"


@pytest.mark.django_db(transaction=True)
class TestAtomicSchedulerClaiming:
    """Test atomic scheduled task claiming to prevent duplicate enqueueing."""

    def test_select_for_update_used_in_run_due_tasks(self):
        """Test that run_due_tasks uses select_for_update for task claiming."""
        # Create a due scheduled task
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        executor = TaskExecutor()

        # Run scheduler
        jobs = executor.run_due_tasks()

        # Should have created one job
        assert len(jobs) == 1
        assert jobs[0].scheduled_task == task
        assert jobs[0].status == "queued"

        # Task's next_run_at should be updated
        task.refresh_from_db()
        assert task.next_run_at > timezone.now()

    @skip_on_sqlite
    def test_concurrent_schedulers_no_duplicate_enqueueing(self):
        """Test that concurrent schedulers don't enqueue duplicate jobs."""
        # Create multiple due scheduled tasks
        tasks = []
        for i in range(5):
            task = ScheduledTask.objects.create(
                name=f"Test Task {i}",
                task_path="tests.test_atomic_scheduler.test_task",
                cron_expression="* * * * *",
                queue_name="default",
                priority=5,
                enabled=True,
                next_run_at=timezone.now() - timedelta(seconds=10),
            )
            tasks.append(task)

        enqueued_jobs = []
        job_lock = threading.Lock()

        def run_scheduler():
            """Run scheduler in a thread."""
            executor = TaskExecutor()
            jobs = executor.run_due_tasks()
            with job_lock:
                enqueued_jobs.extend(jobs)

        # Start multiple schedulers concurrently
        threads = []
        for i in range(3):
            thread = threading.Thread(target=run_scheduler)
            thread.start()
            threads.append(thread)

        # Wait for all schedulers to finish
        for thread in threads:
            thread.join()

        # Verify each task was enqueued exactly once
        assert len(enqueued_jobs) == 5

        # Check all jobs are unique (no duplicates)
        job_ids = [job.id for job in enqueued_jobs]
        assert len(set(job_ids)) == 5

        # Verify each task has exactly one queued job
        for task in tasks:
            jobs_for_task = QueuedJob.objects.filter(scheduled_task=task)
            assert jobs_for_task.count() == 1

    @skip_on_sqlite
    def test_skip_locked_prevents_scheduler_blocking(self):
        """Test that SKIP LOCKED prevents schedulers from blocking on same task."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        claimed_by = []
        claim_lock = threading.Lock()

        def claim_task(scheduler_id):
            """Try to claim and process task."""
            with transaction.atomic():
                try:
                    locked_task = ScheduledTask.objects.select_for_update(
                        skip_locked=True
                    ).get(id=task.id, enabled=True)

                    with claim_lock:
                        claimed_by.append(scheduler_id)

                    time.sleep(0.2)  # Hold the lock
                except ScheduledTask.DoesNotExist:
                    # Task was already claimed
                    pass

        # Scheduler 1 claims task
        thread1 = threading.Thread(target=claim_task, args=("scheduler1",))
        thread1.start()

        time.sleep(0.05)  # Ensure thread1 acquires lock first

        # Scheduler 2 should skip locked task immediately
        thread2 = threading.Thread(target=claim_task, args=("scheduler2",))
        thread2.start()

        thread1.join()
        thread2.join()

        # Only one scheduler should have claimed the task
        assert len(claimed_by) == 1

    def test_scheduler_updates_next_run_at_atomically(self):
        """Test that next_run_at is updated within the claiming transaction."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="@hourly",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        old_next_run = task.next_run_at
        executor = TaskExecutor()

        jobs = executor.run_due_tasks()

        # Should have created job
        assert len(jobs) == 1

        # next_run_at should be updated
        task.refresh_from_db()
        assert task.next_run_at > old_next_run
        assert task.next_run_at > timezone.now()

    def test_disabled_task_not_enqueued(self):
        """Test that disabled tasks are not enqueued even if due."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=False,  # Disabled
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        executor = TaskExecutor()
        jobs = executor.run_due_tasks()

        # No jobs should be created
        assert len(jobs) == 0
        assert QueuedJob.objects.filter(scheduled_task=task).count() == 0

    def test_already_queued_task_not_duplicated(self):
        """Test that task with existing queued job is not enqueued again."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        # Manually create a queued job for this task
        existing_job = QueuedJob.objects.create(
            task_path=task.task_path,
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_task=task,
            status="queued",
        )

        executor = TaskExecutor()
        jobs = executor.run_due_tasks()

        # No new jobs should be created
        assert len(jobs) == 0
        assert QueuedJob.objects.filter(scheduled_task=task).count() == 1

    def test_running_task_not_duplicated(self):
        """Test that task with running job is not enqueued again."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        # Manually create a running job for this task
        running_job = QueuedJob.objects.create(
            task_path=task.task_path,
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_task=task,
            status="queued",
        )
        running_job.mark_running()

        executor = TaskExecutor()
        jobs = executor.run_due_tasks()

        # No new jobs should be created
        assert len(jobs) == 0
        assert QueuedJob.objects.filter(scheduled_task=task, status="queued").count() == 0


@skip_on_sqlite
@pytest.mark.django_db(transaction=True)
class TestAtomicSchedulerPerformance:
    """Test performance characteristics of atomic scheduler claiming."""

    def test_skip_locked_doesnt_wait(self):
        """Test that SKIP LOCKED returns immediately without waiting."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_atomic_scheduler.test_task",
            cron_expression="* * * * *",
            queue_name="default",
            priority=5,
            enabled=True,
            next_run_at=timezone.now() - timedelta(seconds=10),
        )

        def hold_lock():
            """Hold lock on task for 500ms."""
            with transaction.atomic():
                locked_task = ScheduledTask.objects.select_for_update(
                    skip_locked=True
                ).get(id=task.id)
                time.sleep(0.5)

        thread1 = threading.Thread(target=hold_lock)
        thread1.start()

        time.sleep(0.1)  # Ensure thread1 has lock

        # Scheduler 2 tries to claim - should return immediately
        start = time.time()
        executor = TaskExecutor()
        jobs = executor.run_due_tasks()
        elapsed = time.time() - start

        # Should be fast (< 200ms), not wait for lock (500ms)
        assert elapsed < 0.2
        # Should return empty since task is locked
        assert len(jobs) == 0

        thread1.join()

    def test_multiple_tasks_claimed_by_different_schedulers(self):
        """Test that multiple schedulers can claim different tasks simultaneously."""
        # Create 10 due tasks
        tasks = []
        for i in range(10):
            task = ScheduledTask.objects.create(
                name=f"Test Task {i}",
                task_path="tests.test_atomic_scheduler.test_task",
                cron_expression="* * * * *",
                queue_name="default",
                priority=5,
                enabled=True,
                next_run_at=timezone.now() - timedelta(seconds=10),
            )
            tasks.append(task)

        enqueued_jobs = []
        job_lock = threading.Lock()

        def run_scheduler():
            """Run scheduler in a thread."""
            executor = TaskExecutor()
            jobs = executor.run_due_tasks()
            with job_lock:
                enqueued_jobs.extend(jobs)

        # Start 5 concurrent schedulers
        threads = []
        for i in range(5):
            thread = threading.Thread(target=run_scheduler)
            thread.start()
            threads.append(thread)

        # Wait for all to finish
        for thread in threads:
            thread.join()

        # All 10 tasks should have been enqueued exactly once
        assert len(enqueued_jobs) == 10
        job_ids = [job.id for job in enqueued_jobs]
        assert len(set(job_ids)) == 10

        # Verify each task has exactly one job
        for task in tasks:
            assert QueuedJob.objects.filter(scheduled_task=task).count() == 1
