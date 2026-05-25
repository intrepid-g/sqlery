"""Tests for sqlery models.

FAILING TESTS EXPLANATION:
Two tests in TestScheduledTaskRecomputation are failing:

1. test_enabling_task_recalculates_next_run:
   The test expects that when a disabled task is enabled, `next_run_at` should be
   recalculated to a NEW value. However, if the cron expression already produces
   the same next run time (e.g., "0 * * * *" with next run at top of next hour),
   the value won't change even after recalculation.

2. test_disabled_then_cron_change_then_enable:
   Similar issue - the test expects `next_run_at` to change when re-enabling,
   but the cron calculation may produce the same result if the current time
   hasn't advanced enough.

Root cause: The tests assume recalculation always produces a DIFFERENT value,
but cron expression calculation is deterministic - the same expression at
similar times produces similar results.

The model's save() method likely calculates next_run_at correctly, but the
test assertions are too strict. The tests should verify that:
- next_run_at is in the future (not necessarily different)
- The calculation logic was invoked (via mocking or checking updated_at)
"""

import pytest
from datetime import timedelta
from django.utils import timezone
from sqlery.models import ScheduledTask, QueuedJob


@pytest.mark.django_db
class TestScheduledTask:
    """Test ScheduledTask model."""

    def test_scheduled_task_creation(self):
        """Test creating a scheduled task."""
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.tasks.dummy_task",
            cron_expression="0 0 * * *",
        )
        assert task.next_run_at is not None
        assert task.enabled is True
        assert task.queue_name == "default"
        assert task.priority == 0
        assert str(task) == "Test Task"

    def test_scheduled_task_with_custom_queue_and_priority(self):
        """Test creating task with custom queue and priority."""
        task = ScheduledTask.objects.create(
            name="Email Task",
            task_path="myapp.tasks.send_email",
            cron_expression="0 9 * * *",
            queue_name="email",
            priority=10,
        )
        assert task.queue_name == "email"
        assert task.priority == 10

    def test_scheduled_task_updates_next_run_at_on_save(self):
        """Test that next_run_at is calculated on save."""
        task = ScheduledTask.objects.create(
            name="Hourly Task",
            task_path="myapp.tasks.hourly",
            cron_expression="0 * * * *",  # Every hour
        )

        initial_next_run = task.next_run_at
        assert initial_next_run is not None
        assert initial_next_run.minute == 0

    def test_scheduled_task_disabled_state(self):
        """Test enabled/disabled state."""
        task = ScheduledTask.objects.create(
            name="Test",
            task_path="myapp.tasks.test",
            cron_expression="* * * * *",
            enabled=False,
        )
        assert task.enabled is False


