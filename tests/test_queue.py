"""Comprehensive tests for queue processing.

FAILING TESTS EXPLANATION:
1. test_next_run_updated_after_enqueue:
   The test expects `next_run_at` to be updated by approximately 1 hour (3600 seconds)
   after running a scheduled task with cron "0 * * * *" (every hour at minute 0).

   The test sets `next_run_at` to "now" and expects it to update to ~1 hour in the future.
   However, the actual update shows only ~95 seconds difference.

   Root cause: The model's save() method recalculates `next_run_at` based on the current
   time when saved, not based on the previous `next_run_at`. So if the test runs at
   11:58, the next run would be 12:00 (2 minutes away), not 1 hour away.

   The cron library calculates "next occurrence from NOW" not "next occurrence from
   previous scheduled time". This is correct behavior for a scheduler, but the test
   assertion is wrong.

To fix: Update the test to check that next_run_at is in the future and matches the
cron expression, rather than checking for a specific time delta.
"""

import pytest
from datetime import datetime, timezone, timedelta
from django.utils import timezone as django_timezone
from sqlery.models import ScheduledTask, QueuedJob
from sqlery.executor import TaskExecutor
from sqlery import enqueue, enqueue_at


# Test tasks
def success_task():
    """A task that succeeds."""
    return "Success"


def failing_task():
    """A task that fails."""
    raise ValueError("Task failed")


def slow_task():
    """A slow task."""
    import time

    time.sleep(0.1)
    return "Done"


@pytest.mark.django_db
class TestQueueProcessing:
    """Test that queued jobs are processed correctly."""

    def test_jobs_processed_in_priority_order(self):
        """Jobs with higher priority should be processed first."""
        executor = TaskExecutor()

        # Enqueue jobs with different priorities
        job_low = enqueue("tests.test_queue.success_task", priority=1)
        job_high = enqueue("tests.test_queue.success_task", priority=10)
        job_medium = enqueue("tests.test_queue.success_task", priority=5)

        # Process queue
        processed = executor.run_queue_workers(once=True)

        # Check order: high (10), medium (5), low (1)
        assert len(processed) == 3
        assert processed[0].id == job_high.id
        assert processed[1].id == job_medium.id
        assert processed[2].id == job_low.id

    def test_queue_filtering_works(self):
        """Workers should only process jobs from specified queue."""
        executor = TaskExecutor()

        # Enqueue to different queues
        job_email = enqueue("tests.test_queue.success_task", queue="email")
        job_default = enqueue("tests.test_queue.success_task", queue="default")
        job_reports = enqueue("tests.test_queue.success_task", queue="reports")

        # Process only email queue
        processed = executor.run_queue_workers(queue_name="email", once=True)

        # Should only process email job
        assert len(processed) == 1
        assert processed[0].id == job_email.id

        # Other jobs still queued
        job_default.refresh_from_db()
        job_reports.refresh_from_db()
        assert job_default.status == "queued"
        assert job_reports.status == "queued"

    def test_concurrency_control_prevents_duplicates(self):
        """Same task should not run concurrently."""
        executor = TaskExecutor()

        # Create two jobs with same task_path
        job1 = enqueue("tests.test_queue.slow_task")
        job2 = enqueue("tests.test_queue.slow_task")

        # Mark first job as running
        job1.mark_running()

        # Try to execute second job
        result = executor.execute_job(job2)

        # Second job should be skipped (still queued)
        assert result.status == "queued"  # Not executed

    def test_status_transitions_are_correct(self):
        """Job status should transition: queued → running → success/failed."""
        executor = TaskExecutor()

        job = enqueue("tests.test_queue.success_task")

        # Initial state
        assert job.status == "queued"
        assert job.started_at is None
        assert job.finished_at is None

        # Execute
        executor.execute_job(job)
        job.refresh_from_db()

        # Final state
        assert job.status == "success"
        assert job.started_at is not None
        assert job.finished_at is not None
        assert job.duration_seconds is not None
        assert job.duration_seconds > 0

    def test_jobs_respect_scheduled_at(self):
        """Jobs with future scheduled_at should not be processed yet."""
        executor = TaskExecutor()

        # Enqueue job for future
        future_time = django_timezone.now() + timedelta(hours=1)
        job_future = enqueue_at("tests.test_queue.success_task", future_time)

        # Enqueue immediate job
        job_now = enqueue("tests.test_queue.success_task")

        # Process queue
        processed = executor.run_queue_workers(once=True)

        # Only immediate job should be processed
        assert len(processed) == 1
        assert processed[0].id == job_now.id

        # Future job still queued
        job_future.refresh_from_db()
        assert job_future.status == "queued"


