"""Unified model schemas using Pydantic.

These schemas are the single source of truth for both Django and SQLModel.
"""

from datetime import datetime
# from typing import Optional, Any  # Optional replaced with X | None (Python 3.10+)
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class ScheduledTaskSchema(BaseModel):
    """Schema for scheduled tasks that run on a cron schedule."""

    model_config = ConfigDict(
        json_schema_extra={
            "db_table": "sqlery_scheduled_task",
            "indexes": [
                ("enabled", "next_run_at"),
            ],
        }
    )

    # Primary key
    id: int | None = None

    # Task definition
    name: str = Field(max_length=255, description="Unique name for this task")
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
    created_at: datetime | None = Field(
        default=None,
        description="When task was created",
        json_schema_extra={"django_auto_now_add": True}
    )
    updated_at: datetime | None = Field(
        default=None,
        description="When task was last updated",
        json_schema_extra={"django_auto_now": True}
    )


class QueuedJobSchema(BaseModel):
    """Schema for jobs in the queue, waiting to be executed or already processed."""

    model_config = ConfigDict(
        json_schema_extra={
            "db_table": "sqlery_queued_job",
            "indexes": [
                ("queue_name", "status", "priority", "created_at"),
                ("task_path", "status"),
            ],
        }
    )

    # Primary key
    id: int | None = None

    # Task definition
    task_path: str = Field(max_length=500, description="Python path to callable (e.g., 'myapp.tasks.my_function')")
    kwargs: dict[str, Any] = Field(default_factory=dict, description="Keyword arguments to pass to task function")

    # Queue configuration
    queue_name: str = Field(default="default", max_length=50, description="Queue name for job routing")
    priority: int = Field(default=0, description="Priority (higher = sooner)")

    # Status
    status: str = Field(default="queued", max_length=10, description="Job status (queued/running/success/failed)")

    # Retry configuration
    retry_count: int = Field(default=0, description="Current retry attempt number (0 = first attempt)")
    max_retries: int = Field(default=0, description="Maximum number of retry attempts (0 = no retries)")
    retry_backoff: float = Field(default=1.0, description="Exponential backoff multiplier (seconds between retries)")

    # Concurrency and timeout configuration
    allow_parallel: bool = Field(default=False, description="Allow multiple jobs from same queue to run in parallel")
    timeout_seconds: int | None = Field(default=None, description="Maximum execution time in seconds")
    worker_pid: int | None = Field(default=None, description="Process ID of worker executing this job")

    # Execution history
    runs: list[dict[str, Any]] = Field(default_factory=list, description="History of all execution attempts")

    # Foreign keys
    scheduled_task_id: int | None = None
    worker_id: UUID | None = None

    # Timing
    created_at: datetime | None = Field(
        default=None,
        description="When job was enqueued",
        json_schema_extra={"django_auto_now_add": True}
    )
    scheduled_at: datetime | None = Field(default=None, description="When job should run (NULL = run immediately)")
    started_at: datetime | None = Field(default=None, description="When execution began")
    finished_at: datetime | None = Field(default=None, description="When execution completed")
    duration_seconds: float | None = None

    # Results
    output: str = Field(default="", description="Task return value or stdout")
    error: str = Field(default="", description="Error message if failed")
    traceback: str = Field(default="", description="Full traceback if failed")


class JobRegistrySchema(BaseModel):
    """Schema for tracking job lifecycle in registries (RQ-compatible)."""

    model_config = ConfigDict(
        json_schema_extra={
            "db_table": "sqlery_registry",
            "indexes": [
                ("registry_type", "entered_at"),
                ("job_id", "registry_type"),
                ("registry_type", "exited_at"),
            ],
        }
    )

    # Primary key
    id: int | None = None

    # Foreign key
    job_id: int = Field(description="Job being tracked")

    # Registry type
    registry_type: str = Field(max_length=20, description="Registry type (started/finished/failed/etc)")

    # Timing
    entered_at: datetime | None = Field(
        default=None,
        description="When job entered this registry",
        json_schema_extra={"django_auto_now_add": True}
    )
    exited_at: datetime | None = Field(default=None, description="When job exited this registry (NULL = still active)")

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class WorkerSchema(BaseModel):
    """Schema for worker processes that execute jobs from the queue."""

    model_config = ConfigDict(
        json_schema_extra={
            "db_table": "sqlery_worker",
            "indexes": [
                ("node_id", "status"),
                ("status", "last_heartbeat"),
            ],
        }
    )

    # Primary key (UUID7)
    id: UUID | None = None

    # Worker identification
    node_id: str = Field(max_length=255, description="Hostname or container ID where worker is running")
    pid: int = Field(description="Process ID of worker")

    # Status
    status: str = Field(default="idle", max_length=10, description="Worker status (idle/busy/dead)")
    current_job_id: int | None = None

    # Configuration
    queues: list[str] = Field(default_factory=list, description="List of queue names this worker handles")

    # Heartbeat and lifecycle
    last_heartbeat: datetime | None = Field(
        default=None,
        description="Last time worker sent heartbeat",
        json_schema_extra={"django_auto_now": True}
    )
    started_at: datetime | None = Field(
        default=None,
        description="When worker started",
        json_schema_extra={"django_auto_now_add": True}
    )

    # Statistics
    jobs_processed: int = Field(default=0, description="Total number of jobs processed by this worker")
