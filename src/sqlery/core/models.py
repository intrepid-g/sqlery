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
from sqlalchemy import Index


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
    """A job in the queue, waiting to be executed or already processed."""

    __tablename__ = "sqlery_queued_job"

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

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

    # Timing
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When job was enqueued")
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


class JobRegistry(SQLModel, table=True):
    """Track job lifecycle in registries (RQ-compatible)."""

    __tablename__ = "sqlery_registry"

    # Primary key
    id: int | None = Field(default=None, primary_key=True)

    # Foreign key
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
