"""Tests for public API functions (enqueue, enqueue_at)."""

import pytest
from datetime import timedelta
from django.utils import timezone
from sqlery import enqueue, enqueue_at
from sqlery.models import QueuedJob


@pytest.mark.django_db
class TestEnqueueAPI:
    """Test enqueue() API function."""

    def test_enqueue_creates_queued_job(self):
        """Test basic enqueueing."""
        job = enqueue("myapp.tasks.test_task")

        assert job is not None
        assert isinstance(job, QueuedJob)
        assert job.status == "queued"
        assert job.task_path == "myapp.tasks.test_task"
        assert job.scheduled_at is None
        assert job.scheduled_task is None

    def test_enqueue_with_custom_queue(self):
        """Test enqueueing with custom queue name."""
        job = enqueue("myapp.tasks.test_task", queue="email")

        assert job.queue_name == "email"

    def test_enqueue_with_custom_priority(self):
        """Test enqueueing with custom priority."""
        job = enqueue("myapp.tasks.test_task", priority=10)

        assert job.priority == 10

    def test_enqueue_uses_default_queue(self, settings):
        """Test that default queue is used when not specified."""
        settings.DJANGO_SQL_JOBS = {
            "DEFAULT_QUEUE": "custom-default",
        }

        job = enqueue("myapp.tasks.test_task")

        assert job.queue_name == "custom-default"

    def test_enqueue_uses_default_priority(self, settings):
        """Test that default priority is used when not specified."""
        settings.DJANGO_SQL_JOBS = {
            "DEFAULT_PRIORITY": 5,
        }

        job = enqueue("myapp.tasks.test_task")

        assert job.priority == 5

    def test_enqueue_returns_persisted_job(self):
        """Test that returned job is saved to database."""
        job = enqueue("myapp.tasks.test_task")

        # Should be persisted
        assert job.id is not None

        # Should be retrievable from database
        db_job = QueuedJob.objects.get(id=job.id)
        assert db_job.task_path == job.task_path


@pytest.mark.django_db
class TestEnqueueAtAPI:
    """Test enqueue_at() API function."""

    def test_enqueue_at_creates_scheduled_job(self):
        """Test scheduling job for specific time."""
        run_time = timezone.now() + timedelta(hours=2)

        job = enqueue_at("myapp.tasks.test_task", run_time)

        assert job is not None
        assert isinstance(job, QueuedJob)
        assert job.status == "queued"
        assert job.task_path == "myapp.tasks.test_task"
        assert job.scheduled_at is not None
        assert job.scheduled_task is None

    def test_enqueue_at_respects_datetime(self):
        """Test that scheduled_at is set correctly."""
        run_time = timezone.now() + timedelta(hours=2)

        job = enqueue_at("myapp.tasks.test_task", run_time)

        # Should be within 1 second (accounting for processing time)
        time_diff = abs((job.scheduled_at - run_time).total_seconds())
        assert time_diff < 1

    def test_enqueue_at_with_naive_datetime(self):
        """Test that naive datetime is converted to UTC."""
        from datetime import datetime

        # Naive datetime
        naive_time = datetime.now() + timedelta(hours=2)

        job = enqueue_at("myapp.tasks.test_task", naive_time)

        # Should be timezone-aware (UTC)
        assert job.scheduled_at.tzinfo is not None

    def test_enqueue_at_with_custom_queue(self):
        """Test scheduling with custom queue."""
        run_time = timezone.now() + timedelta(hours=2)

        job = enqueue_at("myapp.tasks.test_task", run_time, queue="email")

        assert job.queue_name == "email"

    def test_enqueue_at_with_custom_priority(self):
        """Test scheduling with custom priority."""
        run_time = timezone.now() + timedelta(hours=2)

        job = enqueue_at("myapp.tasks.test_task", run_time, priority=10)

        assert job.priority == 10

    def test_enqueue_at_uses_defaults(self, settings):
        """Test that defaults are used when not specified."""
        settings.DJANGO_SQL_JOBS = {
            "DEFAULT_QUEUE": "custom-queue",
            "DEFAULT_PRIORITY": 7,
        }

        run_time = timezone.now() + timedelta(hours=2)
        job = enqueue_at("myapp.tasks.test_task", run_time)

        assert job.queue_name == "custom-queue"
        assert job.priority == 7

    def test_enqueue_at_past_time(self):
        """Test scheduling job in the past (should still be queued)."""
        past_time = timezone.now() - timedelta(hours=1)

        job = enqueue_at("myapp.tasks.test_task", past_time)

        # Should be created with past scheduled_at
        assert job.scheduled_at < timezone.now()
        assert job.status == "queued"

    def test_enqueue_at_returns_persisted_job(self):
        """Test that returned job is saved to database."""
        run_time = timezone.now() + timedelta(hours=2)

        job = enqueue_at("myapp.tasks.test_task", run_time)

        # Should be persisted
        assert job.id is not None

        # Should be retrievable from database
        db_job = QueuedJob.objects.get(id=job.id)
        assert db_job.task_path == job.task_path
        assert db_job.scheduled_at == job.scheduled_at


@pytest.mark.django_db
class TestAPIIntegration:
    """Test API functions with actual job processing."""

    def test_enqueued_jobs_are_processable(self):
        """Test that jobs created via API can be processed."""
        from sqlery.executor import TaskExecutor

        # Enqueue a job
        job = enqueue("tests.test_executor.dummy_task")

        # Process it
        executor = TaskExecutor()
        processed = executor.run_queue_workers(once=True)

        assert len(processed) == 1
        assert processed[0].id == job.id
        assert processed[0].status == "success"

    def test_enqueue_at_jobs_wait_until_scheduled(self):
        """Test that enqueue_at jobs respect scheduled_at."""
        from sqlery.executor import TaskExecutor

        # Schedule for future
        future_time = timezone.now() + timedelta(hours=1)
        job_future = enqueue_at("tests.test_executor.dummy_task", future_time)

        # Enqueue immediate job
        job_now = enqueue("tests.test_executor.dummy_task")

        # Process queue
        executor = TaskExecutor()
        processed = executor.run_queue_workers(once=True)

        # Only immediate job should be processed
        assert len(processed) == 1
        assert processed[0].id == job_now.id

        # Future job should still be queued
        job_future.refresh_from_db()
        assert job_future.status == "queued"

    def test_multiple_enqueues_create_separate_jobs(self):
        """Test that multiple enqueues create separate job instances."""
        job1 = enqueue("myapp.tasks.test_task")
        job2 = enqueue("myapp.tasks.test_task")
        job3 = enqueue("myapp.tasks.test_task")

        # Should be separate instances
        assert job1.id != job2.id
        assert job2.id != job3.id

        # All should be in database
        assert QueuedJob.objects.count() == 3
