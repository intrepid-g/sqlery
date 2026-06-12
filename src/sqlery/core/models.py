"""SQLModel models for standalone mode (generated from Pydantic schemas).

These models are automatically generated from the unified schemas
to ensure consistency with Django models.
"""

import os
from datetime import datetime, timedelta, UTC
# from typing import Optional  # Replaced with X | None (Python 3.10+)
# Needed for SQLModel forward-reference string annotations in Relationship() — union syntax ("X | None")
# is not supported by SQLAlchemy's string resolver, so Optional["X"] must be used instead.
from typing import Optional
from uuid import UUID
from uuid6 import uuid7
from sqlmodel import Field, SQLModel, Column, JSON, Relationship
from sqlalchemy import BigInteger, Index, DateTime


class ScheduledTask(SQLModel, table=True):
    """A scheduled task that runs on a cron schedule."""

    __tablename__ = "sqlery_scheduled_task"

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Task definition
    name: str = Field(max_length=255, unique=True, description="Unique name for this task")
    task_path: str = Field(max_length=500, description="Python path to callable (e.g., 'myapp.tasks.my_function')")
    cron_expression: str = Field(max_length=100, description="Cron expression (e.g., '0 2 * * *' for 2 AM daily)")

    # Queue configuration
    queue_name: str = Field(default="default", max_length=50, description="Queue name for job routing")
    priority: int = Field(default=0, description="Priority for enqueued jobs (higher = sooner)")

    # Status
    enabled: bool = Field(default=True, description="Whether this task should run")

    # Execution tracking
    last_run_at: datetime | None = Field(default=None, description="Last successful execution time (UTC)")
    next_run_at: datetime | None = Field(default=None, description="Next scheduled execution time (UTC)")

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    jobs: list["QueuedJob"] = Relationship(back_populates="scheduled_task")

    class Config:
        """SQLModel configuration."""
        table = True
        indexes = [
            ("enabled", "next_run_at"),
        ]


