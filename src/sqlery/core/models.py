"""SQLModel models for standalone mode (generated from Pydantic schemas).

These models are automatically generated from the unified schemas
to ensure consistency with Django models.
"""

import os
from datetime import datetime, UTC
# from typing import Optional  # Replaced with X | None (Python 3.10+)
# Needed for SQLModel forward-reference string annotations in Relationship() — union syntax ("X | None")
# is not supported by SQLAlchemy's string resolver, so Optional["X"] must be used instead.
from typing import Optional
from uuid import UUID
from uuid6 import uuid7
from sqlmodel import Field, SQLModel, Column, JSON, Relationship
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    text,
)
from sqlalchemy import event as _sa_event
from sqlalchemy.orm import registry as _sa_registry

from sqlery.core import history as _history
from sqlery.core.utils import reject_unawaited_coroutine
from sqlery.core.sqltypes import UTCDateTime
from sqlery.core.worker_admin import is_worker_beating
from sqlery.compat import get_config
from sqlery.tables import JOB_ALL


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
    last_run_at: datetime | None = Field(sa_type=UTCDateTime, default=None, description="Last successful execution time (UTC)")
    next_run_at: datetime | None = Field(sa_type=UTCDateTime, default=None, description="Next scheduled execution time (UTC)")

    # Metadata
    created_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))

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
    model for partition parity. In standalone mode the id is assigned by the
    `before_flush` listener below (a 62-bit UUID-v7-derived integer) on BOTH
    SQLite and PostgreSQL — this guarantees no id collision between QueuedJob
    and ScheduledJob via UUID-v7 global uniqueness. A `sqlery_job_id_seq`
    DEFAULT also exists on the PG column (created by database.py DDL) as a
    fallback, but the listener assigns the id before insert so the sequence is
    not normally exercised in standalone mode. (Django mode draws ids from the
    shared sequence directly — see migration 0029.)
    """

    __tablename__ = "sqlery_queued_job"

    # Composite primary key — id first for SQLAlchemy ordering; created_at
    # second so that partition pruning on created_at works for both backends.
    #
    # id assignment strategy (standalone mode):
    #   The `before_flush` listener below assigns a 62-bit UUID-v7-derived
    #   integer (time-sortable, globally unique) whenever id is None — on BOTH
    #   SQLite and PostgreSQL. PG additionally carries a nextval('sqlery_job_id_seq')
    #   column DEFAULT (from database.py DDL) as a safety fallback, but the
    #   listener pre-empts it. UUID-v7 uniqueness — not the sequence — is what
    #   guarantees QueuedJob/ScheduledJob ids never collide in standalone mode.
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

    # Retry chain linkage. BigInteger (matching the raw DDL in
    # fastapi_sqlery/database.py) because it stores a 64-bit QueuedJob id — a
    # plain Integer column overflows once a failed job spawns a retry.
    parent_job_id: int | None = Field(default=None, sa_column=Column(BigInteger, index=True, nullable=True), description="ID of the failed job this retry was created from (links retry chain)")

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
        sa_type=UTCDateTime,
        default_factory=lambda: datetime.now(UTC),
        primary_key=True,
        description="When job was enqueued (also part of composite PK for partitioning)",
    )
    scheduled_at: datetime | None = Field(sa_type=UTCDateTime, default=None, index=True, description="When job should run (NULL = run immediately)")
    started_at: datetime | None = Field(sa_type=UTCDateTime, default=None, description="When execution began")
    finished_at: datetime | None = Field(sa_type=UTCDateTime, default=None, description="When execution completed")
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
        ]

    def mark_running(self):
        """Mark job as running (the worker PID is recorded on the execution attempt)."""
        # import os  # moved to top-level
        self.status = "running"
        self.started_at = datetime.now(UTC)
        # worker_pid is no longer written: it lives on the attempt (#138).
        # #CLEANUP: drop QueuedJob.worker_pid/child_pid once no reader falls back to them.

    def mark_success(self, output: str = ""):
        """Mark job as successful."""
        # REGRESSION 2026-08-08: guard the shared success-recording choke point,
        # not just individual executor call sites -- see REGRESSIONS.md
        reject_unawaited_coroutine(output)
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
    The id is assigned by the same `before_flush` UUID-v7 listener as
    QueuedJob (see QueuedJob docstring) on both SQLite and PostgreSQL, which
    guarantees ids never collide with QueuedJob. (Django mode draws ScheduledJob
    ids from the shared sqlery_job_id_seq — see migration 0029.)
    """

    __tablename__ = "sqlery_scheduled_job"

    # Composite primary key matching QueuedJob.
    # id follows the same two-tier assignment strategy as QueuedJob.id.
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, nullable=False),
    )
    created_at: datetime = Field(
        sa_type=UTCDateTime,
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
        sa_type=UTCDateTime,
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
    # BigInteger: standalone job ids are 62-bit (UUIDv7-derived), too big for INTEGER on PG.
    job_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("sqlery_queued_job.id"), nullable=False),
        description="Job being tracked",
    )

    # Registry type
    registry_type: str = Field(max_length=20, index=True, description="Registry type (started/finished/failed/etc)")

    # Timing
    entered_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC), description="When job entered this registry")
    exited_at: datetime | None = Field(sa_type=UTCDateTime, default=None, description="When job exited this registry (NULL = still active)")

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
    # BigInteger: standalone job ids are 62-bit (UUIDv7-derived), too big for INTEGER on PG.
    current_job_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("sqlery_queued_job.id"), nullable=True),
    )

    # Configuration
    queues: list = Field(default_factory=list, sa_column=Column(JSON), description="List of queue names this worker handles")

    # Heartbeat and lifecycle
    last_heartbeat: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC), index=True, description="Last time worker sent heartbeat")
    started_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))

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

    def is_alive(self, timeout_seconds: int | None = None) -> bool:
        """Check if worker is alive based on heartbeat (see is_worker_beating)."""
        if timeout_seconds is None:
            timeout_seconds = get_config("WORKER_ALIVE_TIMEOUT", 30)
        return is_worker_beating(
            self.status, self.last_heartbeat, datetime.now(UTC), timeout_seconds
        )