@pytest.mark.django_db
class TestQueuedJob:
    """Test QueuedJob model."""

    def test_queued_job_creation(self):
        """Test creating a queued job."""
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
            queue_name="default",
            priority=0,
        )
        assert job.status == "queued"
        assert job.created_at is not None
        assert job.started_at is None
        assert job.finished_at is None
        assert job.scheduled_at is None
        assert job.scheduled_task is None

    def test_queued_job_with_scheduled_task(self):
        """Test job linked to scheduled task."""
        task = ScheduledTask.objects.create(
            name="Test",
            task_path="myapp.tasks.test",
            cron_expression="* * * * *",
        )

        job = QueuedJob.objects.create(
            task_path=task.task_path,
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_task=task,
        )

        assert job.scheduled_task == task
        assert job.task_path == task.task_path

    def test_queued_job_with_scheduled_at(self):
        """Test job with future scheduled_at."""
        future_time = timezone.now() + timedelta(hours=2)

        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
            scheduled_at=future_time,
        )

        assert job.scheduled_at is not None
        assert job.scheduled_at > timezone.now()

    def test_mark_running(self):
        """Test marking job as running."""
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
        )

        assert job.status == "queued"
        assert job.started_at is None

        job.mark_running()

        assert job.status == "running"
        assert job.started_at is not None

    def test_mark_success(self):
        """Test marking job as successful."""
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
        )
        job.mark_running()

        job.mark_success(output="Success result")

        assert job.status == "success"
        assert job.output == "Success result"
        assert job.finished_at is not None
        assert job.duration_seconds is not None
        assert job.duration_seconds >= 0

    def test_mark_failed(self):
        """Test marking job as failed."""
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
        )
        job.mark_running()

        job.mark_failed(error="Test error", traceback="Traceback here")

        assert job.status == "failed"
        assert job.error == "Test error"
        assert job.traceback == "Traceback here"
        assert job.finished_at is not None
        assert job.duration_seconds is not None

    def test_duration_calculation(self):
        """Test duration is calculated correctly."""
        import time

        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
        )
        job.mark_running()

        time.sleep(0.1)  # 100ms

        job.mark_success()

        assert job.duration_seconds >= 0.1
        assert job.duration_seconds < 1.0  # Should be less than 1 second

    def test_queued_job_ordering(self):
        """Test jobs are ordered by priority desc, created_at asc."""
        job_low = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
            priority=1,
        )
        job_high = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
            priority=10,
        )
        job_medium = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
            priority=5,
        )

        jobs = list(QueuedJob.objects.all())

        # Should be ordered by priority descending
        assert jobs[0].id == job_high.id
        assert jobs[1].id == job_medium.id
        assert jobs[2].id == job_low.id

    def test_status_choices(self):
        """Test all status choices are valid."""
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
        )

        # Queued -> Running -> Success
        assert job.status == "queued"
        job.status = "running"
        job.save()
        job.status = "success"
        job.save()

        # Queued -> Running -> Failed
        job2 = QueuedJob.objects.create(
            task_path="myapp.tasks.test",
        )
        job2.status = "running"
        job2.save()
        job2.status = "failed"
        job2.save()

    def test_task_execution_alias(self):
        """Test TaskExecution alias still works for backward compatibility."""
        from sqlery.models import TaskExecution

        # Should be same class
        assert TaskExecution is QueuedJob

        # Should work to create via alias
        execution = TaskExecution.objects.create(
            task_path="myapp.tasks.test",
        )
        assert execution.status == "queued"