class QueuedJob(SQLModel, table=True):
    """A job in the queue, waiting to be executed or already processed.

    Schema note: composite primary key (created_at, id) mirrors the Django
    model for partition parity. On PostgreSQL the id column draws from the
    shared sqlery_job_id_seq sequence (wired in the Alembic migration in
    plan 17-02). On SQLite the integer column draws from the implicit rowid.
    """

    __tablename__ = "sqlery_queued_job"

    # Composite primary key — id first for SQLAlchemy ordering; created_at
    # second so that partition pruning on created_at works for both backends.
    #
    # id assignment strategy (two-tier):
    #   - SQLite (dev/test): Python-side default generates a 62-bit integer
    #     from the lower bits of a UUID v7 (time-sortable, globally unique).
    #   - PostgreSQL (production): the Alembic migration in plan 17-02 replaces
    #     the column default with nextval('sqlery_job_id_seq') so the shared
    #     sequence assigns ids instead of this Python default.
    #
    # Note: autoincrement=True is intentionally absent — SQLite does not
    # support AUTOINCREMENT for composite primary keys and raises CompileError.
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, nullable=False),
    )
    # created_at is also part of the PK so partitioned tables can route rows
    # by time range. default_factory ensures a value is always set on insert.

    # Task definition
    task_path: str = Field(max_length=500, description="Python path to callable (e.g., 'myapp.tasks.my_function')")
    kwargs: dict = Field(default_factory=dict, sa_column=Column(JSON), description="Keyword arguments to pass to task function")

    # Queue configuration
    queue_name: str = Field(default="default", max_length=50, index=True, description="Queue name for job routing")
    priority: int = Field(default=0, index=True, description="Priority (higher = sooner)")

    # Status
    status: str = Field(default="queued", max_length=20, index=True, description="Job status (queued/running/success/failed/archived/shutting_down)")

    # Retry chain linkage
    parent_job_id: int | None = Field(default=None, index=True, description="ID of the failed job this retry was created from (links retry chain)")

    # Retry configuration
    retry_count: int = Field(default=0, description="Current retry attempt number (0 = first attempt)")
    max_retries: int = Field(default=0, description="Maximum number of retry attempts (0 = no retries)")
    retry_backoff: float = Field(default=1.0, description="Exponential backoff multiplier (seconds between retries)")

    # Concurrency and timeout configuration
    allow_parallel: bool = Field(default=False, description="Allow multiple jobs from same queue to run in parallel")
    timeout_seconds: int | None = Field(default=None, description="Maximum execution time in seconds")
    worker_pid: int | None = Field(default=None, description="Process ID of worker executing this job")
    child_pid: int | None = Field(default=None, description="PID of forked child process executing this job")

    # Named jobs and tags
    job_name: str | None = Field(default=None, max_length=255, index=True, description="Unique job name for dedup (new job with same name replaces old)")
    tags: list = Field(default_factory=list, sa_column=Column("tags", JSON), description="Tags for rate limiting and concurrency control")

    # Extended retry configuration
    retry_intervals: list | None = Field(default=None, sa_column=Column("retry_intervals", JSON), description="Custom retry intervals in seconds")

    # Job metadata
    meta: dict | None = Field(default=None, sa_column=Column("meta", JSON), description="Arbitrary metadata")

    # Dependencies
    dependencies: list = Field(default_factory=list, sa_column=Column("dependencies", JSON), description="List of job IDs this job depends on")

    # Callbacks
    on_success_path: str = Field(default="", max_length=500, description="Python path to success callback")
    on_failure_path: str = Field(default="", max_length=500, description="Python path to failure callback")

    # TTL (time-to-live)
    ttl: int | None = Field(default=None, description="Max seconds job can stay queued before expiring")
    result_ttl: int | None = Field(default=None, description="Seconds to keep successful job result")
    failure_ttl: int | None = Field(default=None, description="Seconds to keep failed job result (-1 = forever)")

    # Optimistic locking
    version: int = Field(default=0, description="Version counter for optimistic locking (SQLite CAS)")

    # Execution history
    runs: list = Field(default_factory=list, sa_column=Column(JSON), description="History of all execution attempts")

    # Foreign keys
    scheduled_task_id: int | None = Field(default=None, foreign_key="sqlery_scheduled_task.id")
    worker_id: UUID | None = Field(default=None, foreign_key="sqlery_worker.id")

    # Timing — created_at is part of the composite PK (created_at, id)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        primary_key=True,
        description="When job was enqueued (also part of composite PK for partitioning)",
    )
    scheduled_at: datetime | None = Field(default=None, index=True, description="When job should run (NULL = run immediately)")
    started_at: datetime | None = Field(default=None, description="When execution began")
    finished_at: datetime | None = Field(default=None, description="When execution completed")
    duration_seconds: float | None = Field(default=None)

    # Results
    output: str = Field(default="", description="Task return value or stdout")
    error: str = Field(default="", description="Error message if failed")
    traceback: str = Field(default="", description="Full traceback if failed")
    termination_reason: str = Field(default="", max_length=100, description="Reason for job termination (signal, timeout, user action, etc.)")

    # Relationships
    scheduled_task: ScheduledTask | None = Relationship(back_populates="jobs")
    # "Worker | None" string is not resolvable by SQLAlchemy's class registry; use Optional["Worker"]
    # foreign_keys required because QueuedJob<->Worker has two FK paths (worker_id and current_job_id)
    worker: Optional["Worker"] = Relationship(
        back_populates="assigned_jobs",
        sa_relationship_kwargs={"foreign_keys": "[QueuedJob.worker_id]"},
    )
    registry_entries: list["JobRegistry"] = Relationship(back_populates="job", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

    class Config:
        """SQLModel configuration."""
        table = True
        indexes = [
            ("queue_name", "status", "priority", "created_at"),
            ("task_path", "status"),
        ]

    def mark_running(self):
        """Mark job as running and record worker PID."""
        # import os  # moved to top-level
        self.status = "running"
        self.started_at = datetime.now(UTC)
        self.worker_pid = os.getpid()

    def mark_success(self, output: str = ""):
        """Mark job as successful."""
        self.status = "success"
        self.finished_at = datetime.now(UTC)
        if self.started_at:
            # started_at may be naive (TIMESTAMP WITHOUT TIME ZONE from Postgres); normalise to UTC
            started = self.started_at if self.started_at.tzinfo else self.started_at.replace(tzinfo=UTC)
            self.duration_seconds = (self.finished_at - started).total_seconds()
        self.output = str(output)

        # Record this run in history
        self._record_run(status="success", output=str(output))

    def mark_failed(self, error: str, traceback: str = "", termination_reason: str = ""):
        """Mark job as failed with optional termination reason.

        Args:
            error: Error message
            traceback: Full traceback if available
            termination_reason: Human-readable reason for termination
                              (e.g., "timeout", "killed_by_user", "sigterm", "sigkill", "cancelled")
        """
        self.status = "failed"
        self.finished_at = datetime.now(UTC)
        if self.started_at:
            # started_at may be naive (TIMESTAMP WITHOUT TIME ZONE from Postgres); normalise to UTC
            started = self.started_at if self.started_at.tzinfo else self.started_at.replace(tzinfo=UTC)
            self.duration_seconds = (self.finished_at - started).total_seconds()
        self.error = str(error)
        self.traceback = traceback
        self.termination_reason = termination_reason

        # Record this run in history
        self._record_run(status="failed", error=str(error))

    def _record_run(self, status: str, output: str = "", error: str = ""):
        """Record execution attempt in runs history."""
        run_record = {
            "attempt_number": self.retry_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": status,
            "duration": self.duration_seconds,
            "output": output[:1000] if output else "",
            "error": error[:1000] if error else "",
        }

        if not isinstance(self.runs, list):
            self.runs = []

        self.runs.append(run_record)

    def should_retry(self) -> bool:
        """Check if job should be retried after failure."""
        return (
            self.status == "failed"
            and self.max_retries > 0
            and self.retry_count < self.max_retries
        )

    def calculate_retry_delay(self) -> float:
        """Calculate delay before next retry using exponential backoff."""
        return self.retry_backoff * (2 ** self.retry_count)


class ScheduledJob(SQLModel, table=True):
    """Staging table for jobs that are scheduled to run at a future time.

    Mirrors Django's ScheduledJob model shape for drop-in compatibility.
    Rows are promoted to QueuedJob when their scheduled_at time arrives.
    The id uses the shared sqlery_job_id_seq sequence on PostgreSQL (wired
    in the Alembic migration in plan 17-02). On SQLite it is rowid-backed.
    """

    __tablename__ = "sqlery_scheduled_job"

    # Composite primary key matching QueuedJob.
    # id follows the same two-tier assignment strategy as QueuedJob.id.
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        primary_key=True,
        description="When the scheduled job was created (also part of composite PK)",
    )

    # Queue configuration
    queue_name: str = Field(
        default="default",
        max_length=50,
        index=True,
        description="Queue name for job routing",
    )

    # Task definition
    task_path: str = Field(
        max_length=500,
        description="Python path to callable (e.g., 'myapp.tasks.my_function')",
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="JSON payload (keyword arguments) to pass to the task function",
    )

    # Scheduling
    scheduled_at: datetime = Field(
        description="When the job should be promoted to QueuedJob and executed",
    )

    # Job configuration
    priority: int = Field(
        default=0,
        description="Priority for the resulting QueuedJob (higher = sooner)",
    )
    max_retries: int = Field(
        default=0,
        description="Maximum number of retry attempts (0 = no retries)",
    )

    class Config:
        """SQLModel configuration."""

        table = True


