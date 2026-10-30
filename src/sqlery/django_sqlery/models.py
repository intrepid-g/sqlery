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
from django.db import models, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from uuid6 import uuid7

from sqlery.core.attempts import (
    OUTCOME_ABANDONED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    AttemptHandle,
    attach_attempt,
    attempt_of,
    decide_close,
    new_token,
)
from sqlery.core.utils import reject_unawaited_coroutine
from sqlery.core.worker_admin import is_worker_beating

from .db_compat import is_sqlite


def _generate_job_id() -> int:
    """62-bit time-sortable id from a UUID v7 (default for QueuedJob.id).

    Resolves fields.E100 (BigAutoField under a CompositePrimaryKey) while keeping
    id assignment working on both SQLite and PostgreSQL without an AutoField.
    """
    return uuid7().int & ((1 << 62) - 1)
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


TERMINAL_STATUSES = ("success", "failed", "archived")

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

    @property
    def all_jobs(self):
        """Every job this task created, live and finished (``jobs`` is the live ones only)."""
        return JobAll.objects.filter(scheduled_task_id=self.pk)

    @property
    def recent_jobs(self):
        """The newest 200 jobs this task created, live and finished (admin change form)."""
        return list(self.all_jobs.order_by("-created_at", "-id")[:200])

    def get_kwargs_dict(self):
        """Get task_kwargs as a dict for passing to QueuedJob."""
        if isinstance(self.task_kwargs, dict):
            return self.task_kwargs.copy()
        return {}


def _open_attempt_exists():
    """``Exists`` of an open attempt for the outer job row (``OuterRef("id")``)."""
    return Exists(JobAttempt.objects.filter(job_id=OuterRef("id"), finished_at__isnull=True))


def _is_retry_marker(reason) -> bool:
    from sqlery.core.retry import is_retry_pending

    return is_retry_pending(reason)