class JobAttempt(SQLModel, table=True):
    """One execution attempt of a job: inserted on claim, closed exactly once on completion.

    ``job_id`` is a plain indexed column, not a foreign key: on partitioned PostgreSQL the
    ``sqlery_queued_job`` primary key is ``(created_at, id)``, so ``id`` alone cannot be
    referenced. A completion closes the attempt with a compare-and-set on ``(id, token)``
    where ``finished_at IS NULL``; zero rows means the completion is stale (see
    ``sqlery.core.attempts``). Mirrors the Django ``JobAttempt`` model.
    """

    __tablename__ = "sqlery_job_attempt"
    __table_args__ = (
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('success', 'failed', 'abandoned')",
            name="sqlery_attempt_outcome_chk",
        ),
        Index("sqlery_attempt_job_idx", "job_id"),
        Index(
            "sqlery_attempt_open_worker_idx",
            "worker_id",
            postgresql_where=text("finished_at IS NULL"),
            sqlite_where=text("finished_at IS NULL"),
        ),
        # "Is this job running / which jobs are running" (#157): the open attempts, by job.
        Index(
            "sqlery_attempt_open_job_idx",
            "job_id",
            postgresql_where=text("finished_at IS NULL"),
            sqlite_where=text("finished_at IS NULL"),
        ),
    )

    # Integer on SQLite so the primary key auto-increments (rowid alias).
    id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger().with_variant(Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
    )
    job_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    token: str = Field(max_length=36)
    worker_id: UUID | None = Field(default=None, description="Claiming worker, if registered")
    worker_pid: int | None = Field(default=None, description="PID of the claiming process")
    child_pid: int | None = Field(default=None, description="PID of the forked child")
    claimed_at: datetime = Field(
        sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC)
    )
    finished_at: datetime | None = Field(sa_type=UTCDateTime, default=None)
    outcome: str | None = Field(default=None, max_length=10)


class JobReady(SQLModel, table=True):
    """A claimable job: one narrow entry per queued job that is neither waiting on
    dependencies nor already claimed (issue #139, see ``sqlery.core.ready``).

    A claim deletes the entry; an enqueue, retry, requeue or unblock inserts it. On
    PostgreSQL the table is ``PARTITION BY LIST (slot)`` (created by the bootstrap /
    migration DDL, not by ``create_all``); on SQLite it is a plain table. Mirrors the Django
    ``JobReady`` model.
    """

    __tablename__ = "sqlery_job_ready"

    slot: int = Field(
        default=0,
        sa_column=Column(
            SmallInteger, primary_key=True, nullable=False, autoincrement=False,
            server_default=text("0"),
        ),
    )
    job_id: int = Field(
        sa_column=Column(BigInteger, primary_key=True, nullable=False, autoincrement=False)
    )
    queue_name: str = Field(max_length=50)
    priority: int = Field(default=0)
    created_at: datetime = Field(sa_type=UTCDateTime)
    scheduled_at: datetime | None = Field(sa_type=UTCDateTime, default=None)