class JobRegistry(SQLModel, table=True):
    """Track job lifecycle in registries (RQ-compatible)."""

    __tablename__ = "sqlery_registry"

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Foreign key — references sqlery_queued_job.id (not composite FK; id is sufficient)
    job_id: int = Field(foreign_key="sqlery_queued_job.id", description="Job being tracked")

    # Registry type
    registry_type: str = Field(max_length=20, index=True, description="Registry type (started/finished/failed/etc)")

    # Timing
    entered_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When job entered this registry")
    exited_at: datetime | None = Field(default=None, description="When job exited this registry (NULL = still active)")

    # Metadata — renamed from 'metadata' which is reserved by SQLAlchemy's Declarative API
    extra_data: dict = Field(default_factory=dict, sa_column=Column("metadata", JSON), description="Additional metadata")

    # Relationships
    job: QueuedJob = Relationship(back_populates="registry_entries")

    class Config:
        """SQLModel configuration."""
        table = True
        indexes = [
            ("registry_type", "entered_at"),
            ("job_id", "registry_type"),
            ("registry_type", "exited_at"),
        ]


class Worker(SQLModel, table=True):
    """A worker process that executes jobs from the queue."""

    __tablename__ = "sqlery_worker"

    # Primary key (UUID7 for time-sortable UUIDs)
    id: UUID = Field(default_factory=uuid7, primary_key=True)

    # Worker identification
    node_id: str = Field(max_length=255, index=True, description="Hostname or container ID where worker is running")
    pid: int = Field(description="Process ID of worker")

    # Status
    status: str = Field(default="idle", max_length=10, index=True, description="Worker status (idle/busy/dead)")
    current_job_id: int | None = Field(default=None, foreign_key="sqlery_queued_job.id")

    # Configuration
    queues: list = Field(default_factory=list, sa_column=Column(JSON), description="List of queue names this worker handles")

    # Heartbeat and lifecycle
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True, description="Last time worker sent heartbeat")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Statistics
    jobs_processed: int = Field(default=0, description="Total number of jobs processed by this worker")

    # Relationships
    # foreign_keys required because QueuedJob<->Worker has two FK paths (worker_id and current_job_id)
    assigned_jobs: list[QueuedJob] = Relationship(
        back_populates="worker",
        sa_relationship_kwargs={"foreign_keys": "[QueuedJob.worker_id]"},
    )

    class Config:
        """SQLModel configuration."""
        table = True
        indexes = [
            ("node_id", "status"),
            ("status", "last_heartbeat"),
        ]

    def is_alive(self, timeout_seconds: int = 30) -> bool:
        """Check if worker is alive based on heartbeat."""
        if self.status == "dead":
            return False
        # from datetime import timedelta  # moved to top-level
        threshold = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        return self.last_heartbeat >= threshold