class QueuedJobQuerySet(models.QuerySet):
    """QuerySet whose ``update()`` keeps the ready queue and the history in step (#139, #140).

    A bulk ``update(status=<terminal>)`` is a terminal transition: the rows are written with
    their final state and then move to ``sqlery_job_history`` in the same transaction (see
    ``update_to_terminal``), so nothing finished is ever left in the live table by the ORM.

    A bulk ``update(status="queued")`` makes jobs claimable again, and an update of
    ``queue_name`` / ``priority`` / ``scheduled_at`` changes where (or when) their ready
    entry is claimed. Every other update (running, success, failed...) passes straight
    through: an entry left behind by a job that stopped being queued is dropped by the next
    claim that meets it.
    """

    _READY_FIELDS = ("queue_name", "priority", "scheduled_at")

    def running(self):
        """Jobs that are running: a ``queued`` row with an open attempt (#157).

        The claim writes nothing to the job row, so ``filter(status="running")`` matches
        nothing; this is the one place that states the rule for the ORM.
        """
        return self.filter(status="queued").filter(_open_attempt_exists())

    def waiting(self):
        """Jobs that are queued and not running (no open attempt)."""
        return self.filter(status="queued").filter(~_open_attempt_exists())

    def fail_running(self, **fields):
        """Bulk-fail the running jobs of this queryset (a recovery sweep); returns how many.

        A job is running iff it has an open attempt, so the attempts are abandoned first (in
        the same transaction; a late completion from the old holder is then rejected) and the
        terminal update, with ``fields`` (``error``, ``termination_reason``, ``finished_at``...),
        is applied to the jobs that were running. They move to history like any final state.
        """
        with transaction.atomic(using=self.db):
            ids = list(self.running().values_list("id", flat=True))
            if not ids:
                return 0
            now = timezone.now()
            JobAttempt.objects.filter(job_id__in=ids, finished_at__isnull=True).update(
                finished_at=now, outcome=OUTCOME_ABANDONED
            )
            return self.model.objects.filter(id__in=ids, status="queued").update(
                status="failed", **fields
            )

    def list_shown(self):
        """Evaluate to a list, each job that has an open attempt shown as running (start time,
        worker and PIDs from the attempt, in memory; see ``QueuedJob.show_running``). One extra
        query for all of them."""
        jobs = list(self)
        attempts = {
            a.job_id: a
            for a in JobAttempt.objects.filter(
                job_id__in=[j.id for j in jobs if j.status == "queued"], finished_at__isnull=True
            ).order_by("id")
        }
        for job in jobs:
            job.show_running(attempts.get(job.id))
        return jobs

    def update(self, **kwargs):
        if kwargs.get("status") in TERMINAL_STATUSES:
            return self._update_to_terminal(kwargs)
        requeue = kwargs.get("status") == "queued"
        edits = {
            k: v
            for k, v in kwargs.items()
            if k in self._READY_FIELDS and not hasattr(v, "resolve_expression")
        }
        if not requeue and not edits:
            return super().update(**kwargs)
        from . import ready

        with transaction.atomic(using=self.db):
            rows = list(
                self.values_list("id", "created_at", "queue_name", "priority", "scheduled_at")
            )
            # Entries first, then the job rows: the lock order of a claim.
            ready.before_update(rows, edits)
            count = super().update(**kwargs)
            ready.after_update(rows, requeue=requeue, edits=edits)
        return count

    def update_in_place(self, **kwargs):
        """``update()`` without the ready-queue / history bookkeeping.

        For the few writes that mark a job finished but must keep it in the live table for
        now: a failure that still owes its retry, or a finished job whose webhook delivery is
        being attempted. Everything else goes through ``update()``.
        """
        return super().update(**kwargs)

    def _update_to_terminal(self, kwargs):
        """Bulk terminal transition: write the final state, then move the rows to history.

        A failure that still owes a retry (``retry_pending`` marker) is not final and stays.
        """
        from . import history, ready

        keep_live = _is_retry_marker(kwargs.get("termination_reason"))
        with transaction.atomic(using=self.db):
            rows = list(self.values_list("id", "created_at", "status"))
            ids = [r[0] for r in rows]
            # Entries first, then the job rows: the lock order of a claim.
            if any(r[2] == "queued" for r in rows):
                ready.remove(ids)
            count = super().update(**kwargs)
            if not keep_live:
                history.move_current([(i, c, kwargs["status"]) for i, c, _ in rows])
        if not keep_live:
            for job_id in ids:
                ready.release_dependents_quietly(job_id)
        return count


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

    # Django 5.2 composite PK — partition key (created_at) must be first.
    # id stays globally unique (shared IDENTITY sequence with sqlery_scheduled_job).
    # FKs from JobRegistry and Worker demoted to BigIntegerField per D4 (Phase 15):
    # orphans on partition drop are an accepted, documented trade-off.
    pk = models.CompositePrimaryKey("created_at", "id")
    # Old: id = models.BigAutoField(primary_key=False)
    #   BigAutoField under a CompositePrimaryKey raises fields.E100 ("AutoFields
    #   must set primary_key=True"), which blocks `manage.py check`/`migrate` in
    #   real Django apps. The id value is DB-assigned — nextval('sqlery_job_id_seq')
    #   on Postgres (migrations 0029-0030) and an autoincrement rowid on SQLite —
    #   so a plain BigIntegerField carries the column without the AutoField check.
    #   A 62-bit UUID-v7-derived default assigns the id on insert for BOTH backends
    #   (mirrors the standalone SQLModel listener) — globally unique, time-sortable,
    #   no collision with the Postgres sequence (which still backs raw-SQL inserts).
    id = models.BigIntegerField(default=_generate_job_id, editable=False)

    objects = QueuedJobQuerySet.as_manager()

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

    # Retry chain linkage. BigIntegerField (not IntegerField) because it stores a
    # QueuedJob id, which is a 64-bit _generate_job_id() value — an int4 column
    # overflows ("integer out of range") the moment a failed job spawns a retry.
    parent_job_id = models.BigIntegerField(
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
    # Old: unique=True — cannot exist on a PG partitioned table without including the
    # partition key (created_at) in the constraint.  Uniqueness is enforced at application
    # level in backend.create_job: named job always wins (stop + delete conflicts before
    # insert).  unique=True removed by migration 0030 state_operations AlterField to keep
    # ORM state in sync with the physical schema after the partitioned cutover.
    # job_name = models.CharField(
    #     max_length=255,
    #     null=True,
    #     blank=True,
    #     unique=True,
    #     db_index=True,
    #     help_text="Optional unique string identifier (e.g. 'send-invoice-123')",
    # )
    job_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
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
    # Not written by a claim since #157 (the open attempt names the worker); NULL for new jobs.
    # db_index=False: the partial index below (migration 0042) serves ON DELETE SET NULL.
    worker = models.ForeignKey(
        "Worker",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=False,
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
            # Old: models.Index(fields=["queue_name", "status", "-priority", "created_at"]),
            # Dropped (migration 0038): models.Index(fields=["queue_name", "-priority",
            # "created_at"], name="sqlery_job_pending_idx", condition=Q(status="queued")).
            # Claiming reads the ready queue (sqlery_job_ready), not this table (#139).
            # Dropped (migration 0036): ("task_path", "status") -- no query filters on
            # task_path and `status` churns on every transition; and ("-created_at") --
            # served by the (created_at, id) primary key.
            # Dropped (migration 0039): ("-finished_at") and ("-started_at"). Finished jobs
            # are in sqlery_job_history, which has its own indexes (#140); a live job is found
            # by status.
            # Replaced (migration 0042): the full ("worker") index by a partial one. The claim
            # does not write the worker to the row any more (#157), so only legacy rows have
            # one; the index still serves the SET NULL of a deleted Worker.
            models.Index(
                fields=["worker"],
                name="sqlery_qj_worker_id_idx",
                condition=Q(worker__isnull=False),
            ),
        ]

    def __str__(self):
        task_name = (
            self.scheduled_task.name if self.scheduled_task else self.task_path.split(".")[-1]
        )
        return f"{task_name} [{self.queue_name}] - {self.status}"

    def get_status(self) -> str:
        """Return the job status (RQ compat): ``running`` for a job with an open attempt."""
        if self.status == "queued" and "_stored_row" not in self.__dict__:
            if self.open_attempt() is not None:
                return "running"
        return self.status

    _READY_RELEVANT = frozenset({"status", "queue_name", "priority", "scheduled_at"})

    # What a running job shows (derived from its open attempt) but its row does not hold.
    _DERIVED_FIELDS = ("status", "started_at", "worker_id", "worker_pid", "child_pid")

    def show_running(self, attempt):
        """Show this job as the running job its open ``attempt`` makes it, in memory only.

        The claim writes nothing to the job row (#157): the row says ``queued`` with no start
        time or worker, and the attempt says the rest. The values shown here never reach the
        row: ``save()`` writes the stored ones back in their place unless the caller changed
        them after this call. Returns ``self``.
        """
        if attempt is None:
            return self
        if "_stored_row" not in self.__dict__:
            self.__dict__["_stored_row"] = {n: getattr(self, n) for n in self._DERIVED_FIELDS}
        self.status = "running"
        self.started_at = attempt.claimed_at
        self.worker_id = attempt.worker_id
        self.worker_pid = attempt.worker_pid
        self.child_pid = attempt.child_pid
        self.__dict__["_shown_row"] = {n: getattr(self, n) for n in self._DERIVED_FIELDS}
        return self

    def hide_running(self):
        """Undo ``show_running``: the object shows the row as stored again (it is queued)."""
        for name, value in self.__dict__.get("_stored_row", {}).items():
            setattr(self, name, value)
        self._forget_running()

    def _forget_running(self):
        """The object now holds a real (final) state: nothing shown is derived any more."""
        self.__dict__.pop("_stored_row", None)
        self.__dict__.pop("_shown_row", None)

    def open_attempt(self):
        """The open attempt of this job (it is running), or None."""
        return JobAttempt.open_for(self.id)

    def save(self, *args, **kwargs):
        """Save the job; values shown from the open attempt (``show_running``) are not written.

        A field that still holds exactly what ``show_running`` put there is saved as the row
        had it; one the caller changed since is saved as they changed it.
        """
        stored = self.__dict__.get("_stored_row")
        if stored is None:
            return self._save_row(*args, **kwargs)
        shown = self.__dict__.get("_shown_row", {})
        swapped = {n: getattr(self, n) for n in stored if getattr(self, n) == shown.get(n)}
        for name in swapped:
            setattr(self, name, stored[name])
        try:
            return self._save_row(*args, **kwargs)
        finally:
            if "_stored_row" in self.__dict__:  # not cleared by a terminal transition
                for name, value in swapped.items():
                    setattr(self, name, value)

    def _save_row(self, *args, **kwargs):
        """Save the job and keep its ready-queue entry in step, in the same transaction.

        A job saved as ``queued`` gets (or keeps) its ready entry; one saved as anything
        else loses it. Saves that touch none of status / queue / priority / schedule skip
        the extra statements.
        """
        if getattr(self, "_in_history", False):
            raise TypeError(
                f"job {self.id} is in history, which is insert-only; a finished job can not be "
                "saved again"
            )
        update_fields = kwargs.get("update_fields")
        adding = self._state.adding
        from . import ready

        terminal = self.status in TERMINAL_STATUSES and not _is_retry_marker(
            self.termination_reason
        )
        if terminal and (adding or update_fields is None or "status" in update_fields):
            # A job saved with a final status is a terminal transition: a new one is recorded
            # in history directly, an existing one is written and then moved there.
            return self._save_terminal(adding, args, kwargs)
        if not (
            adding or update_fields is None or self._READY_RELEVANT & set(update_fields)
        ):
            return super().save(*args, **kwargs)

        with transaction.atomic(using=kwargs.get("using")):
            # Lock order everywhere is ready entry first, then the job row (what a claim
            # does), so edits to an existing entry come before the job write.
            entry_updated = False if adding else ready.before_save(self)
            super().save(*args, **kwargs)
            ready.after_save(self, adding=adding, entry_updated=entry_updated)

    def _save_terminal(self, adding, args, kwargs):
        from . import history, ready

        with transaction.atomic(using=kwargs.get("using")):
            if adding:
                if self.finished_at is None:
                    self.finished_at = timezone.now()
                history.insert_finished(self)
                self._state.adding = False
                return
            ready.before_save(self)  # drops the entries of a job that is not queued
            super().save(*args, **kwargs)
            self._in_history = history.move(self.id, self.created_at, self.status, {})
        self._forget_running()
        ready.release_dependents_quietly(self.id, self.parent_job_id)

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        """Reload from the database; a job that has since finished is read from history.

        The object stays what it was (a ``QueuedJob``) but carries the final state, so code
        holding a job across its completion keeps working.
        """
        if fields is not None and set(fields) & {"status", "started_at", "worker", "worker_id"}:
            # Reloading any of what a running job shows reloads all of it (see show_running).
            fields = list(dict.fromkeys([*fields, *self._DERIVED_FIELDS]))
        if fields is None or "status" in fields:
            self._forget_running()
        try:
            result = super().refresh_from_db(using=using, fields=fields, **kwargs)
        except QueuedJob.DoesNotExist:
            row = JobHistory.objects.filter(id=self.id).first()
            if row is None:
                raise
            self._in_history = True
            names = fields or [f.attname for f in self._meta.concrete_fields if f.name != "pk"]
            for name in names:
                attname = self._meta.get_field(name).attname if name != "pk" else name
                if hasattr(row, attname):
                    setattr(self, attname, getattr(row, attname))
            return None
        if (fields is None or "status" in fields) and self.status == "queued":
            # A queued row with an open attempt is a running job (the claim leaves the row alone).
            self.show_running(self.open_attempt())
        return result

    def delete(self, *args, **kwargs):
        """Delete the job together with its ready / blocked entries; jobs that waited on it
        are re-checked (they fail, as a missing dependency always failed a job)."""
        from . import ready

        job_id, parent_job_id = self.id, self.parent_job_id
        with transaction.atomic(using=kwargs.get("using")):
            ready.remove([job_id])  # entries first: the lock order of a claim
            result = super().delete(*args, **kwargs)
        ready.release_dependents_quietly(job_id, parent_job_id)
        return result

    def mark_running(self):
        """Mark job as running: open its attempt and drop its ready / blocked entries.

        The job row is not written (a job is running iff it has an open attempt, see
        ``QueuedJob.objects.running()``). Fenced like a claim: the row is locked and must still
        be the queued job this object read (same version) with no attempt open.

        Raises:
            ConcurrentModificationError: If job was modified by another process, or is
                running already
        """
        # import os  # moved to top-level
        expected_version = self.version

        with transaction.atomic():
            rows = QueuedJob.objects.filter(
                id=self.id,
                created_at=self.created_at,  # checklist item 3
                version=expected_version,
                status="queued",
            )
            if not is_sqlite():
                rows = rows.select_for_update(skip_locked=True)
            if not list(rows.values_list("id", flat=True)[:1]) or JobAttempt.open_for(self.id):
                raise ConcurrentModificationError(
                    f"Job {self.id} was modified by another process (version conflict)"
                )

            # A running job is not claimable: drop its ready / blocked entries (without
            # waiting on a claimer that holds the entry; see ready.remove).
            from . import ready

            ready.remove([self.id], wait=False)
            JobAttempt.open_new(self, self.worker_id, os.getpid())

        self.refresh_from_db()

    def _close_attempt(self, attempt, outcome):
        """Close the attempt this completion belongs to (inside the completion transaction).

        With a handle (the claimed job's), only that exact attempt may be closed: a stale
        completion raises ``JobFencingError`` before the job row is touched. Without one
        (unfenced callers: admin, recovery, a job that was never claimed), every open attempt
        of the job is closed; a failure there means the attempt was abandoned.
        """
        attempt = attempt or attempt_of(self)
        if attempt is not None:
            JobAttempt.close(attempt, outcome)
        else:
            JobAttempt.close_open(
                self.id, OUTCOME_SUCCESS if outcome == OUTCOME_SUCCESS else OUTCOME_ABANDONED
            )

    def _record_final(self, status, in_history=True, **fields):
        """Carry the final state in memory after the terminal transition (the row itself is
        in history, or marked in place, by now)."""
        self._forget_running()  # the final state is real, not derived from an attempt
        self.status = status
        self.version = (self.version or 0) + 1
        self._in_history = in_history
        for name, value in fields.items():
            setattr(self, name, value)

    def _attempt_columns(self) -> dict:
        """What the job row will record about the attempt that is finishing (``started_at``,
        ``worker_id``, ``worker_pid``, ``child_pid``): the claim wrote none of it to the row.

        Read from the object when it shows a running job (``show_running``), else from the open
        attempt; empty when there is none (the history move then falls back to the newest
        attempt, or the row's own values).
        """
        if "_stored_row" in self.__dict__:
            return {n: getattr(self, n) for n in ("started_at", "worker_id", "worker_pid", "child_pid")}
        attempt = self.open_attempt()
        if attempt is None:
            return {}
        return {
            "started_at": attempt.claimed_at,
            "worker_id": attempt.worker_id,
            "worker_pid": attempt.worker_pid,
            "child_pid": attempt.child_pid,
        }

    def mark_success(self, output="", attempt=None):
        """Mark job as successful: the job moves from the live table to history.

        The transition is one transaction with the attempt close, fenced by the optimistic
        locking version (``INSERT INTO sqlery_job_history .. SELECT`` + ``DELETE``, see
        ``sqlery.django_sqlery.history``); a stale completion writes nothing to history. The
        object keeps the final state in memory. A job whose webhook delivery is still to be
        attempted stays in the live table, marked ``success``, until the delivery resolves
        (``settle_finished``).

        Raises:
            ConcurrentModificationError: If job was modified by another process
            JobFencingError: If the attempt is no longer open (completion is stale)
        """
        # from django.db.models import F  # moved to top-level

        # REGRESSION 2026-08-08: guard the shared success-recording choke point,
        # not just individual executor call sites -- see REGRESSIONS.md
        reject_unawaited_coroutine(output)

        from . import history

        expected_version = self.version
        finished_at = timezone.now()
        columns = self._attempt_columns()
        started_at = columns.get("started_at") or self.started_at
        duration = None
        if started_at:
            duration = (finished_at - started_at).total_seconds()
        self.finished_at, self.duration_seconds = finished_at, duration
        if started_at and not self.started_at:
            self.started_at = started_at  # recorded in the run below

        # Record this run in history (update in-memory only)
        self._record_run(status="success", output=str(output))
        values = {
            "finished_at": finished_at,
            "duration_seconds": duration,
            "output": str(output),
            "runs": self.runs,
            **columns,
        }

        with transaction.atomic():
            fenced = (attempt or attempt_of(self)) is not None
            self._close_attempt(attempt, OUTCOME_SUCCESS)
            if self._awaits_webhook("success"):
                rows_updated = QueuedJob.objects.filter(
                    id=self.id, created_at=self.created_at, version=expected_version
                ).update_in_place(status="success", version=F("version") + 1, **values)
                moved = rows_updated == 1
            else:
                moved = history.move(
                    self.id,
                    self.created_at,
                    "success",
                    values,
                    expected_version=expected_version,
                    close_attempts=not fenced,  # its own attempt was closed just above
                )
            if not moved:
                raise ConcurrentModificationError(
                    f"Job {self.id} was modified by another process (version conflict)"
                )

        self._record_final("success", in_history=not self._awaits_webhook("success"), **values)

        # Clear worker.current_job so worker shows as idle
        self._release_worker()

        # Jobs waiting on this one are re-checked (moved back to the ready queue).
        from . import ready

        ready.release_dependents_quietly(self.id, self.parent_job_id)

        # Send webhook notification if configured
        if self._awaits_webhook("success"):
            from .webhooks import send_webhook_with_retry

            send_webhook_with_retry(self, event="success")

    def _awaits_webhook(self, event: str) -> bool:
        """True if a webhook delivery for ``event`` still has to be attempted: the job then
        stays in the live table until the delivery resolves (its bookkeeping is mutable)."""
        return bool(self.webhook_url)

    def settle_finished(self) -> bool:
        """Move this finished job from the live table into history if it is still there
        (called once its webhook delivery has resolved). True iff it moved."""
        from . import history, ready

        if self.status not in TERMINAL_STATUSES:
            return False
        moved = history.settle(self)
        if moved:
            ready.release_dependents_quietly(self.id, self.parent_job_id)
        return moved

    def mark_failed(
        self, error, traceback="", termination_reason="", attempt=None, idle_only=False
    ):
        """Mark job as failed with optional termination reason: the job moves to history.

        A failure that will be retried carries the ``retry_pending`` marker
        (``sqlery.core.retry``) and is not final: it stays in the live table, marked failed,
        until its retry exists (``mark_job_archived`` / ``settle_retry_pending`` then move
        it). The same holds for a job whose webhook delivery is still to be attempted.

        Uses optimistic locking to ensure atomic state transition.

        Args:
            error: Error message
            traceback: Full traceback if available
            termination_reason: Human-readable reason for termination
                              (e.g., "timeout", "killed_by_user", "sigterm", "sigkill", "cancelled")
            attempt: Attempt handle to close (default: the one this object was claimed with).
                Without any handle every open attempt of the job is abandoned.
            idle_only: The caller fails a job that is *waiting* (cancelled, expired, dependency
                failed) and must not touch one that has started since it looked: the failure
                then applies only while no attempt is open, else it is a lost race. The claim
                no longer changes the job row, so the version alone cannot tell.

        Raises:
            ConcurrentModificationError: If job was modified by another process
            JobFencingError: If the attempt is no longer open (completion is stale)
        """
        # from django.db.models import F  # moved to top-level

        from . import history

        expected_version = self.version
        finished_at = timezone.now()
        columns = {} if idle_only else self._attempt_columns()
        # Failed before it ran (cancelled, expired, dependency failed): a queued job that no
        # attempt ever opened. A running job shows as running (and has an open attempt).
        was_queued = idle_only or (self.status == "queued" and not columns)
        started_at = columns.get("started_at") or self.started_at
        duration = None
        if started_at:
            duration = (finished_at - started_at).total_seconds()
        self.finished_at, self.duration_seconds = finished_at, duration
        if started_at and not self.started_at:
            self.started_at = started_at  # recorded in the run below

        # Record this run in history (update in-memory only)
        self._record_run(status="failed", error=str(error))
        values = {
            "finished_at": finished_at,
            "duration_seconds": duration,
            "error": str(error),
            "traceback": traceback,
            "termination_reason": termination_reason,
            "runs": self.runs,
            **columns,
        }
        in_place = _is_retry_marker(termination_reason) or self._awaits_webhook("failure")

        with transaction.atomic():
            if was_queued:
                # Failed before it ran (cancelled, expired, dependency failed): it is no
                # longer claimable, so its ready / blocked entries go with the failure. Entries
                # first, then the job row: the lock order of a claim.
                from . import ready

                ready.remove([self.id])
            if not idle_only:
                self._close_attempt(attempt, OUTCOME_FAILED)
            if in_place:
                rows = QueuedJob.objects.filter(
                    id=self.id, created_at=self.created_at, version=expected_version
                )
                if idle_only:
                    rows = rows.filter(~_open_attempt_exists())
                rows_updated = rows.update_in_place(
                    status="failed", version=F("version") + 1, **values
                )
                moved = rows_updated == 1
            else:
                moved = history.move(
                    self.id,
                    self.created_at,
                    "failed",
                    values,
                    expected_version=expected_version,
                    require_idle=idle_only,
                )
            if not moved:
                raise ConcurrentModificationError(
                    f"Job {self.id} was modified by another process (version conflict)"
                )

        self._record_final("failed", in_history=not in_place, **values)

        # Clear worker.current_job so worker shows as idle
        self._release_worker()

        # Fail any jobs that depend on this one
        self.fail_dependent_jobs()

        # Send webhook notification if configured
        if self._awaits_webhook("failure"):
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
        # Old: worker = Worker.objects.filter(current_job=self, status="busy").first()
        worker = Worker.objects.filter(current_job_id=self.id, status="busy").first()
        if worker:
            worker.status = "idle"
            # Old: worker.current_job = None
            worker.current_job_id = None
            # Old: worker.save(update_fields=["status", "current_job", "last_heartbeat"])
            worker.save(update_fields=["status", "current_job_id", "last_heartbeat"])

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
                # Old: current_job=None,
                current_job_id=None,
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
        # Old: QueuedJob.objects.filter(pk=self.pk).update(meta=self.meta)
        # created_at already present per Phase 15 — checklist item 6 verified
        QueuedJob.objects.filter(id=self.id, created_at=self.created_at).update(meta=self.meta)

    def refresh_meta(self) -> None:
        """Reload meta from the database into this instance."""
        self.refresh_from_db(fields=["meta"])

    @classmethod
    def get_by_name(cls, job_name: str) -> "QueuedJob | JobHistory | None":
        """Look up a job by its unique string name.

        Returns:
            QueuedJob if found, None otherwise.
        """
        try:
            return cls.objects.get(job_name=job_name)
        except cls.DoesNotExist:
            # A finished job with that name is in history (the newest one wins).
            return JobHistory.objects.filter(job_name=job_name).first()

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

        # Get all dependency jobs (live or finished: the union view)
        dependency_jobs = JobAll.objects.filter(id__in=self.dependencies)

        # Check if we found all dependencies
        found_ids = set(dependency_jobs.values_list("id", flat=True))
        missing = set(self.dependencies) - found_ids
        if missing:
            logger.warning(f"Job {self.id} has missing dependencies: {missing}")
            return False, list(missing)

        # Check statuses. A dependency that failed/was archived but has a retry attempt is
        # judged by its newest attempt, so a retrying step neither fails nor blocks forever.
        failed_deps = []
        for dep in dependency_jobs:
            hops = 0
            while dep.status in ("failed", "archived") and hops < 1000:
                child = (
                    JobAll.objects.filter(parent_job_id=dep.id)
                    .order_by("-created_at", "-id")
                    .first()
                )
                if child is None:
                    break
                dep, hops = child, hops + 1
            if dep.status == "archived":
                return False, []  # archived with no retry found: treat as not complete
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
                QueuedJob.objects.waiting()
                .exclude(dependencies=[])
                .exclude(dependencies__isnull=True)
            )
            dependent_jobs = [
                job for job in queued_jobs if job.dependencies and self.id in job.dependencies
            ]
        else:
            # PostgreSQL: use JSON containment for efficient DB-level filtering
            dependent_jobs = list(
                QueuedJob.objects.waiting().filter(dependencies__contains=[self.id])
            )

        for job in dependent_jobs:
            try:
                job.mark_failed(
                    error=f"Dependency failed: job {self.id}",
                    termination_reason="dependency_failed",
                    idle_only=True,
                )
            except ConcurrentModificationError:
                continue  # started or changed since we looked: not ours to fail
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

    # Old: job = models.ForeignKey(QueuedJob, on_delete=models.CASCADE, related_name="registry_entries", help_text="Job being tracked")
    # #CLEANUP: Remove after soak — rename job_id back to job if FK ever restored
    job_id = models.BigIntegerField(
        db_index=True,
        help_text="ID of the QueuedJob being tracked (FK demoted — D4: referential integrity to partitioned table intentionally dropped)",
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
            # Old: models.Index(fields=["job", "registry_type"]),
            models.Index(fields=["job_id", "registry_type"]),
            models.Index(fields=["registry_type", "exited_at"]),
        ]
        verbose_name_plural = "Job registries"

    def __str__(self):
        # Old: return f"{self.job.id} in {self.registry_type} registry"
        return f"{self.job_id} in {self.registry_type} registry"


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
    # Old: current_job = models.ForeignKey(QueuedJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="current_worker", help_text="Job currently being processed")
    # #CLEANUP: Remove after soak — rename current_job_id back to current_job if FK ever restored
    current_job_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="ID of the QueuedJob currently being processed (FK demoted — D4: referential integrity to partitioned table intentionally dropped)",
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

    def is_alive(self, timeout_seconds=None):
        """Check if worker is alive based on heartbeat (see is_worker_beating)."""
        if timeout_seconds is None:
            timeout_seconds = get_setting("WORKER_ALIVE_TIMEOUT", 30)
        return is_worker_beating(
            self.status, self.last_heartbeat, timezone.now(), timeout_seconds
        )


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