@pytest.mark.django_db
class TestScheduledTaskRecomputation:
    """Test automatic schedule recomputation on changes."""

    def test_cron_expression_change_recalculates_next_run(self):
        """Test that changing cron_expression recalculates next_run_at."""
        # Create task with daily cron (midnight)
        task = ScheduledTask.objects.create(
            name="Daily Task",
            task_path="myapp.tasks.daily",
            cron_expression="0 0 * * *",  # Midnight daily
        )

        original_next_run = task.next_run_at
        assert original_next_run.hour == 0
        assert original_next_run.minute == 0

        # Change to hourly cron
        task.cron_expression = "0 * * * *"  # Every hour on the hour
        task.save()

        # Should recalculate - next run should be sooner
        task.refresh_from_db()
        assert task.next_run_at != original_next_run
        assert task.next_run_at.minute == 0

    def test_enabling_task_recalculates_next_run(self):
        """Test that re-enabling a disabled task recalculates next_run_at.

        Uses mock to simulate time passing across a cron boundary, since
        cron has minute-level granularity and real wall-clock time won't
        advance enough within a test.
        """
        from unittest.mock import patch
        from datetime import datetime, timezone as dt_tz

        # Create disabled task at a fixed time (minute=10)
        frozen_t1 = datetime(2026, 3, 10, 1, 10, 0, tzinfo=dt_tz.utc)
        with patch("sqlery.core.utils.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_t1
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            task = ScheduledTask.objects.create(
                name="Disabled Task",
                task_path="myapp.tasks.test",
                cron_expression="0 * * * *",  # Hourly at :00
                enabled=False,
            )

        # original next_run should be 02:00 (next hour boundary from 01:10)
        original_next_run = task.next_run_at

        # # Simulate time passing
        # import time
        # time.sleep(0.1)

        # Re-enable task at a later time (past the next cron boundary)
        frozen_t2 = datetime(2026, 3, 10, 2, 15, 0, tzinfo=dt_tz.utc)
        with patch("sqlery.core.utils.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_t2
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            task.enabled = True
            task.save()

        # Should recalculate from frozen_t2 → next hour boundary = 03:00
        task.refresh_from_db()
        # assert task.next_run_at != original_next_run
        assert task.next_run_at != original_next_run
        assert task.next_run_at > frozen_t2

    def test_disabling_task_keeps_next_run(self):
        """Test that disabling a task keeps next_run_at unchanged."""
        # Create enabled task
        task = ScheduledTask.objects.create(
            name="Enabled Task",
            task_path="myapp.tasks.test",
            cron_expression="0 0 * * *",  # Daily at midnight
            enabled=True,
        )

        original_next_run = task.next_run_at

        # Disable task
        task.enabled = False
        task.save()

        # Should keep same next_run_at
        task.refresh_from_db()
        assert task.next_run_at == original_next_run

    def test_no_change_keeps_next_run(self):
        """Test that saving without changes keeps next_run_at."""
        # Create task
        task = ScheduledTask.objects.create(
            name="Unchanged Task",
            task_path="myapp.tasks.test",
            cron_expression="0 * * * *",
        )

        original_next_run = task.next_run_at

        # Save without changes
        task.name = "Renamed Task"  # Change non-cron field
        task.save()

        # Should keep same next_run_at
        task.refresh_from_db()
        assert task.next_run_at == original_next_run

    def test_cron_and_enabled_change_uses_cron_priority(self):
        """Test that cron change takes priority over enabled change."""
        # Create disabled task
        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="myapp.tasks.test",
            cron_expression="0 0 * * *",  # Daily
            enabled=False,
        )

        # Change both cron and enabled flag
        task.cron_expression = "0 * * * *"  # Hourly
        task.enabled = True
        task.save()

        # Should use new cron expression for calculation
        task.refresh_from_db()
        assert task.next_run_at.minute == 0

    def test_multiple_cron_changes(self):
        """Test multiple successive cron changes."""
        task = ScheduledTask.objects.create(
            name="Changing Task",
            task_path="myapp.tasks.test",
            cron_expression="0 0 * * *",  # Daily at midnight
        )

        # Change 1: To hourly
        task.cron_expression = "0 * * * *"
        task.save()
        task.refresh_from_db()
        next_run_1 = task.next_run_at

        # Change 2: To every 5 minutes
        task.cron_expression = "*/5 * * * *"
        task.save()
        task.refresh_from_db()
        next_run_2 = task.next_run_at

        # Each change should produce different next_run_at
        assert next_run_2 != next_run_1

    def test_disabled_then_cron_change_then_enable(self):
        """Test complex scenario: disable, change cron, re-enable.

        Uses mock to simulate time passing across cron boundaries.
        """
        from unittest.mock import patch
        from datetime import datetime, timezone as dt_tz

        # Create at fixed time
        frozen_t1 = datetime(2026, 3, 10, 1, 10, 0, tzinfo=dt_tz.utc)
        with patch("sqlery.core.utils.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_t1
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            task = ScheduledTask.objects.create(
                name="Complex Task",
                task_path="myapp.tasks.test",
                cron_expression="0 0 * * *",  # Daily at midnight
                enabled=True,
            )

        # Disable (no time mock needed — disabling doesn't recalculate)
        task.enabled = False
        task.save()
        task.refresh_from_db()
        next_run_after_disable = task.next_run_at

        # Change cron while disabled — expression change triggers recalculation
        with patch("sqlery.core.utils.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_t1
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            task.cron_expression = "0 * * * *"  # Hourly
            task.save()
        task.refresh_from_db()
        next_run_after_cron_change = task.next_run_at

        # Cron change should recalculate even when disabled
        assert next_run_after_cron_change != next_run_after_disable

        # Re-enable at a later time (past the next hourly boundary)
        frozen_t2 = datetime(2026, 3, 10, 2, 15, 0, tzinfo=dt_tz.utc)
        with patch("sqlery.core.utils.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_t2
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            task.enabled = True
            task.save()
        task.refresh_from_db()
        next_run_after_enable = task.next_run_at

        # # Re-enabling should recalculate from current time
        # assert next_run_after_enable != next_run_after_cron_change
        # Re-enabling 1h+ later should produce a different next_run
        assert next_run_after_enable != next_run_after_cron_change
