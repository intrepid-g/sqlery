"""Models for sqlery.

These Django models match the SQLModel definitions in core/models.py
to ensure schema consistency across Django and standalone modes.
"""

import logging
import os
import uuid
from datetime import timedelta

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import F
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from uuid6 import uuid7

from .db_compat import is_sqlite
from .friendly_name import uuid_to_friendly
from .settings import get_setting
from .utils import calculate_next_run, validate_cron_expression

logger = logging.getLogger(__name__)


class ConcurrentModificationError(Exception):
    """Raised when a job is modified by another process during an update.

    This indicates an optimistic locking conflict where the job's version
    changed between reading and updating.
    """

    pass


SCHEDULE_TYPE_CHOICES = [
    ("cron", "Cron"),
    ("interval", "Interval"),
    ("once", "Once"),
]

INTERVAL_UNIT_CHOICES = [
    ("seconds", "Seconds"),
    ("minutes", "Minutes"),
    ("hours", "Hours"),
    ("days", "Days"),
    ("weeks", "Weeks"),
]


class ScheduledTaskManager(models.Manager):
    """Manager with natural key support for cross-environment fixture portability."""

    def get_by_natural_key(self, name):
        return self.get(name=name)


class ScheduledTask(models.Model):
    # """A scheduled task that runs on a cron schedule.
    #
    # Schema synchronized with core.models.ScheduledTask (SQLModel).
    # """
    """A scheduled task that runs on a cron, interval, or one-time schedule."""

    objects = ScheduledTaskManager()

    # Task definition
    name = models.CharField(max_length=255, unique=True, help_text="Unique name for this task")
    task_path = models.CharField(
        max_length=500,
        help_text="Python path to callable (e.g., 'myapp.tasks.my_function')",
    )
    task_kwargs = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Keyword arguments to pass to the task callable on each run",
    )

    # Schedule configuration
    schedule_type = models.CharField(
        max_length=10,
        choices=SCHEDULE_TYPE_CHOICES,
        default="cron",
        help_text="Type of schedule: cron, interval, or once",
    )

    # Cron schedule fields
    # cron_expression = models.CharField(
    #     max_length=100, help_text="Cron expression (e.g., '0 2 * * *' for 2 AM daily)"
    # )
    cron_expression = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Cron expression (e.g., '0 2 * * *' for 2 AM daily). Required for cron type.",
    )

    # Interval schedule fields
    interval = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Interval amount (e.g., 5 for 'every 5 minutes'). Required for interval type.",
    )
    interval_unit = models.CharField(
        max_length=10,
        choices=INTERVAL_UNIT_CHOICES,
        null=True,
        blank=True,
        default="minutes",
        help_text="Interval unit: seconds, minutes, hours, days, or weeks",
    )
    repeat = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of times to repeat (null = indefinitely). For interval type.",
    )

    # Once schedule fields
    scheduled_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Exact datetime to run the task once. Required for once type.",
    )

    # Queue configuration
    queue_name = models.CharField(
        max_length=50,
        default="default",
        help_text="Queue name for job routing",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Priority for enqueued jobs (higher = sooner)",
    )

    # Status
    enabled = models.BooleanField(default=True, help_text="Whether this task should run")

    # Execution tracking
    last_run_at = models.DateTimeField(
        null=True, blank=True, help_text="Last successful execution time (UTC)"
    )
    next_run_at = models.DateTimeField(
        null=True, blank=True, help_text="Next scheduled execution time (UTC)"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sqlery_scheduled_task"
        ordering = ["name"]
        verbose_name = "Task"
        verbose_name_plural = "SQLery Dashboard"
        indexes = [
            models.Index(fields=["enabled", "next_run_at"]),
            models.Index(fields=["schedule_type", "enabled"]),
        ]

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # """Calculate next_run_at on creation or when cron_expression/enabled changes.
        #
        # Behavior:
        # - New task: Calculate next_run_at from cron_expression
        # - Cron changed: Recalculate next_run_at immediately
        # - Enabled changed True→False: Keep next_run_at (ready when re-enabled)
        # - Enabled changed False→True: Recalculate next_run_at from now
        # """
        """Calculate next_run_at based on schedule_type on creation or schedule changes."""

        # Creating new task
        if not self.pk:
            if not self.next_run_at:
                # self.next_run_at = calculate_next_run(self.cron_expression)
                self.next_run_at = self._calculate_next_run()
        else:
            # Updating existing task - check for changes
            try:
                old = ScheduledTask.objects.get(pk=self.pk)

                # # Cron expression changed - recalculate schedule
                # if old.cron_expression != self.cron_expression:
                #     self.next_run_at = calculate_next_run(self.cron_expression)
                #
                # # Re-enabled task - recalculate from current time
                # elif not old.enabled and self.enabled:
                #     self.next_run_at = calculate_next_run(self.cron_expression)

                # Multi-type schedule detection (v0.13)
                schedule_changed = (
                    old.schedule_type != self.schedule_type
                    or old.cron_expression != self.cron_expression
                    or old.interval != self.interval
                    or old.interval_unit != self.interval_unit
                    or old.scheduled_time != self.scheduled_time
                )

                if schedule_changed:
                    self.next_run_at = self._calculate_next_run()
                elif not old.enabled and self.enabled:
                    self.next_run_at = self._calculate_next_run()

                # Disabled task - keep next_run_at unchanged

            except ScheduledTask.DoesNotExist:
                # Edge case: pk set but object doesn't exist in DB
                if not self.next_run_at:
                    # self.next_run_at = calculate_next_run(self.cron_expression)
                    self.next_run_at = self._calculate_next_run()

        super().save(*args, **kwargs)

        # EventBridge mode: Create/update or disable cron rule
        self._sync_eventbridge_rule()

    def _sync_eventbridge_rule(self):
        """Sync EventBridge cron rule when in eventbridge trigger mode."""
        # from .settings import get_setting  # moved to top-level

        trigger_mode = get_setting("TRIGGER_MODE", "middleware")

        if trigger_mode != "eventbridge":
            return

        # import logging  # moved to top-level

        # logger = logging.getLogger(__name__)  # already defined at module level

        try:
            if self.enabled:
                # Create or update EventBridge rule
                from .eventbridge_trigger import ensure_cron_eventbridge_rule

                result = ensure_cron_eventbridge_rule(
                    task_id=self.id,
                    cron_expression=self.cron_expression,
                    task_path=self.task_path,
                    queue_name=self.queue_name,
                    priority=self.priority,
                )
                logger.info(
                    f"Synced EventBridge rule for task '{self.name}': {result['rule_name']}"
                )
            else:
                # Disable EventBridge rule
                from .eventbridge_trigger import disable_cron_eventbridge_rule

                disable_cron_eventbridge_rule(self.id)
                logger.info(f"Disabled EventBridge rule for task '{self.name}'")

        except Exception as e:
            logger.error(f"Failed to sync EventBridge rule for task '{self.name}': {e}")

    def _calculate_next_run(self):
        """Calculate next_run_at based on schedule_type."""
        # from datetime import timedelta  # moved to top-level

        # from django.utils import timezone as tz  # moved to top-level

        # from .utils import calculate_next_run  # moved to top-level

        if self.schedule_type == "cron" and self.cron_expression:
            return calculate_next_run(self.cron_expression)
        elif self.schedule_type == "interval" and self.interval:
            return timezone.now() + timedelta(seconds=self.get_interval_seconds())
        elif self.schedule_type == "once" and self.scheduled_time:
            return self.scheduled_time
        return None

    def get_interval_seconds(self):
        """Convert interval + interval_unit to seconds."""
        multipliers = {
            "seconds": 1,
            "minutes": 60,
            "hours": 3600,
            "days": 86400,
            "weeks": 604800,
        }
        if self.interval and self.interval_unit:
            return self.interval * multipliers.get(self.interval_unit, 60)
        return 0

    def schedule_display(self):
        """Human-readable schedule description."""
        if self.schedule_type == "cron":
            return f"Cron: {self.cron_expression}"
        elif self.schedule_type == "interval":
            repeat_str = f" ({self.repeat}x)" if self.repeat else ""
            return f"Every {self.interval} {self.interval_unit}{repeat_str}"
        elif self.schedule_type == "once":
            if self.scheduled_time:
                return f"Once at {self.scheduled_time.strftime('%Y-%m-%d %H:%M UTC')}"
            return "Once (no time set)"
        return str(self.schedule_type)

    def clean(self):
        """Validate schedule fields based on schedule_type."""
        # from django.core.exceptions import ValidationError  # moved to top-level

        errors = {}

        if self.schedule_type == "cron":
            if not self.cron_expression:
                errors["cron_expression"] = "Cron expression is required for cron schedule type."
            else:
                # from .utils import validate_cron_expression  # moved to top-level

                is_valid, error_msg = validate_cron_expression(self.cron_expression)
                if not is_valid:
                    errors["cron_expression"] = f"Invalid cron expression: {error_msg}"

        elif self.schedule_type == "interval":
            if not self.interval or self.interval <= 0:
                errors["interval"] = "Interval must be a positive number."
            if not self.interval_unit:
                errors["interval_unit"] = "Interval unit is required."

        elif self.schedule_type == "once":
            if not self.scheduled_time:
                errors["scheduled_time"] = "Scheduled time is required for once schedule type."

        if errors:
            raise ValidationError(errors)

    def get_kwargs_dict(self):
        """Get task_kwargs as a dict for passing to QueuedJob."""
        if isinstance(self.task_kwargs, dict):
            return self.task_kwargs.copy()
        return {}


class QueuedJob(models.Model):
    """A job in the queue, waiting to be executed or already processed.

    Schema synchronized with core.models.QueuedJob (SQLModel).
    """

    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("archived", "Archived"),
        ("shutting_down", "Shutting Down"),
    ]

    # Task definition
    task_path = models.CharField(
        max_length=500,
        help_text="Python path to callable (e.g., 'myapp.tasks.my_function')",
    )
    kwargs = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Keyword arguments to pass to task function",
    )

    # Queue configuration
    queue_name = models.CharField(
        max_length=50,
        default="default",
        db_index=True,
        help_text="Queue name for job routing",
    )
    priority = models.IntegerField(
        default=0,
        db_index=True,
        help_text="Priority (higher = sooner)",
    )

    # Status
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="queued", db_index=True
    )
    version = models.IntegerField(
        default=0,
        help_text="Optimistic locking version for atomic job claiming (increments on each update)",
    )

    # Retry chain linkage
    parent_job_id = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="ID of the failed job this retry was created from (links retry chain)",
    )

    # Retry configuration
    retry_count = models.IntegerField(
        default=0,
        help_text="Current retry attempt number (0 = first attempt)",
    )
    max_retries = models.IntegerField(
        default=0,
        help_text="Maximum number of retry attempts (0 = no retries)",
    )
    retry_backoff = models.FloatField(
        default=1.0,
        help_text="Exponential backoff multiplier (seconds between retries)",
    )

    # Concurrency and timeout configuration
    allow_parallel = models.BooleanField(
        default=False,
        help_text="Allow multiple jobs from same queue to run in parallel (default: false for safety)",
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Tags for concurrency limiting (e.g., ['acme-api', 'rate-limited'])",
    )
    dependencies = models.JSONField(
        default=list,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="List of job IDs that must complete successfully before this job can run",
    )
    webhook_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text="URL to call when job completes (success or failure)",
    )
    webhook_events = models.JSONField(
        default=list,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Events that trigger webhook: ['success', 'failure'] or subset",
    )
    webhook_status = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        choices=[
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("failed", "Failed"),
        ],
        help_text="Status of webhook delivery",
    )
    webhook_retries = models.IntegerField(
        default=0,
        help_text="Number of webhook delivery attempts",
    )
    webhook_max_retries = models.IntegerField(
        default=3,
        help_text="Maximum webhook delivery retry attempts",
    )
    timeout_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="Maximum execution time in seconds (None = no timeout)",
    )
    worker_pid = models.IntegerField(
        null=True,
        blank=True,
        help_text="Process ID of worker executing this job (for external kill on timeout)",
    )
    child_pid = models.IntegerField(
        null=True,
        blank=True,
        help_text="PID of forked child executing this job",
    )

    # Execution history
    runs = models.JSONField(
        default=list,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="History of all execution attempts",
    )

    # Free-form metadata (persisted to DB; compatible with RQ job.meta)
    meta = models.JSONField(
        null=True,
        blank=True,
        default=None,
        encoder=DjangoJSONEncoder,
        help_text="Free-form metadata dict for task functions (persisted to DB)",
    )

    # Optional unique string identifier (e.g. 'send-invoice-123')
    job_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Optional unique string identifier (e.g. 'send-invoice-123')",
    )

    # Fixed retry delay list in seconds — overrides exponential backoff
    retry_intervals = models.JSONField(
        null=True,
        blank=True,
        default=None,
        encoder=DjangoJSONEncoder,
        help_text="Fixed retry delay list in seconds [5, 10, 60]. Overrides exponential backoff when set.",
    )

    # Callbacks (RQ compat: on_success / on_failure)
    on_success_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="Import path for success callback",
    )
    on_failure_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="Import path for failure callback",
    )

    # TTL (RQ compat: ttl / result_ttl / failure_ttl)
    ttl = models.IntegerField(
        null=True,
        blank=True,
        help_text="Seconds job can stay queued before expiring (None = no limit)",
    )
    result_ttl = models.IntegerField(
        null=True,
        blank=True,
        help_text="Seconds to keep successful result (-1 = forever, None = use global)",
    )
    failure_ttl = models.IntegerField(
        null=True,
        blank=True,
        help_text="Seconds to keep failed job data (-1 = forever, None = use global)",
    )

    # Optional reference to scheduled task
    scheduled_task = models.ForeignKey(
        ScheduledTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
        help_text="The scheduled task that created this job (if any)",
    )

    # Worker assignment (for multi-worker mode)
    worker = models.ForeignKey(
        "Worker",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_jobs",
        help_text="Worker currently processing this job",
    )

    # Timing
    created_at = models.DateTimeField(auto_now_add=True, help_text="When job was enqueued")
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When job should run (NULL = run immediately)",
    )
    started_at = models.DateTimeField(null=True, blank=True, help_text="When execution began")
    finished_at = models.DateTimeField(null=True, blank=True, help_text="When execution completed")
    duration_seconds = models.FloatField(null=True, blank=True)

    # Results
    output = models.TextField(blank=True, help_text="Task return value or stdout")
    error = models.TextField(blank=True, help_text="Error message if failed")
    traceback = models.TextField(blank=True, help_text="Full traceback if failed")
    termination_reason = models.CharField(
        max_length=100,
        blank=True,
        help_text="Reason for job termination (signal, timeout, user action, etc.)",
    )

    class Meta:
        db_table = "sqlery_queued_job"
        ordering = ["-priority", "created_at"]
        verbose_name = "Job"
        verbose_name_plural = "Jobs"
        indexes = [
            models.Index(fields=["queue_name", "status", "-priority", "created_at"]),
            models.Index(fields=["task_path", "status"]),
            models.Index(fields=["-created_at"], name="sqlery_job_created_desc"),
            models.Index(fields=["-finished_at"], name="sqlery_job_finished_desc"),
            models.Index(fields=["-started_at"], name="sqlery_job_started_desc"),
        ]

    def __str__(self):
        task_name = (
            self.scheduled_task.name if self.scheduled_task else self.task_path.split(".")[-1]
        )
        return f"{task_name} [{self.queue_name}] - {self.status}"

    def get_status(self) -> str:
        """Return the job status (RQ compat)."""
        return self.status

    def mark_running(self):
        """Mark job as running and record worker PID.

        Uses optimistic locking to ensure atomic state transition.

        Raises:
            ConcurrentModificationError: If job was modified by another process
        """
        # import os  # moved to top-level

        # from django.db.models import F  # moved to top-level

        expected_version = self.version

        rows_updated = QueuedJob.objects.filter(id=self.id, version=expected_version).update(
            status="running",
            started_at=timezone.now(),
            worker_pid=os.getpid(),
            version=F("version") + 1,
        )

        if rows_updated == 0:
            raise ConcurrentModificationError(
                f"Job {self.id} was modified by another process (version conflict)"
            )

        self.refresh_from_db()

    def mark_success(self, output=""):
        """Mark job as successful.

        Uses optimistic locking to ensure atomic state transition.

        Raises:
            ConcurrentModificationError: If job was modified by another process
        """
        # from django.db.models import F  # moved to top-level

        expected_version = self.version
        self.finished_at = timezone.now()
        self.duration_seconds = None
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

        # Record this run in history (update in-memory only)
        self._record_run(status="success", output=str(output))

        rows_updated = QueuedJob.objects.filter(id=self.id, version=expected_version).update(
            status="success",
            finished_at=self.finished_at,
            duration_seconds=self.duration_seconds,
            output=str(output),
            runs=self.runs,
            # worker FK kept for historical tracking (Worker.current_job is cleared separately)
            version=F("version") + 1,
        )

        if rows_updated == 0:
            raise ConcurrentModificationError(
                f"Job {self.id} was modified by another process (version conflict)"
            )

        self.refresh_from_db()

        # Clear worker.current_job so worker shows as idle
        self._release_worker()

        # Send webhook notification if configured
        if self.webhook_url:
            from .webhooks import send_webhook_with_retry

            send_webhook_with_retry(self, event="success")

    def mark_failed(self, error, traceback="", termination_reason=""):
        """Mark job as failed with optional termination reason.

        Uses optimistic locking to ensure atomic state transition.

        Args:
            error: Error message
            traceback: Full traceback if available
            termination_reason: Human-readable reason for termination
                              (e.g., "timeout", "killed_by_user", "sigterm", "sigkill", "cancelled")

        Raises:
            ConcurrentModificationError: If job was modified by another process
        """
        # from django.db.models import F  # moved to top-level

        expected_version = self.version
        self.finished_at = timezone.now()
        self.duration_seconds = None
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

        # Record this run in history (update in-memory only)
        self._record_run(status="failed", error=str(error))

        rows_updated = QueuedJob.objects.filter(id=self.id, version=expected_version).update(
            status="failed",
            finished_at=self.finished_at,
            duration_seconds=self.duration_seconds,
            error=str(error),
            traceback=traceback,
            termination_reason=termination_reason,
            runs=self.runs,
            # worker FK kept for historical tracking (Worker.current_job is cleared separately)
            version=F("version") + 1,
        )

        if rows_updated == 0:
            raise ConcurrentModificationError(
                f"Job {self.id} was modified by another process (version conflict)"
            )

        self.refresh_from_db()

        # Clear worker.current_job so worker shows as idle
        self._release_worker()

        # Fail any jobs that depend on this one
        self.fail_dependent_jobs()

        # Send webhook notification if configured
        if self.webhook_url:
            from .webhooks import send_webhook_with_retry

            send_webhook_with_retry(self, event="failure")

    def force_stop(self) -> bool:
        """Mark this job as failed (displaced) and clear its worker assignment.

        Does NOT send SIGTERM — the worker will finish the current execution,
        detect the version mismatch in release_job, and continue to pick up
        the next queued job without any daemon restart delay.

        Returns True if the job was running and was stopped, False otherwise.
        Safe to call on any status.
        """
        if self.status != "running":
            return False

        try:
            self.mark_failed(
                error="Displaced by newer job with same name", termination_reason="displaced"
            )
        except Exception:
            pass

        # Reset worker state so it shows idle immediately on the dashboard
        worker = Worker.objects.filter(current_job=self, status="busy").first()
        if worker:
            worker.status = "idle"
            worker.current_job = None
            worker.save(update_fields=["status", "current_job", "last_heartbeat"])

        return True

    def _release_worker(self):
        """Clear worker.current_job and set worker to idle after job finishes.

        Uses filter(current_job_id=self.id) so it only clears if the worker
        is still pointing at THIS job (avoids race with a new claim).
        """
        if not self.worker_id:
            return
        try:
            # from django.apps import apps  # moved to top-level

            Worker = apps.get_model("sqlery", "Worker")
            Worker.objects.filter(id=self.worker_id, current_job_id=self.id).update(
                status="idle",
                current_job=None,
                last_heartbeat=timezone.now(),
            )
        except Exception:
            pass

    def _record_run(self, status, output="", error=""):
        """Record execution attempt in runs history.

        Args:
            status: Run status (success/failed)
            output: Task output if successful
            error: Error message if failed
        """
        run_record = {
            "attempt_number": self.retry_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": status,
            "duration": self.duration_seconds,
            "output": output[:2000] if output else "",  # Limit to 2000 chars
            "error": error[:2000] if error else "",  # Limit to 2000 chars
        }

        # Initialize runs list if needed
        if not isinstance(self.runs, list):
            self.runs = []

        self.runs.append(run_record)

    def should_retry(self):
        """Check if job should be retried after failure.

        Returns:
            bool: True if job should be retried
        """
        return (
            self.status == "failed" and self.max_retries > 0 and self.retry_count < self.max_retries
        )

    def save_meta(self) -> None:
        """Persist the in-memory meta dict to the database."""
        QueuedJob.objects.filter(pk=self.pk).update(meta=self.meta)

    def refresh_meta(self) -> None:
        """Reload meta from the database into this instance."""
        self.refresh_from_db(fields=["meta"])

    @classmethod
    def get_by_name(cls, job_name: str) -> "QueuedJob | None":
        """Look up a job by its unique string name.

        Returns:
            QueuedJob if found, None otherwise.
        """
        try:
            return cls.objects.get(job_name=job_name)
        except cls.DoesNotExist:
            return None

    def calculate_retry_delay(self):
        """Calculate delay before next retry.

        Uses fixed retry_intervals list when set; falls back to exponential backoff.

        Returns:
            float: Seconds to wait before retry
        """
        if self.retry_intervals:
            idx = min(self.retry_count, len(self.retry_intervals) - 1)
            return float(self.retry_intervals[idx])
        # Exponential backoff: retry_backoff * (2 ^ retry_count)
        return self.retry_backoff * (2**self.retry_count)

    def check_dependencies_met(self):
        """Check if all job dependencies have completed successfully.

        Returns:
            tuple: (bool, list) - (all_met, failed_dependencies)
                all_met: True if all dependencies are successful
                failed_dependencies: List of failed dependency job IDs
        """
        if not self.dependencies:
            return True, []

        # Get all dependency jobs
        dependency_jobs = QueuedJob.objects.filter(id__in=self.dependencies)

        # Check if we found all dependencies
        found_ids = set(dependency_jobs.values_list("id", flat=True))
        missing = set(self.dependencies) - found_ids
        if missing:
            logger.warning(f"Job {self.id} has missing dependencies: {missing}")
            return False, list(missing)

        # Check statuses
        failed_deps = []
        for dep in dependency_jobs:
            if dep.status == "failed":
                failed_deps.append(dep.id)
            elif dep.status != "success":
                # Dependency not yet complete
                return False, []

        if failed_deps:
            return False, failed_deps

        return True, []

    def then(self, task_path, **kwargs):
        """Chain another job to run after this one completes successfully.

        Fluent API for creating job dependencies.

        Args:
            task_path: Path to task function
            **kwargs: Arguments to pass to the task

        Returns:
            QueuedJob: The newly created dependent job

        Example:
            job1 = extract_data.enqueue()
            job2 = job1.then('myapp.tasks.transform_data', input_file='data.csv')
            job3 = job2.then('myapp.tasks.load_data')
        """
        from .api import enqueue

        # Create new job that depends on this one
        dependent_job = enqueue(
            task_path,
            depends_on=[self.id],
            queue=kwargs.pop("queue", self.queue_name),
            priority=kwargs.pop("priority", self.priority),
            **kwargs,
        )

        return dependent_job

    def fail_dependent_jobs(self):
        """Mark all jobs that depend on this job as failed.

        Called when this job fails to cascade the failure to dependent jobs.
        """
        # # Old: loaded ALL queued jobs into memory to filter in Python
        # queued_jobs = QueuedJob.objects.filter(status="queued")
        # dependent_jobs = [
        #     job for job in queued_jobs
        #     if job.dependencies and self.id in job.dependencies
        # ]
        # from .db_compat import is_sqlite  # moved to top-level

        if is_sqlite():
            # SQLite doesn't support JSON __contains — filter in Python
            queued_jobs = (
                QueuedJob.objects.filter(
                    status="queued",
                )
                .exclude(dependencies=[])
                .exclude(dependencies__isnull=True)
            )
            dependent_jobs = [
                job for job in queued_jobs if job.dependencies and self.id in job.dependencies
            ]
        else:
            # PostgreSQL: use JSON containment for efficient DB-level filtering
            dependent_jobs = list(
                QueuedJob.objects.filter(
                    status="queued",
                    dependencies__contains=[self.id],
                )
            )

        for job in dependent_jobs:
            job.mark_failed(
                error=f"Dependency failed: job {self.id}", termination_reason="dependency_failed"
            )
            logger.info(f"Failed dependent job {job.id} because dependency {self.id} failed")