class ScheduledJob(models.Model):
    """A staged far-future job in the scheduling table.

    Far-future jobs (scheduled_at > now() + staging threshold) land here
    instead of sqlery_queued_job so they cannot pin otherwise-drained partitions.
    The daemon promotion loop moves rows from this table into sqlery_queued_job
    once scheduled_at is within the lookahead window.

    The id column shares sqlery_queued_job_id_seq on PostgreSQL (wired in
    migration 0029) so ids are globally unique across both tables.
    """

    id = models.BigAutoField(primary_key=True)
    queue_name = models.CharField(
        max_length=50,
        default="default",
        help_text="Queue name for job routing",
    )
    task_path = models.CharField(
        max_length=500,
        help_text="Python path to callable",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Serialised job kwargs dict",
    )
    scheduled_at = models.DateTimeField(
        help_text="UTC datetime when this job becomes due for promotion",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Priority for enqueued jobs (higher = sooner)",
    )
    max_retries = models.IntegerField(
        default=0,
        help_text="Max retry attempts after failure",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sqlery_scheduled_job"
        ordering = ["scheduled_at"]
        verbose_name = "Staged scheduled job"
        verbose_name_plural = "Staged scheduled jobs"
        indexes = [
            models.Index(fields=["scheduled_at"], name="sqlery_staged_job_sched_idx"),
        ]

    def __str__(self):
        return f"ScheduledJob {self.id} → {self.task_path} at {self.scheduled_at}"


def _attempt_outcome_check():
    """CheckConstraint kwargs across Django versions (``check`` became ``condition`` in 5.1)."""
    import django

    q = Q(outcome__isnull=True) | Q(
        outcome__in=[OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_ABANDONED]
    )
    key = "condition" if django.VERSION >= (5, 1) else "check"
    return models.CheckConstraint(name="sqlery_attempt_outcome_chk", **{key: q})


class JobAttempt(models.Model):
    """One execution attempt of a job: inserted on claim, closed exactly once.

    ``job_id`` is a plain indexed column, not a foreign key: on partitioned PostgreSQL the
    ``sqlery_queued_job`` primary key is ``(created_at, id)``, so ``id`` alone cannot be
    referenced. A completion closes the attempt with a compare-and-set on ``(id, token)``
    where ``finished_at IS NULL``; zero rows updated means the completion is stale (see
    ``sqlery.core.attempts``). Mirrors the SQLModel ``JobAttempt``.
    """

    id = models.BigAutoField(primary_key=True)
    job_id = models.BigIntegerField(help_text="Claimed job (no FK: partitioned PK)")
    token = models.CharField(max_length=36, help_text="Random fencing token of this attempt")
    worker_id = models.UUIDField(null=True, blank=True, help_text="Claiming worker, if registered")
    worker_pid = models.IntegerField(null=True, blank=True, help_text="PID of the claiming process")
    child_pid = models.IntegerField(null=True, blank=True, help_text="PID of the forked child")
    claimed_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(
        max_length=10, null=True, blank=True, help_text="success / failed / abandoned"
    )

    class Meta:
        db_table = "sqlery_job_attempt"
        indexes = [
            models.Index(fields=["job_id"], name="sqlery_attempt_job_idx"),
            models.Index(
                fields=["worker_id"],
                name="sqlery_attempt_open_worker_idx",
                condition=Q(finished_at__isnull=True),
            ),
            # The open attempts by job: "is this job running", "which jobs are running" (#157).
            models.Index(
                fields=["job_id"],
                name="sqlery_attempt_open_job_idx",
                condition=Q(finished_at__isnull=True),
            ),
        ]
        constraints = [_attempt_outcome_check()]

    def __str__(self):
        return f"JobAttempt {self.id} (job {self.job_id}, {self.outcome or 'open'})"

    @classmethod
    def open_for(cls, job_id):
        """The open attempt of ``job_id`` (newest if several), or None."""
        return cls.objects.filter(job_id=job_id, finished_at__isnull=True).order_by("-id").first()

    @classmethod
    def latest_for(cls, job_id):
        """The newest attempt of ``job_id`` (open or finished), or None."""
        return cls.objects.filter(job_id=job_id).order_by("-id").first()

    @classmethod
    def process_ids(cls, job):
        """``(worker_pid, child_pid)`` of a running job, read from its open attempt.

        Falls back to the legacy job-row columns for rows claimed before attempts existed.
        """
        attempt = cls.open_for(job.id)
        if attempt is not None:
            return attempt.worker_pid, attempt.child_pid
        return job.worker_pid, job.child_pid

    @classmethod
    def worker_pids_for(cls, jobs):
        """``{job_id: worker_pid}`` of each job's newest attempt, in one query (dashboards)."""
        ids = [j.id for j in jobs]
        pids = {}
        if ids:
            rows = cls.objects.filter(job_id__in=ids).order_by("id")
            pids = {r.job_id: r.worker_pid for r in rows}  # newest attempt wins
        return {j.id: pids.get(j.id, j.worker_pid) for j in jobs}

    @classmethod
    def close(cls, handle, outcome):
        """Close ``handle``'s attempt (compare-and-set on id+token); raise if it is stale."""
        rows = cls.objects.filter(
            id=handle.id, token=handle.token, finished_at__isnull=True
        ).update(finished_at=timezone.now(), outcome=outcome)
        decide_close(rows, handle)

    @classmethod
    def close_open(cls, job_id, outcome):
        """Close every open attempt of ``job_id`` (recovery / unfenced completions)."""
        return cls.objects.filter(job_id=job_id, finished_at__isnull=True).update(
            finished_at=timezone.now(), outcome=outcome
        )

    @classmethod
    def open_new(cls, job, worker_id, worker_pid):
        """Insert the attempt for a claim of ``job`` and attach its handle to the object.

        Must run inside the claim transaction, after the claimer has verified (job row locked)
        that no attempt is open for the job: an open attempt *is* the running state (#157), so
        none is abandoned here. The object is shown as running (``show_running``); its row is
        not written.
        """
        row = cls.objects.create(
            job_id=job.id,
            token=new_token(),
            worker_id=worker_id,
            worker_pid=worker_pid,
        )
        attach_attempt(
            job,
            AttemptHandle(row.id, row.token, job.id, row.claimed_at, worker_id, worker_pid),
        )
        if hasattr(job, "show_running"):
            job.show_running(row)
        return attempt_of(job)


# Backward compatibility alias
TaskExecution = QueuedJob


class JobReady(models.Model):
    """A claimable job: one narrow entry per queued job that is neither waiting on
    dependencies nor already claimed (issue #139, see ``sqlery.core.ready``).

    A claim deletes the entry; an enqueue, retry, requeue or unblock inserts it. On
    PostgreSQL the table is ``PARTITION BY LIST (slot)`` (created by migration 0038); on
    SQLite it is a plain table. ``job_id`` is a plain column (no FK: partitioned PK).
    Mirrors the SQLModel ``JobReady``.
    """

    pk = models.CompositePrimaryKey("slot", "job_id")
    slot = models.SmallIntegerField(default=0)
    job_id = models.BigIntegerField()
    queue_name = models.CharField(max_length=50)
    priority = models.IntegerField(default=0)
    created_at = models.DateTimeField(help_text="The job's created_at (its partition key)")
    scheduled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sqlery_job_ready"
        indexes = [
            models.Index(
                fields=["queue_name", "-priority", "created_at", "job_id"],
                name="sqlery_ready_claim_idx",
            ),
        ]

    def __str__(self):
        return f"JobReady job {self.job_id} ({self.queue_name}, slot {self.slot})"


class JobBlocked(models.Model):
    """One row per dependency of a queued job whose dependencies are unmet (issue #139).

    The job has no ready entry while any row exists here; when a dependency reaches a
    terminal state the dependents are looked up by ``dependency_id`` and re-checked.
    ``created_at`` is the job's partition key, so the job is read by primary key.
    Mirrors the SQLModel ``JobBlocked``.
    """

    pk = models.CompositePrimaryKey("job_id", "dependency_id")
    job_id = models.BigIntegerField()
    dependency_id = models.BigIntegerField()
    created_at = models.DateTimeField(help_text="The blocked job's created_at")
    blocked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "sqlery_job_blocked"
        indexes = [
            models.Index(fields=["dependency_id"], name="sqlery_blocked_dependency_idx"),
        ]

    def __str__(self):
        return f"JobBlocked job {self.job_id} on {self.dependency_id}"


class _JobRowMixin:
    """Read helpers of a job row that is not (or no longer) live: shared by the history and
    the union-view models. Nothing here executes a job."""

    STATUS_CHOICES = QueuedJob.STATUS_CHOICES

    def __str__(self):
        return f"{self.task_path.split('.')[-1]} [{self.queue_name}] - {self.status}"

    def get_status(self) -> str:
        """Return the job status (RQ compat)."""
        return self.status

    def should_retry(self) -> bool:
        return False

    def calculate_retry_delay(self):
        return QueuedJob.calculate_retry_delay(self)

    @property
    def scheduled_task(self):
        """The scheduled task that created the job, if it still exists."""
        if self.scheduled_task_id is None:
            return None
        return ScheduledTask.objects.filter(pk=self.scheduled_task_id).first()

    @property
    def worker(self):
        if self.worker_id is None:
            return None
        return Worker.objects.filter(pk=self.worker_id).first()


def _job_row_fields(*, history: bool) -> dict:
    """Fields of a job row that is not live, cloned from ``QueuedJob`` so that history (and the
    union view) have exactly the columns the terminal transition copies.

    The two foreign keys become plain columns (the referenced row may be long gone), index
    flags are dropped (history has its own small index set, see ``sqlery.core.history``), and
    ``created_at`` is a plain timestamp. ``finished_at`` is the partition key of history, so
    it can not be NULL there.
    """
    import copy

    fields: dict = {}
    for f in QueuedJob._meta.concrete_fields:
        if f.name == "pk":
            continue  # the composite key of the live table; history has its own
        if f.name == "scheduled_task":
            fields["scheduled_task_id"] = models.IntegerField(null=True, blank=True)
            continue
        if f.name == "worker":
            fields["worker_id"] = models.UUIDField(null=True, blank=True)
            continue
        clone = copy.deepcopy(f)
        clone.db_index = False
        if f.name == "created_at":
            clone.auto_now_add = False
            clone.editable = True
            clone.blank = False
        if f.name == "finished_at" and history:
            clone.null = False
            clone.blank = False
        if f.name == "id" and not history:
            clone.primary_key = True
        fields[f.name] = clone
    return fields


class _InsertOnly:
    """A history row is written once, by the terminal transition, and never updated."""

    def save(self, *args, **kwargs):
        if not self._state.adding and not kwargs.get("force_insert"):
            raise TypeError("job history is insert-only; a finished job cannot be modified")
        super().save(*args, **kwargs)


class _ReadOnlyView:
    def save(self, *args, **kwargs):
        raise TypeError("sqlery_job_all is a read-only view")

    def delete(self, *args, **kwargs):
        raise TypeError("sqlery_job_all is a read-only view")


class _HistoryMeta:
    db_table = "sqlery_job_history"
    ordering = ["-finished_at", "-id"]
    verbose_name = "Finished job"
    verbose_name_plural = "Job history"
    indexes = [
        models.Index(fields=["id"], name="sqlery_hist_id_idx"),
        models.Index(
            fields=["parent_job_id"],
            name="sqlery_hist_parent_idx",
            condition=Q(parent_job_id__isnull=False),
        ),
        models.Index(fields=["status", "-finished_at"], name="sqlery_hist_status_idx"),
        models.Index(
            fields=["job_name"],
            name="sqlery_hist_name_idx",
            condition=Q(job_name__isnull=False),
        ),
        models.Index(fields=["-created_at"], name="sqlery_hist_created_idx"),
    ]


class _JobAllMeta:
    db_table = "sqlery_job_all"
    managed = False
    ordering = ["-priority", "created_at"]
    verbose_name = "Job"
    verbose_name_plural = "Jobs"


# Built in one go (a Django model's fields must exist when its class is created).
JobHistory = type(
    "JobHistory",
    (_InsertOnly, _JobRowMixin, models.Model),
    {
        "__module__": __name__,
        "__doc__": """A job that reached a terminal state (success / failed / archived): insert-only (#140).

    Has every column of ``sqlery_queued_job`` (cloned from ``QueuedJob``); primary key
    ``(finished_at, id)``. On PostgreSQL the table is ``PARTITION BY RANGE (finished_at)``
    (created by migration 0039) and retention drops whole partitions; elsewhere it is a plain
    table and retention deletes in batches. Rows are written once by the terminal
    transition (``INSERT .. SELECT`` + ``DELETE`` from the live table, one transaction, see
    ``sqlery.django_sqlery.history``) and never updated: ``save()`` of an existing row is
    refused. Mirrors the SQLModel ``JobHistory``.
    """,
        "pk": models.CompositePrimaryKey("finished_at", "id"),
        "Meta": _HistoryMeta,
        **_job_row_fields(history=True),
    },
)

JobAll = type(
    "JobAll",
    (_ReadOnlyView, _JobRowMixin, models.Model),
    {
        "__module__": __name__,
        "__doc__": """Read-only row of the ``sqlery_job_all`` view: the live table and history together.

    Reads that must see a job whatever its state (dashboards, lists, counts, statistics,
    dependency checks, retry chains) use this model. Unmanaged: the view is created by
    migration 0039. Writes are refused.
    """,
        "Meta": _JobAllMeta,
        **_job_row_fields(history=False),
    },
)


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