@pytest.mark.django_db
class TestFailureHandling:
    """Test that failures are handled correctly."""

    def test_failed_jobs_captured_with_traceback(self):
        """Failed jobs should capture error and traceback."""
        executor = TaskExecutor()

        job = enqueue("tests.test_queue.failing_task")
        executor.execute_job(job)

        job.refresh_from_db()

        assert job.status == "failed"
        assert "Task failed" in job.error
        assert job.traceback != ""
        assert "ValueError" in job.traceback

    def test_failed_jobs_dont_block_queue(self):
        """Failed jobs should not prevent other jobs from running."""
        executor = TaskExecutor()

        # Enqueue: fail, success, fail
        job1 = enqueue("tests.test_queue.failing_task")
        job2 = enqueue("tests.test_queue.success_task")
        job3 = enqueue("tests.test_queue.failing_task")

        # Process all
        processed = executor.run_queue_workers(once=True)

        # All should be processed
        assert len(processed) == 3

        job1.refresh_from_db()
        job2.refresh_from_db()
        job3.refresh_from_db()

        assert job1.status == "failed"
        assert job2.status == "success"
        assert job3.status == "failed"

    def test_task_import_error_handled(self):
        """Jobs with invalid task paths should fail gracefully."""
        executor = TaskExecutor()

        job = enqueue("nonexistent.module.task")
        executor.execute_job(job)

        job.refresh_from_db()

        assert job.status == "failed"
        assert "Cannot import task" in job.error or "ImportError" in job.traceback


@pytest.mark.django_db
class TestTiming:
    """Test that timing and scheduling work correctly."""

    def test_scheduled_task_enqueues_at_correct_time(self):
        """Scheduled tasks should enqueue when next_run_at is reached."""
        executor = TaskExecutor()

        # Create task that's due now
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_queue.success_task",
            cron_expression="* * * * *",
            next_run_at=django_timezone.now() - timedelta(minutes=1),  # Due
        )

        # Run scheduler
        jobs = executor.run_due_tasks()

        # Should create one job
        assert len(jobs) == 1
        assert jobs[0].scheduled_task == task
        assert jobs[0].task_path == task.task_path

    def test_enqueue_at_respects_datetime(self):
        """enqueue_at should create job with correct scheduled_at."""
        run_time = django_timezone.now() + timedelta(hours=2)
        job = enqueue_at("tests.test_queue.success_task", run_time)

        assert job.scheduled_at is not None
        # Should be within 1 second (accounting for processing time)
        time_diff = abs((job.scheduled_at - run_time).total_seconds())
        assert time_diff < 1

    def test_cron_next_run_calculated_correctly(self):
        """Cron next_run_at should be calculated correctly."""
        # Create task with daily cron (2 AM)
        task = ScheduledTask.objects.create(
            name="Daily Task",
            task_path="tests.test_queue.success_task",
            cron_expression="0 2 * * *",
        )

        # next_run_at should be set automatically
        assert task.next_run_at is not None
        assert task.next_run_at.hour == 2
        assert task.next_run_at.minute == 0

    def test_next_run_updated_after_enqueue(self):
        """next_run_at should update after task is enqueued."""
        executor = TaskExecutor()

        # Create task
        task = ScheduledTask.objects.create(
            name="Hourly Task",
            task_path="tests.test_queue.success_task",
            cron_expression="0 * * * *",  # Every hour
            next_run_at=django_timezone.now(),  # Due now
        )

        initial_next_run = task.next_run_at

        # Enqueue
        executor.run_due_tasks()

        # Reload task
        task.refresh_from_db()

        # next_run_at should be updated to the future (next hour boundary from NOW,
        # not from the previous next_run_at, so the delta depends on current minute)
        assert task.next_run_at > initial_next_run
        # The cron "0 * * * *" calculates next occurrence from now, which is
        # anywhere from 1 to 60 minutes away depending on current time
        time_from_now = (task.next_run_at - django_timezone.now()).total_seconds()
        assert 0 < time_from_now <= 3700  # Should be in the future, at most ~1 hour

    def test_job_duration_measured(self):
        """Job duration should be measured accurately."""
        executor = TaskExecutor()

        job = enqueue("tests.test_queue.slow_task")
        executor.execute_job(job)

        job.refresh_from_db()

        assert job.duration_seconds is not None
        assert job.duration_seconds >= 0.1  # Task sleeps for 0.1s