class JobRegistry(models.Model):
    """Track job lifecycle in registries (RQ-compatible).

    Schema synchronized with core.models.JobRegistry (SQLModel).
    """

    REGISTRY_TYPES = [
        ("started", "Started"),
        ("finished", "Finished"),
        ("failed", "Failed"),
        ("scheduled", "Scheduled"),
        ("deferred", "Deferred"),
        ("canceled", "Canceled"),
    ]

    job = models.ForeignKey(
        QueuedJob,
        on_delete=models.CASCADE,
        related_name="registry_entries",
        help_text="Job being tracked",
    )
    registry_type = models.CharField(
        max_length=20,
        choices=REGISTRY_TYPES,
        db_index=True,
        help_text="Registry type",
    )
    entered_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When job entered this registry",
    )
    exited_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When job exited this registry (NULL = still active)",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Additional metadata (error info, duration, etc.)",
    )

    class Meta:
        db_table = "sqlery_registry"
        ordering = ["-entered_at"]
        indexes = [
            models.Index(fields=["registry_type", "entered_at"]),
            models.Index(fields=["job", "registry_type"]),
            models.Index(fields=["registry_type", "exited_at"]),
        ]
        verbose_name_plural = "Job registries"

    def __str__(self):
        return f"{self.job.id} in {self.registry_type} registry"


