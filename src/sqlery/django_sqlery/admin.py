"""Django admin configuration for sqlery."""

import os
import signal as sig
from datetime import datetime

from django.contrib import admin
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from sqlery.core.cleanup import CleanupManager

from sqlery.core.worker import TaskExecutor
from .models import ScheduledTask, QueuedJob


# Customize admin site titles
# admin.site.site_header = "SQLery"
# admin.site.site_title = "SQLery"
# admin.site.index_title = "SQLery Dashboard"


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    """Admin for ScheduledTask with multi-type schedule support.

    Supports cron, interval, and once schedule types with conditional field visibility.
    List view redirects to unified dashboard, but add/change forms still work.
    """
    change_form_template = 'admin/sqlery/scheduledtask/change_form.html'
    change_list_template = 'admin/sqlery/change_list.html'

    list_display = [
        "name",
        "enabled_status",
        "schedule_type_display",
        "queue_name",
        "priority",
        "schedule_info",
        "last_run_display",
        "next_run_display",
        "job_count",
    ]
    list_filter = ["enabled", "schedule_type", "queue_name", "created_at"]
    search_fields = ["name", "task_path"]
    readonly_fields = ["last_run_at", "next_run_at", "created_at", "updated_at"]

    fieldsets = (
        (
            "Task Definition",
            {"fields": ("name", "task_path", "task_kwargs", "enabled")},
        ),
        (
            "Schedule Configuration",
            {"fields": ("schedule_type",)},
        ),
        (
            "Cron Schedule",
            {
                "fields": ("cron_expression",),
                "classes": ("schedule-cron",),
                "description": "Configure cron schedule (e.g., '0 2 * * *' for 2 AM daily)",
            },
        ),
        (
            "Interval Schedule",
            {
                "fields": ("interval", "interval_unit", "repeat"),
                "classes": ("schedule-interval",),
                "description": "Run at fixed intervals. Leave repeat empty for indefinite.",
            },
        ),
        (
            "One-Time Schedule",
            {
                "fields": ("scheduled_time",),
                "classes": ("schedule-once",),
                "description": "Run once at a specific time. Task is auto-disabled after execution.",
            },
        ),
        ("Queue Configuration", {"fields": ("queue_name", "priority")}),
        ("Execution Info", {"fields": ("last_run_at", "next_run_at")}),
        (
            "Metadata",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    class Media:
        js = ('admin/js/schedule-fields.js',)

    def schedule_type_display(self, obj):
        colors = {"cron": "#3498db", "interval": "#9b59b6", "once": "#e67e22"}
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            colors.get(obj.schedule_type, "#333"),
            obj.get_schedule_type_display(),
        )

    schedule_type_display.short_description = "Type"

    def schedule_info(self, obj):
        return obj.schedule_display()

    schedule_info.short_description = "Schedule"

    def enabled_status(self, obj):
        if obj.enabled:
            return format_html('<span style="color: green;">{}</span>', "✓ Enabled")
        return format_html('<span style="color: red;">{}</span>', "✗ Disabled")

    enabled_status.short_description = "Status"

    def last_run_display(self, obj):
        if obj.last_run_at:
            return obj.last_run_at.strftime("%Y-%m-%d %H:%M UTC")
        return "-"

    last_run_display.short_description = "Last Run"

    def next_run_display(self, obj):
        now = timezone.now()
        if obj.next_run_at:
            if obj.next_run_at <= now:
                return format_html(
                    '<span style="color: orange;">⏰ {} (due)</span>',
                    obj.next_run_at.strftime("%Y-%m-%d %H:%M UTC"),
                )
            return obj.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
        return "-"

    next_run_display.short_description = "Next Run"

    def job_count(self, obj):
        total = obj.jobs.count()
        queued = obj.jobs.filter(status="queued").count()
        failed = obj.jobs.filter(status="failed").count()

        parts = []
        if queued > 0:
            parts.append(format_html('<span style="color: blue;">{} queued</span>', queued))
        if failed > 0:
            parts.append(format_html('<span style="color: red;">{} failed</span>', failed))

        if parts:
            return format_html("{} total ({})", total, ", ".join(parts))
        return total

    job_count.short_description = "Jobs"

    actions = ["enqueue_now", "enable_tasks", "disable_tasks", "run_cleanup_now"]

    def enqueue_now(self, request, queryset):
        """Admin action to enqueue jobs for tasks immediately."""
        # from .executor import TaskExecutor  # moved to top-level

        executor = TaskExecutor()
        count = 0
        for task in queryset.filter(enabled=True):
            job = executor._enqueue_for_scheduled_task(task)
            if job:
                count += 1
        self.message_user(request, f"Enqueued {count} jobs")

    enqueue_now.short_description = "Enqueue jobs for selected tasks now"

    def enable_tasks(self, request, queryset):
        updated = queryset.update(enabled=True)
        self.message_user(request, f"Enabled {updated} tasks")

    enable_tasks.short_description = "Enable selected tasks"

    def disable_tasks(self, request, queryset):
        updated = queryset.update(enabled=False)
        self.message_user(request, f"Disabled {updated} tasks")

    disable_tasks.short_description = "Disable selected tasks"

    def run_cleanup_now(self, request, queryset):
        """Admin action to run database cleanup and vacuum immediately."""
        # from sqlery.core.cleanup import CleanupManager  # moved to top-level
        result = CleanupManager().auto_cleanup()
        self.message_user(request, f"Cleanup complete: {result}")

    run_cleanup_now.short_description = "Run database cleanup and vacuum now"

    def changelist_view(self, request, extra_context=None):
        """Redirect to unified SQLery dashboard instead of default changelist."""
        # Redirect to unified dashboard
        # from django.urls import reverse  # moved to top-level
        return redirect(reverse('sqlery:unified_view'))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Add 'Enqueue Now' button to task detail page."""
        extra_context = extra_context or {}

        # Handle "Enqueue Now" button click
        if request.method == 'POST' and '_enqueue_now' in request.POST:
            # from .executor import TaskExecutor  # moved to top-level
            task = self.get_object(request, object_id)
            if task and task.enabled:
                executor = TaskExecutor()
                job = executor._enqueue_for_scheduled_task(task)
                if job:
                    self.message_user(request, f"✓ Job enqueued for '{task.name}' (ID: {job.id})")
                else:
                    self.message_user(request, f"✗ Failed to enqueue job for '{task.name}'", level='error')
            else:
                self.message_user(request, f"Task must be enabled to enqueue", level='warning')
            return redirect(request.path)

        return super().change_view(request, object_id, form_url, extra_context=extra_context)


# QueuedJob admin registered with read-only list view for job management
@admin.register(QueuedJob)
class QueuedJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "task_display",
        "queue_name",
        "priority",
        "status_display",
        "retry_display",
        "scheduled_display",
        "started_display",
        "duration_display",
    ]
    ordering = ["-created_at"]
    list_filter = ["status", "queue_name", "created_at", "scheduled_task", "worker", "worker_pid"]
    search_fields = ["task_path", "output", "error"]
    readonly_fields = [
        "task_path",
        "queue_name",
        "priority",
        "status",
        "scheduled_task",
        "kwargs",
        "retry_count",
        "max_retries",
        "retry_backoff",
        "runs_display",
        "created_at",
        "scheduled_at",
        "started_at",
        "finished_at",
        "duration_seconds",
        "output",
        "error",
        "traceback",
        "termination_reason",
    ]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            "Job Info",
            {
                "fields": (
                    "task_path",
                    "queue_name",
                    "priority",
                    "status",
                    "scheduled_task",
                    "kwargs",
                )
            },
        ),
        (
            "Retry Configuration",
            {
                "fields": (
                    "retry_count",
                    "max_retries",
                    "retry_backoff",
                )
            },
        ),
        (
            "Timing",
            {
                "fields": (
                    "created_at",
                    "scheduled_at",
                    "started_at",
                    "finished_at",
                    "duration_seconds",
                )
            },
        ),
        ("Results", {"fields": ("output", "error", "traceback", "termination_reason")}),
        ("Execution History", {"fields": ("runs_display",)}),
    )

    change_form_template = 'admin/sqlery/queuedjob/change_form.html'

    def has_add_permission(self, request):
        return False  # Can't manually create jobs via admin

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context['now'] = timezone.now()

        # Handle enqueue now button click — clear scheduled_at for immediate pickup
        if request.method == 'POST' and '_enqueue_now' in request.POST:
            job = self.get_object(request, object_id)
            if job and job.status == 'queued' and job.scheduled_at and job.scheduled_at > timezone.now():
                job.scheduled_at = None
                job.save(update_fields=['scheduled_at'])
                self.message_user(request, f"Job #{job.id} enqueued for immediate processing.")
            else:
                self.message_user(request, "Job must be queued with a future schedule.", level='warning')
            # from django.shortcuts import redirect  # moved to top-level
            return redirect(request.path)

        # Handle stop button click
        if request.method == 'POST' and '_stop_job' in request.POST:
            job = self.get_object(request, object_id)
            if job and job.status == 'running':
                # import os  # moved to top-level
                # import signal as sig  # moved to top-level
                # from .models import Worker

                # # Old: killed the parent worker, not the forked child
                # worker = Worker.objects.filter(current_job=job, status='busy').first()
                # if worker and worker.pid:
                #     try:
                #         os.kill(worker.pid, sig.SIGTERM)
                #     except OSError:
                #         pass
                #
                # job.mark_failed(
                #     error="Stopped by admin user",
                #     termination_reason="stopped_by_user",
                # )
                #
                # if worker:
                #     worker.status = 'idle'
                #     worker.current_job = None
                #     worker.save(update_fields=['status', 'current_job', 'last_heartbeat'])

                # Kill the forked child (not the parent worker)
                if job.child_pid:
                    try:
                        os.killpg(os.getpgid(job.child_pid), sig.SIGTERM)
                    except (OSError, ProcessLookupError):
                        try:
                            os.kill(job.child_pid, sig.SIGTERM)
                        except OSError:
                            pass
                elif job.worker_pid:
                    # Legacy fallback for jobs without child_pid
                    try:
                        os.kill(job.worker_pid, sig.SIGTERM)
                    except OSError:
                        pass

                job.mark_failed(
                    error="Stopped by admin user",
                    termination_reason="stopped_by_user",
                )

                # Don't touch worker status — the parent is still alive and will
                # update its own state after detecting the child's exit via waitpid.

                self.message_user(request, f"Job {job.id} stopped.")
            else:
                self.message_user(request, "Job is not running.", level='warning')
            # from django.shortcuts import redirect  # moved to top-level
            return redirect(request.path)

        # Handle retry button click — re-queue the same job (keeps execution history)
        if request.method == 'POST' and '_retry_job' in request.POST:
            job = self.get_object(request, object_id)
            if job and job.status == 'failed':
                QueuedJob.objects.filter(id=job.id).update(
                    status='queued',
                    started_at=None,
                    finished_at=None,
                    duration_seconds=None,
                    error='',
                    traceback='',
                    termination_reason='',
                    output='',
                    worker=None,
                    worker_pid=None,
                )
                self.message_user(request, f"Job #{job.id} re-queued.")
            else:
                self.message_user(request, "Only failed jobs can be retried.", level='warning')
            # from django.shortcuts import redirect  # moved to top-level
            return redirect(request.path)

        # Pass UTC ISO timestamps for JS timezone toggle
        job = self.get_object(request, object_id)
        if job:
            extra_context['job_timestamps'] = {
                'created_at': job.created_at.isoformat() if job.created_at else None,
                'scheduled_at': job.scheduled_at.isoformat() if job.scheduled_at else None,
                'started_at': job.started_at.isoformat() if job.started_at else None,
                'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            }

        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def task_display(self, obj):
        if obj.scheduled_task:
            return format_html(
                '<strong>{}</strong> <br/><small>{}</small>',
                obj.scheduled_task.name,
                obj.task_path,
            )
        return format_html('<small>{}</small>', obj.task_path)

    task_display.short_description = "Task"

    def status_display(self, obj):
        colors = {
            "queued": "blue",
            "running": "orange",
            "success": "green",
            "failed": "red",
            "archived": "gray",
        }
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            colors.get(obj.status, "black"),
            obj.get_status_display(),
        )

    status_display.short_description = "Status"

    def scheduled_display(self, obj):
        if obj.scheduled_at:
            now = timezone.now()
            if obj.scheduled_at > now:
                return format_html(
                    '<span style="color: orange;">⏰ {}</span>',
                    obj.scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
                )
            return obj.scheduled_at.strftime("%Y-%m-%d %H:%M UTC")
        return format_html('<span style="color: green;">{}</span>', "Immediate")

    scheduled_display.short_description = "Scheduled For"

    def started_display(self, obj):
        if obj.started_at:
            return obj.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        return "-"

    started_display.short_description = "Started"

    def duration_display(self, obj):
        if obj.duration_seconds:
            return f"{obj.duration_seconds:.2f}s"
        return "-"

    duration_display.short_description = "Duration"

    def retry_display(self, obj):
        if obj.max_retries == 0:
            return "-"
        return format_html(
            '<span>{}/{}</span>',
            obj.retry_count,
            obj.max_retries,
        )

    retry_display.short_description = "Retries"

    def runs_display(self, obj):
        """Display execution history in a nice table format."""
        if not obj.runs or len(obj.runs) == 0:
            return format_html('<i>{}</i>', "No execution history")

        html_parts = ['<table style="width: 100%; border-collapse: collapse;">']
        html_parts.append(
            '<thead><tr style="background-color: #f0f0f0;">'
            '<th style="padding: 8px; border: 1px solid #ddd;">Attempt</th>'
            '<th style="padding: 8px; border: 1px solid #ddd;">Started</th>'
            '<th style="padding: 8px; border: 1px solid #ddd;">Finished</th>'
            '<th style="padding: 8px; border: 1px solid #ddd;">Duration</th>'
            '<th style="padding: 8px; border: 1px solid #ddd;">Status</th>'
            '<th style="padding: 8px; border: 1px solid #ddd;">Output/Error</th>'
            '</tr></thead><tbody>'
        )

        for run in obj.runs:
            attempt = run.get("attempt_number", "?")
            started = run.get("started_at", "")
            finished = run.get("finished_at", "")
            duration = run.get("duration") or 0
            status = run.get("status", "unknown")
            output = run.get("output", "")
            error = run.get("error", "")

            # Format datetime strings
            if started:
                try:
                    # from datetime import datetime  # moved to top-level
                    started_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                    started = started_dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass

            if finished:
                try:
                    finished_dt = datetime.fromisoformat(finished.replace('Z', '+00:00'))
                    finished = finished_dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass

            # Status color
            status_color = {
                "success": "green",
                "failed": "red",
            }.get(status, "black")

            # Display output or error
            message = error if error else output
            message = message[:500] + "..." if len(message) > 500 else message

            html_parts.append(
                f'<tr>'
                f'<td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{attempt}</td>'
                f'<td style="padding: 8px; border: 1px solid #ddd; font-size: 11px;">{started}</td>'
                f'<td style="padding: 8px; border: 1px solid #ddd; font-size: 11px;">{finished}</td>'
                f'<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{duration:.2f}s</td>'
                f'<td style="padding: 8px; border: 1px solid #ddd; color: {status_color}; font-weight: bold;">{status}</td>'
                f'<td style="padding: 8px; border: 1px solid #ddd; font-size: 11px; max-width: 600px; overflow: hidden; text-overflow: ellipsis;">{message}</td>'
                f'</tr>'
            )

        html_parts.append('</tbody></table>')
        return format_html(''.join(html_parts))

    runs_display.short_description = "Execution History"

    actions = ["retry_failed", "cancel_queued", "stop_running"]

    def retry_failed(self, request, queryset):
        """Retry failed jobs by creating new queued jobs."""
        count = 0
        for job in queryset.filter(status="failed"):
            QueuedJob.objects.create(
                task_path=job.task_path,
                kwargs=job.kwargs.copy() if isinstance(job.kwargs, dict) else {},
                queue_name=job.queue_name,
                priority=job.priority,
                scheduled_task=job.scheduled_task,
                scheduled_at=None,  # Run immediately
                max_retries=job.max_retries,
                retry_backoff=job.retry_backoff,
                retry_count=0,  # Reset retry count for manual retry
                runs=[],  # Fresh run history
                parent_job_id=job.id,
            )
            # Mark original as archived (retry created)
            job.status = 'archived'
            job.save(update_fields=['status'])
            count += 1
        self.message_user(request, f"Created {count} retry jobs")

    retry_failed.short_description = "Retry selected failed jobs"

    def cancel_queued(self, request, queryset):
        """Cancel queued jobs by marking as failed."""
        updated = queryset.filter(status="queued").update(
            status="failed",
            error="Cancelled by admin user before execution started",
            termination_reason="cancelled_by_user"
        )
        self.message_user(request, f"Cancelled {updated} queued jobs")

    cancel_queued.short_description = "Cancel selected queued jobs"

    def stop_running(self, request, queryset):
        """Stop running jobs by killing their worker processes."""
        # from .executor import TaskExecutor  # moved to top-level

        executor = TaskExecutor()
        killed_count = 0
        no_pid_count = 0

        for job in queryset.filter(status="running"):
            if not job.worker_pid:
                # No PID stored, can't kill - mark as failed
                job.mark_failed(
                    error="Stopped by admin user (no worker PID available to kill process)",
                    traceback="Job was running but worker_pid was not stored, cannot kill process",
                    termination_reason="stopped_by_user_no_pid"
                )
                no_pid_count += 1
            else:
                # Try to kill the worker process
                kill_method = executor._kill_worker_process(job.worker_pid)

                if kill_method == "SIGTERM":
                    job.mark_failed(
                        error=f"Stopped by admin user (worker process {job.worker_pid} terminated with SIGTERM)",
                        traceback=f"Admin user requested job termination",
                        termination_reason="stopped_by_user_sigterm"
                    )
                    killed_count += 1
                elif kill_method == "SIGKILL":
                    job.mark_failed(
                        error=f"Stopped by admin user (worker process {job.worker_pid} forcefully killed with SIGKILL)",
                        traceback=f"Admin user requested job termination, SIGTERM did not work within 5 seconds",
                        termination_reason="stopped_by_user_sigkill"
                    )
                    killed_count += 1
                else:
                    # Process already dead or couldn't kill
                    job.mark_failed(
                        error=f"Stopped by admin user (worker process {job.worker_pid} was already terminated)",
                        traceback="Worker process was not running when kill was attempted",
                        termination_reason="stopped_by_user_already_dead"
                    )
                    no_pid_count += 1

        message_parts = []
        if killed_count > 0:
            message_parts.append(f"Killed {killed_count} running jobs")
        if no_pid_count > 0:
            message_parts.append(f"Marked {no_pid_count} jobs as failed (no active process)")

        self.message_user(request, ", ".join(message_parts) if message_parts else "No jobs stopped")

    stop_running.short_description = "Stop/kill selected running jobs"