@pytest.mark.django_db
class TestScheduledTaskIntegration:
    """Test full scheduled task flow."""

    def test_scheduled_task_to_job_flow(self):
        """Test: ScheduledTask → enqueue → process → success."""
        executor = TaskExecutor()

        # Create scheduled task
        task = ScheduledTask.objects.create(
            name="Integration Test",
            task_path="tests.test_queue.success_task",
            cron_expression="* * * * *",
            queue_name="test-queue",
            priority=5,
            next_run_at=django_timezone.now(),  # Due now
        )

        # Step 1: Scheduler enqueues job
        jobs = executor.run_due_tasks()
        assert len(jobs) == 1

        job = jobs[0]
        assert job.status == "queued"
        assert job.queue_name == "test-queue"
        assert job.priority == 5
        assert job.scheduled_task == task

        # Step 2: Worker processes job
        processed = executor.run_queue_workers(queue_name="test-queue", once=True)
        assert len(processed) == 1

        job.refresh_from_db()
        assert job.status == "success"
        assert job.output == "Success"

        # Step 3: Task updated
        task.refresh_from_db()
        assert task.last_run_at is not None

    def test_scheduled_task_prevents_duplicate_enqueue(self):
        """Scheduled task shouldn't enqueue if already queued."""
        executor = TaskExecutor()

        task = ScheduledTask.objects.create(
            name="Test",
            task_path="tests.test_queue.success_task",
            cron_expression="* * * * *",
            next_run_at=django_timezone.now(),
        )

        # Enqueue twice
        jobs1 = executor.run_due_tasks()
        jobs2 = executor.run_due_tasks()

        # Should only create one job
        assert len(jobs1) == 1
        assert len(jobs2) == 0  # Already queued


@pytest.mark.django_db
class TestManualEnqueue:
    """Test manual job enqueueing."""

    def test_enqueue_creates_immediate_job(self):
        """enqueue() should create job with no scheduled_at."""
        job = enqueue("tests.test_queue.success_task")

        assert job.status == "queued"
        assert job.scheduled_at is None
        assert job.scheduled_task is None

    def test_enqueue_respects_queue_and_priority(self):
        """enqueue() should respect queue and priority params."""
        job = enqueue("tests.test_queue.success_task", queue="email", priority=10)

        assert job.queue_name == "email"
        assert job.priority == 10

    def test_enqueue_at_creates_scheduled_job(self):
        """enqueue_at() should create job with scheduled_at."""
        run_time = django_timezone.now() + timedelta(minutes=30)
        job = enqueue_at("tests.test_queue.success_task", run_time)

        assert job.status == "queued"
        assert job.scheduled_at is not None
        assert job.scheduled_task is None


@pytest.mark.django_db
class TestWorkerBehavior:
    """Test worker behavior under various conditions."""

    def test_worker_processes_multiple_jobs(self):
        """Worker should process all queued jobs."""
        executor = TaskExecutor()

        # Enqueue multiple
        for _ in range(5):
            enqueue("tests.test_queue.success_task")

        # Process
        processed = executor.run_queue_workers(once=True)

        assert len(processed) == 5
        for job in processed:
            job.refresh_from_db()
            assert job.status == "success"

    def test_worker_respects_max_jobs_limit(self):
        """Worker should stop at max_jobs limit."""
        executor = TaskExecutor()

        # Enqueue 10 jobs
        for _ in range(10):
            enqueue("tests.test_queue.success_task")

        # Process with limit
        processed = executor.run_queue_workers(max_jobs=5, once=True)

        # Should only process 5
        assert len(processed) == 5

        # 5 should still be queued
        queued_count = QueuedJob.objects.filter(status="queued").count()
        assert queued_count == 5

    def test_worker_stops_when_queue_empty(self):
        """Worker should exit when no jobs queued."""
        executor = TaskExecutor()

        # No jobs
        processed = executor.run_queue_workers(once=True)

        # Should return empty list
        assert len(processed) == 0