class Worker(models.Model):
    """A worker process that executes jobs from the queue.

    Schema synchronized with core.models.Worker (SQLModel).
    Uses UUID7 for time-sortable primary keys.
    """

    STATUS_CHOICES = [
        ("idle", "Idle"),
        ("busy", "Busy"),
        ("dead", "Dead"),
    ]

    # Worker identification (UUID7 for time-sortable UUIDs)
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    node_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Hostname or container ID where worker is running",
    )
    pid = models.IntegerField(help_text="Process ID of worker")

    # Status
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="idle",
        db_index=True,
    )
    current_job = models.ForeignKey(
        QueuedJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_worker",
        help_text="Job currently being processed",
    )

    # Configuration
    queues = models.JSONField(
        default=list,
        encoder=DjangoJSONEncoder,
        help_text="List of queue names this worker handles",
    )

    # Heartbeat and lifecycle
    last_heartbeat = models.DateTimeField(
        auto_now=True,
        db_index=True,
        help_text="Last time worker sent heartbeat",
    )
    started_at = models.DateTimeField(auto_now_add=True)

    # Pause support
    paused_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="If set, worker will not claim jobs until this time",
    )

    # Statistics
    jobs_processed = models.IntegerField(
        default=0,
        help_text="Total number of jobs processed by this worker",
    )
    total_busy_seconds = models.FloatField(
        default=0.0,
        help_text="Cumulative seconds spent executing jobs (tracked by parent process)",
    )

    class Meta:
        db_table = "sqlery_worker"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["node_id", "status"]),
            models.Index(fields=["status", "last_heartbeat"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["node_id", "pid"], name="unique_worker_per_node_pid")
        ]

    @property
    def friendly_name(self):
        # from .friendly_name import uuid_to_friendly  # moved to top-level

        return uuid_to_friendly(self.id)

    def __str__(self):
        return f"{self.friendly_name} [{self.status}] on {self.node_id}"

    def is_alive(self, timeout_seconds=30):
        """Check if worker is alive based on heartbeat."""
        if self.status == "dead":
            return False
        threshold = timezone.now() - timezone.timedelta(seconds=timeout_seconds)
        return self.last_heartbeat >= threshold


