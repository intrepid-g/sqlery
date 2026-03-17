"""Tests for task executor (complements test_queue.py)."""

import pytest
from datetime import timedelta
from django.utils import timezone
from sqlery.executor import TaskExecutor
from sqlery.models import ScheduledTask, QueuedJob


# Dummy tasks for testing
def dummy_task():
    """A simple test task."""
    return "Success"


def failing_task():
    """A task that raises an error."""
    raise ValueError("Task failed")


@pytest.mark.django_db
class TestSchedulerMethods:
    """Test scheduler-specific methods."""

    def test_get_due_tasks_finds_due_tasks(self):
        """Test finding due tasks."""
        executor = TaskExecutor()

        # Create task that's due
        now = timezone.now()
        task1 = ScheduledTask.objects.create(
            name="Due Task",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            next_run_at=now,
        )

        # Create task that's not due
        future = now + timedelta(hours=1)
        task2 = ScheduledTask.objects.create(
            name="Future Task",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            next_run_at=future,
        )

        due_tasks = executor.get_due_tasks()
        assert task1 in due_tasks
        assert task2 not in due_tasks

    def test_get_due_tasks_ignores_disabled(self):
        """Test that disabled tasks are not returned."""
        executor = TaskExecutor()

        now = timezone.now()
        task = ScheduledTask.objects.create(
            name="Disabled Task",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            next_run_at=now,
            enabled=False,
        )

        due_tasks = executor.get_due_tasks()
        assert task not in due_tasks

    def test_run_due_tasks_enqueues_jobs(self):
        """Test that due tasks create queued jobs."""
        executor = TaskExecutor()

        task = ScheduledTask.objects.create(
            name="Test",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            next_run_at=timezone.now(),
        )

        jobs = executor.run_due_tasks()

        assert len(jobs) == 1
        assert jobs[0].scheduled_task == task
        assert jobs[0].task_path == task.task_path
        assert jobs[0].status == "queued"

    def test_run_due_tasks_updates_next_run(self):
        """Test that next_run_at is updated after enqueueing."""
        executor = TaskExecutor()

        task = ScheduledTask.objects.create(
            name="Test",
            task_path="tests.test_executor.dummy_task",
            cron_expression="0 * * * *",  # Hourly
            next_run_at=timezone.now(),
        )

        initial_next_run = task.next_run_at

        executor.run_due_tasks()

        task.refresh_from_db()
        assert task.next_run_at > initial_next_run

    def test_enqueue_for_scheduled_task_prevents_duplicates(self):
        """Test that duplicate jobs are not created."""
        executor = TaskExecutor()

        task = ScheduledTask.objects.create(
            name="Test",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            next_run_at=timezone.now(),
        )

        # First enqueue
        job1 = executor._enqueue_for_scheduled_task(task)
        assert job1 is not None

        # Second enqueue should be skipped (job still queued)
        job2 = executor._enqueue_for_scheduled_task(task)
        assert job2 is None

        # Total jobs should be 1
        assert QueuedJob.objects.filter(scheduled_task=task).count() == 1


@pytest.mark.django_db
class TestWorkerMethods:
    """Test worker-specific methods."""

    def test_get_queued_jobs_filters_by_status(self):
        """Test that only queued jobs are returned."""
        executor = TaskExecutor()

        # Create jobs with different statuses
        job1 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="queued",
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="running",
        )
        job3 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="success",
        )

        queued = executor.get_queued_jobs()

        assert job1 in queued
        assert job2 not in queued
        assert job3 not in queued

    def test_get_queued_jobs_respects_queue_filter(self):
        """Test filtering by queue name."""
        executor = TaskExecutor()

        job_email = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="email",
        )
        job_default = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="default",
        )

        # Get only email queue
        email_jobs = executor.get_queued_jobs(queue_name="email")

        assert job_email in email_jobs
        assert job_default not in email_jobs

    def test_get_queued_jobs_respects_limit(self):
        """Test limit parameter."""
        executor = TaskExecutor()

        # Create 5 jobs
        for _ in range(5):
            QueuedJob.objects.create(
                task_path="tests.test_executor.dummy_task",
            )

        # Get only 3
        jobs = executor.get_queued_jobs(limit=3)

        assert len(jobs) == 3

    def test_get_queued_jobs_orders_by_priority(self):
        """Test jobs are ordered by priority desc, created_at asc."""
        executor = TaskExecutor()

        job_low = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            priority=1,
        )
        job_high = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            priority=10,
        )

        jobs = list(executor.get_queued_jobs())

        assert jobs[0].id == job_high.id
        assert jobs[1].id == job_low.id

    def test_can_execute_job_checks_concurrency(self):
        """Test that concurrent executions are prevented."""
        executor = TaskExecutor()

        job1 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
        )

        # Should be able to execute initially
        assert executor.can_execute_job(job1) is True

        # Mark job1 as running
        job1.mark_running()

        # Should not be able to execute job2 (same task_path)
        assert executor.can_execute_job(job2) is False

    def test_execute_job_success_flow(self):
        """Test successful job execution flow."""
        executor = TaskExecutor()

        job = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
        )

        result = executor.execute_job(job)

        assert result.status == "success"
        assert result.output == "Success"
        assert result.started_at is not None
        assert result.finished_at is not None

    def test_execute_job_failure_flow(self):
        """Test failed job execution flow."""
        executor = TaskExecutor()

        job = QueuedJob.objects.create(
            task_path="tests.test_executor.failing_task",
        )

        result = executor.execute_job(job)

        assert result.status == "failed"
        assert "Task failed" in result.error
        assert result.traceback != ""

    def test_execute_job_updates_scheduled_task_last_run(self):
        """Test that scheduled task last_run_at is updated."""
        executor = TaskExecutor()

        task = ScheduledTask.objects.create(
            name="Test",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
        )

        job = QueuedJob.objects.create(
            task_path=task.task_path,
            scheduled_task=task,
        )

        executor.execute_job(job)

        task.refresh_from_db()
        assert task.last_run_at is not None
        assert task.last_run_at == job.started_at

    def test_run_queue_workers_processes_jobs(self):
        """Test that workers process jobs."""
        executor = TaskExecutor()

        # Create multiple jobs
        for _ in range(3):
            QueuedJob.objects.create(
                task_path="tests.test_executor.dummy_task",
            )

        processed = executor.run_queue_workers(once=True)

        assert len(processed) == 3
        for job in processed:
            assert job.status == "success"

    def test_run_queue_workers_respects_max_jobs(self):
        """Test max_jobs parameter."""
        executor = TaskExecutor()

        # Create 10 jobs
        for _ in range(10):
            QueuedJob.objects.create(
                task_path="tests.test_executor.dummy_task",
            )

        # Process only 5
        processed = executor.run_queue_workers(max_jobs=5, once=True)

        assert len(processed) == 5

        # 5 should still be queued
        queued = QueuedJob.objects.filter(status="queued").count()
        assert queued == 5
