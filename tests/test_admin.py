"""Tests for Django admin actions."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.cookie import CookieStorage
from django.test import RequestFactory
from sqlery.models import ScheduledTask, QueuedJob
from sqlery.admin import ScheduledTaskAdmin, QueuedJobAdmin


@pytest.mark.django_db
class TestScheduledTaskAdmin:
    """Test ScheduledTaskAdmin actions and methods."""

    def test_enqueue_now_action(self, rf):
        """Test manual enqueue action."""
        # Create tasks
        task1 = ScheduledTask.objects.create(
            name="Task 1",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=True,
        )
        task2 = ScheduledTask.objects.create(
            name="Task 2",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=True,
        )

        # Setup admin
        site = AdminSite()
        admin = ScheduledTaskAdmin(ScheduledTask, site)

        # Create request
        request = rf.post("/admin/sqlery/scheduledtask/")
        request._messages = CookieStorage(request)

        # Call action
        queryset = ScheduledTask.objects.filter(id__in=[task1.id, task2.id])
        admin.enqueue_now(request, queryset)

        # Should have created 2 jobs
        assert QueuedJob.objects.filter(scheduled_task=task1).exists()
        assert QueuedJob.objects.filter(scheduled_task=task2).exists()

    def test_enqueue_now_skips_disabled_tasks(self, rf):
        """Test that enqueue action skips disabled tasks."""
        task_enabled = ScheduledTask.objects.create(
            name="Enabled",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=True,
        )
        task_disabled = ScheduledTask.objects.create(
            name="Disabled",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=False,
        )

        site = AdminSite()
        admin = ScheduledTaskAdmin(ScheduledTask, site)

        request = rf.post("/admin/sqlery/scheduledtask/")
        request._messages = CookieStorage(request)

        queryset = ScheduledTask.objects.all()
        admin.enqueue_now(request, queryset)

        # Only enabled task should have job
        assert QueuedJob.objects.filter(scheduled_task=task_enabled).exists()
        assert not QueuedJob.objects.filter(scheduled_task=task_disabled).exists()

    def test_enable_tasks_action(self, rf):
        """Test enable tasks action."""
        task1 = ScheduledTask.objects.create(
            name="Task 1",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=False,
        )
        task2 = ScheduledTask.objects.create(
            name="Task 2",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=False,
        )

        site = AdminSite()
        admin = ScheduledTaskAdmin(ScheduledTask, site)

        request = rf.post("/admin/sqlery/scheduledtask/")
        request._messages = CookieStorage(request)

        queryset = ScheduledTask.objects.all()
        admin.enable_tasks(request, queryset)

        # Both should be enabled
        task1.refresh_from_db()
        task2.refresh_from_db()
        assert task1.enabled is True
        assert task2.enabled is True

    def test_disable_tasks_action(self, rf):
        """Test disable tasks action."""
        task1 = ScheduledTask.objects.create(
            name="Task 1",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=True,
        )
        task2 = ScheduledTask.objects.create(
            name="Task 2",
            task_path="tests.test_executor.dummy_task",
            cron_expression="* * * * *",
            enabled=True,
        )

        site = AdminSite()
        admin = ScheduledTaskAdmin(ScheduledTask, site)

        request = rf.post("/admin/sqlery/scheduledtask/")
        request._messages = CookieStorage(request)

        queryset = ScheduledTask.objects.all()
        admin.disable_tasks(request, queryset)

        # Both should be disabled
        task1.refresh_from_db()
        task2.refresh_from_db()
        assert task1.enabled is False
        assert task2.enabled is False


@pytest.mark.django_db
class TestQueuedJobAdmin:
    """Test QueuedJobAdmin actions and methods."""

    def test_retry_failed_action(self, rf):
        """Test retry failed jobs action."""
        # Create failed job
        job = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="queued",
        )
        job.mark_running()
        job.mark_failed("Test error", "Traceback")

        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        request = rf.post("/admin/sqlery/queuedjob/")
        request._messages = CookieStorage(request)

        queryset = QueuedJob.objects.filter(id=job.id)
        admin.retry_failed(request, queryset)

        # Should have created new queued job
        new_jobs = QueuedJob.objects.filter(
            task_path=job.task_path, status="queued"
        )
        assert new_jobs.count() == 1

        new_job = new_jobs.first()
        assert new_job.task_path == job.task_path
        assert new_job.queue_name == job.queue_name
        assert new_job.priority == job.priority

    def test_retry_failed_only_retries_failed_jobs(self, rf):
        """Test that retry action only affects failed jobs."""
        job_failed = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="failed",
        )
        job_success = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="success",
        )

        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        request = rf.post("/admin/sqlery/queuedjob/")
        request._messages = CookieStorage(request)

        queryset = QueuedJob.objects.all()
        admin.retry_failed(request, queryset)

        # Should only create retry job for failed job
        retry_jobs = QueuedJob.objects.filter(status="queued")
        assert retry_jobs.count() == 1

    def test_cancel_queued_action(self, rf):
        """Test cancel queued jobs action."""
        job1 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="queued",
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="queued",
        )

        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        request = rf.post("/admin/sqlery/queuedjob/")
        request._messages = CookieStorage(request)

        queryset = QueuedJob.objects.all()
        admin.cancel_queued(request, queryset)

        # Both should be marked as failed with "Cancelled" error
        job1.refresh_from_db()
        job2.refresh_from_db()
        assert job1.status == "failed"
        assert job2.status == "failed"
        assert "Cancelled by admin" in job1.error
        assert "Cancelled by admin" in job2.error

    def test_cancel_queued_only_affects_queued_jobs(self, rf):
        """Test that cancel action only affects queued jobs."""
        job_queued = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="queued",
        )
        job_running = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="running",
        )

        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        request = rf.post("/admin/sqlery/queuedjob/")
        request._messages = CookieStorage(request)

        queryset = QueuedJob.objects.all()
        admin.cancel_queued(request, queryset)

        # Only queued job should be cancelled
        job_queued.refresh_from_db()
        job_running.refresh_from_db()
        assert job_queued.status == "failed"
        assert job_running.status == "running"

    def test_has_add_permission_is_false(self, rf):
        """Test that jobs cannot be manually created via admin."""
        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        request = rf.get("/admin/sqlery/queuedjob/add/")

        assert admin.has_add_permission(request) is False


@pytest.mark.django_db
class TestAdminDisplayMethods:
    """Test admin display methods."""

    def test_scheduled_task_admin_display_methods(self):
        """Test ScheduledTaskAdmin display methods."""
        from django.utils import timezone

        task = ScheduledTask.objects.create(
            name="Test Task",
            task_path="tests.test_executor.dummy_task",
            cron_expression="0 9 * * *",
            enabled=True,
            last_run_at=timezone.now(),
            next_run_at=timezone.now(),
        )

        site = AdminSite()
        admin = ScheduledTaskAdmin(ScheduledTask, site)

        # Test enabled_status
        status = admin.enabled_status(task)
        assert "Enabled" in status

        # Test last_run_display
        last_run = admin.last_run_display(task)
        assert last_run != "-"

        # Test next_run_display
        next_run = admin.next_run_display(task)
        assert next_run != "-"

    def test_queued_job_admin_display_methods(self):
        """Test QueuedJobAdmin display methods."""
        from django.utils import timezone

        job = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="success",
            scheduled_at=timezone.now(),
            started_at=timezone.now(),
        )
        job.duration_seconds = 1.5
        job.save()

        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        # Test status_display
        status = admin.status_display(job)
        assert "Success" in status

        # Test duration_display
        duration = admin.duration_display(job)
        assert "1.50s" in duration

        # Test scheduled_display
        scheduled = admin.scheduled_display(job)
        assert scheduled != "-"

        # Test started_display
        started = admin.started_display(job)
        assert started != "-"

    def test_runs_display_does_not_crash_with_execution_history(self):
        """Regression: runs_display crashed on Django 6.0 with format_html() and no args."""
        job = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            status="failed",
            runs=[
                {
                    "attempt_number": 1,
                    "started_at": "2026-05-25T10:00:00Z",
                    "finished_at": "2026-05-25T10:00:01Z",
                    "duration": 1.0,
                    "status": "failed",
                    "error": "Something went wrong",
                    "output": "",
                },
            ],
        )

        site = AdminSite()
        admin = QueuedJobAdmin(QueuedJob, site)

        result = admin.runs_display(job)
        assert "Something went wrong" in result
        assert "<table" in result