class DaemonCommand(models.Model):
    """Commands from frontend/API to the daemon process.

    The daemon reads pending commands each cycle and executes them.
    Commands are fire-and-forget from the frontend's perspective —
    the frontend writes a row, the daemon picks it up.
    """

    COMMAND_CHOICES = [
        ('manual_intervention', 'Manual Intervention'),
        ('restart_workers', 'Restart Workers'),
        ('cleanup_now', 'Cleanup Now'),
        ('enforce_deadlines', 'Enforce Deadlines'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    command = models.CharField(max_length=100, choices=COMMAND_CHOICES)
    payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=20, default='pending', choices=STATUS_CHOICES)
    result = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sqlery_daemon_command"
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
        ]
        verbose_name = "Daemon Command"
        verbose_name_plural = "Daemon Commands"

    def __str__(self):
        return f"DaemonCommand({self.command} [{self.status}])"


class TagLock(models.Model):
    """Coordination point for tag-based rate limiting and concurrency.

    This small table provides exclusive locks for tag-based constraints,
    eliminating race conditions when multiple workers check rate limits.

    Each tag that appears in TAG_RATE_LIMITS or TAG_CONCURRENCY_LIMITS
    gets one row in this table. Workers acquire locks on these rows
    before checking limits, ensuring atomic check-and-claim operations.

    Example:
        TAG_RATE_LIMITS = {"acme-api": "60/m", "stripe-api": "100/s"}

        Table contents:
        tag
        ----------
        acme-api
        stripe-api
    """

    tag = models.CharField(
        max_length=255, primary_key=True, help_text="Tag name used in job.tags field"
    )

    class Meta:
        db_table = "sqlery_tag_lock"
        verbose_name = "Tag Lock"
        verbose_name_plural = "Tag Locks"

    def __str__(self):
        return f"TagLock: {self.tag}"