class DaemonLease(SQLModel, table=True):
    """DB-backed lease for queue-scoped scheduler/daemon ownership (standalone).

    Schema divergence note (WR-05): this standalone table intentionally carries
    an extra ``version`` column that the Django ``DaemonLease`` model
    (``django_sqlery/models.py``) does NOT have. The column backs the SQLite
    optimistic-CAS take-over path; Django relies on ``SELECT FOR UPDATE SKIP
    LOCKED`` and never needs it. Both stacks share the same ``db_table``
    (``sqlery_daemon_lease``); a deployment must not run migrations from both
    stacks against the same database, since the column sets differ by design.
    """

    __tablename__ = "sqlery_daemon_lease"

    queue_name: str = Field(max_length=255, primary_key=True)
    daemon_id: str = Field(max_length=255, description="daemon_{node_id}_{pid}")
    node_id: str = Field(max_length=255)
    pid: int
    # Old (WR-04): naive columns forced re-normalization at every read site.
    # acquired_at: datetime
    # expires_at: datetime = Field(index=True)
    # New (WR-04): timezone-aware columns so Postgres stores timestamptz and
    # reads come back aware (SQLite still returns naive; comparison sites keep
    # the UTC-normalization helper for that case).
    acquired_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), index=True))
    # Optimistic locking (SQLite CAS) — mirrors QueuedJob.version (models.py:113-114)
    version: int = Field(default=0, description="Version counter for optimistic locking (SQLite CAS)")


# ---------------------------------------------------------------------------
# SQLite composite-PK id generation (event listener)
# ---------------------------------------------------------------------------
# SQLite does not support autoincrement for composite primary keys.  When id
# is None before a flush we assign a 62-bit time-sortable integer derived from
# a UUID v7.  On PostgreSQL, the Alembic migration in plan 17-02 replaces the
# column default with nextval('sqlery_job_id_seq'), so this code path is never
# reached there (PG inserts will always carry a server_default).
#
# The listener is registered on the SQLAlchemy Session class (not per-session)
# so it applies to all sessions created from any engine.

from sqlalchemy import event as _sa_event
from sqlalchemy.orm import Session as _SASession


def _generate_job_id_from_uuid7() -> int:
    """Generate a 62-bit time-sortable integer from a UUID v7.

    UUID v7 is a 128-bit time-ordered value.  We take the lower 62 bits
    to stay within signed BigInteger range on all databases.
    """
    return uuid7().int & ((1 << 62) - 1)


@_sa_event.listens_for(_SASession, "before_flush")
def _assign_composite_pk_ids(session, flush_context, instances):
    """Auto-assign id for QueuedJob/ScheduledJob rows that have id=None.

    This hook fires before every flush.  It only acts on new objects
    (session.new) whose id attribute is None, so it is safe to call
    multiple times and does not overwrite ids supplied by the caller or
    by a PostgreSQL sequence server_default.
    """
    for obj in session.new:
        if isinstance(obj, (QueuedJob, ScheduledJob)) and obj.id is None:
            obj.id = _generate_job_id_from_uuid7()