Index(
    "sqlery_ready_claim_idx",
    JobReady.__table__.c.queue_name,
    JobReady.__table__.c.priority.desc(),
    JobReady.__table__.c.created_at,
    JobReady.__table__.c.job_id,
)


class JobBlocked(SQLModel, table=True):
    """One row per dependency of a queued job whose dependencies are unmet (issue #139).

    The job has no ready entry while any row exists here; when a dependency reaches a
    terminal state the dependents are looked up by ``dependency_id`` and re-checked.
    ``created_at`` is the job's partition key, so the job is read by primary key.
    """

    __tablename__ = "sqlery_job_blocked"

    job_id: int = Field(
        sa_column=Column(BigInteger, primary_key=True, nullable=False, autoincrement=False)
    )
    dependency_id: int = Field(
        sa_column=Column(
            BigInteger, primary_key=True, nullable=False, autoincrement=False, index=False
        )
    )
    created_at: datetime = Field(sa_type=UTCDateTime)
    blocked_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))


Index("sqlery_blocked_dependency_idx", JobBlocked.__table__.c.dependency_id)


class JobHistory(SQLModel, table=True):
    """A job that reached a terminal state (success / failed / archived): insert-only (#140).

    Has every column of ``sqlery_queued_job`` (see ``sqlery.core.history``); primary key
    ``(finished_at, id)``. On PostgreSQL it is ``PARTITION BY RANGE (finished_at)`` (created by
    the bootstrap / migration DDL, not by ``create_all``) and retention drops whole
    partitions; elsewhere it is a plain table. A row is written once, by the terminal
    transition (``INSERT .. SELECT`` + ``DELETE`` from the live table, one transaction) and
    never updated; only retention removes it. ``scheduled_task_id`` / ``worker_id`` are plain
    columns here (the referenced rows may be gone long before the history is). Mirrors the
    Django ``JobHistory`` model.
    """

    __tablename__ = "sqlery_job_history"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, nullable=False, autoincrement=False),
    )
    finished_at: datetime = Field(
        sa_type=UTCDateTime, primary_key=True, description="When the job reached its final state"
    )
    task_path: str = Field(max_length=500)
    kwargs: dict = Field(default_factory=dict, sa_column=Column(JSON))
    queue_name: str = Field(default="default", max_length=50)
    priority: int = Field(default=0)
    status: str = Field(default="success", max_length=20)
    parent_job_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=0)
    retry_backoff: float = Field(default=1.0)
    allow_parallel: bool = Field(default=False)
    timeout_seconds: int | None = Field(default=None)
    worker_pid: int | None = Field(default=None)
    child_pid: int | None = Field(default=None)
    job_name: str | None = Field(default=None, max_length=255)
    tags: list = Field(default_factory=list, sa_column=Column("tags", JSON))
    retry_intervals: list | None = Field(
        default=None, sa_column=Column("retry_intervals", JSON)
    )
    meta: dict | None = Field(default=None, sa_column=Column("meta", JSON))
    dependencies: list = Field(default_factory=list, sa_column=Column("dependencies", JSON))
    on_success_path: str = Field(default="", max_length=500)
    on_failure_path: str = Field(default="", max_length=500)
    ttl: int | None = Field(default=None)
    result_ttl: int | None = Field(default=None)
    failure_ttl: int | None = Field(default=None)
    version: int = Field(default=0)
    runs: list = Field(default_factory=list, sa_column=Column(JSON))
    scheduled_task_id: int | None = Field(default=None)
    worker_id: UUID | None = Field(default=None)
    created_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))
    scheduled_at: datetime | None = Field(sa_type=UTCDateTime, default=None)
    started_at: datetime | None = Field(sa_type=UTCDateTime, default=None)
    duration_seconds: float | None = Field(default=None)
    output: str = Field(default="")
    error: str = Field(default="")
    traceback: str = Field(default="")
    termination_reason: str = Field(default="", max_length=100)

    # Read helpers shared with QueuedJob (history is never executed, so nothing else).
    def should_retry(self) -> bool:
        return False