class DaemonLease(models.Model):
    """DB-backed lease for queue-scoped daemon coordinator ownership.

    One row per queue. The daemon that holds the lease is responsible for
    running the scheduler and zombie cleanup for that queue. Renewed every
    loop iteration; expires after check_interval × 3 seconds if the daemon
    dies without a clean shutdown.

    Schema divergence note (WR-05): the standalone SQLModel ``DaemonLease``
    (``core/models.py``) carries an extra ``version`` column for SQLite
    optimistic-CAS take-over. Django uses ``SELECT FOR UPDATE SKIP LOCKED`` and
    intentionally omits that column, so the two stacks produce slightly
    different DDL for the shared ``sqlery_daemon_lease`` table. A single
    database must not be migrated by both stacks.
    """

    queue_name = models.CharField(max_length=255, primary_key=True)
    daemon_id = models.CharField(max_length=255, help_text="daemon_{node_id}_{pid}")
    node_id = models.CharField(max_length=255)
    pid = models.IntegerField()
    acquired_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "sqlery_daemon_lease"
        verbose_name = "Daemon Lease"
        verbose_name_plural = "Daemon Leases"

    def __str__(self):
        return f"DaemonLease({self.queue_name} → {self.daemon_id})"


# Backward compatibility alias
TaskExecution = QueuedJob


# Signal handlers
@receiver(post_delete, sender=ScheduledTask)
def cleanup_eventbridge_rule(sender, instance, **kwargs):
    """Delete EventBridge rule when ScheduledTask is deleted.

    When a ScheduledTask is deleted, we need to clean up the corresponding
    EventBridge rule to prevent orphaned rules from continuing to fire.

    This only runs if TRIGGER_MODE='eventbridge', otherwise it's a no-op.
    """
    # from .settings import get_setting  # moved to top-level

    trigger_mode = get_setting("TRIGGER_MODE", "middleware")
    if trigger_mode != "eventbridge":
        return  # Not using EventBridge, nothing to clean up

    try:
        from .eventbridge_trigger import delete_eventbridge_rule

        delete_eventbridge_rule(instance.name)
        logger.info(f"Deleted EventBridge rule for ScheduledTask: {instance.name}")
    except ImportError:
        # boto3 not installed, can't clean up
        logger.warning(
            f"Cannot delete EventBridge rule for '{instance.name}': "
            f"boto3 not installed (pip install sqlery[eventbridge])"
        )
    except Exception as e:
        # Log but don't fail the delete operation
        logger.error(f"Failed to delete EventBridge rule for '{instance.name}': {e}", exc_info=True)