# Indexes of the history table (shared DDL: ``sqlery.core.history.HISTORY_INDEXES``).
for _name, _cols, _where in _history.HISTORY_INDEXES:
    _expressions = []
    for _part in _cols.split(","):
        _col_name, *_order = _part.split()
        _column = JobHistory.__table__.c[_col_name]
        _expressions.append(_column.desc() if _order and _order[0].upper() == "DESC" else _column)
    _kwargs = {}
    if _where:
        _kwargs = {"postgresql_where": text(_where), "sqlite_where": text(_where)}
    Index(_name, *_expressions, **_kwargs)


class JobAll:
    """Read-only row of the ``sqlery_job_all`` view: the live table and history together.

    Mapped imperatively onto the view (a separate registry, so ``create_all`` never tries to
    create it). Attribute names are those of ``QueuedJob`` / ``JobHistory``.
    """

    #: Marks the row as read from the union view (not a live, writable job).
    is_read_only_view = True

    def should_retry(self) -> bool:
        return False


def _build_job_all_table() -> Table:
    columns = [
        Column(c.name, c.type, primary_key=(c.name == "id"), autoincrement=False)
        for c in QueuedJob.__table__.columns
    ]
    return Table(JOB_ALL, MetaData(), *columns)


JOB_ALL_TABLE = _build_job_all_table()
_job_all_registry = _sa_registry()
_job_all_registry.map_imperatively(JobAll, JOB_ALL_TABLE)

#: Columns shared by the live table, history and the view (standalone model).
JOB_COLUMNS = [c.name for c in QueuedJob.__table__.columns]


def _job_view_exists(connection) -> bool:
    from sqlalchemy import inspect as _inspect

    return JOB_ALL in _inspect(connection).get_view_names()


@_sa_event.listens_for(SQLModel.metadata, "after_create")
def _create_job_view(target, connection, **kw):
    """``SQLModel.metadata.create_all`` also creates the union view over the live table and
    history (it is not a table, so ``create_all`` alone would miss it). Skipped while either
    table is missing (a partial ``create_all``)."""
    from sqlalchemy import inspect as _inspect

    if connection.dialect.name not in ("sqlite", "postgresql"):
        return
    inspector = _inspect(connection)
    if not (
        inspector.has_table(QueuedJob.__tablename__)
        and inspector.has_table(JobHistory.__tablename__)
        and inspector.has_table(JobAttempt.__tablename__)
    ):
        return
    if _job_view_exists(connection):
        return
    for statement in _history.view_columns_ddl(JOB_COLUMNS, connection.dialect.name, with_attempts=True):
        connection.execute(text(statement))


@_sa_event.listens_for(SQLModel.metadata, "before_drop")
def _drop_job_view(target, connection, **kw):
    """The view depends on both tables: it goes before ``drop_all`` drops them."""
    if connection.dialect.name in ("sqlite", "postgresql"):
        connection.execute(text(_history.drop_view_ddl()))


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
    acquired_at: datetime = Field(sa_column=Column(UTCDateTime()))
    expires_at: datetime = Field(sa_column=Column(UTCDateTime(), index=True))
    # Optimistic locking (SQLite CAS) — mirrors QueuedJob.version (models.py:113-114)
    version: int = Field(default=0, description="Version counter for optimistic locking (SQLite CAS)")


# ---------------------------------------------------------------------------
# Composite-PK id generation (event listener) — standalone mode
# ---------------------------------------------------------------------------
# Composite primary keys cannot use SQLite AUTOINCREMENT. When id is None
# before a flush we assign a 62-bit time-sortable integer derived from a UUID
# v7. This fires on BOTH SQLite and PostgreSQL in standalone mode — so even
# though PG carries a nextval('sqlery_job_id_seq') column DEFAULT (created by
# database.py DDL), the listener assigns the id first and the sequence default
# is not normally exercised. UUID-v7 global uniqueness is what guarantees
# QueuedJob and ScheduledJob ids never collide in standalone mode. (Django mode
# uses the shared sequence directly via migration 0029; it has no listener.)
#
# The listener is registered on the SQLAlchemy Session class (not per-session)
# so it applies to all sessions created from any engine.

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


# The ready-queue session hook (keeps sqlery_job_ready in step with ORM writes of QueuedJob)
# must be registered after the id-assignment hook above.
from . import ready_sql as _ready_sql  # noqa: E402,F401
