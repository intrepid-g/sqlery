"""Compatibility layer for Django and standalone modes.

Auto-detects the running mode and provides unified interfaces for:
- Database backend (Django ORM vs SQLAlchemy)
- Configuration (Django settings vs standalone config)
"""

import sys
from abc import ABC, abstractmethod
from typing import Any
from datetime import datetime

try:
    from django.conf import settings as _django_settings
except ImportError:
    _django_settings = None


# Global backend instance (initialized once)
_backend = None
_config = None


class DatabaseBackend(ABC):
    """Abstract database backend interface.

    Implementations provide database operations for either Django ORM or SQLAlchemy.
    """

    @abstractmethod
    def create_job(
        self,
        task_path: str,
        kwargs: dict,
        queue_name: str,
        priority: int,
        scheduled_at: datetime | None,
        max_retries: int,
        retry_backoff: float,
        allow_parallel: bool,
        timeout_seconds: int | None,
        # # Original ABC had only 9 params + parent_job_id.
        # # DjangoBackend proved these additional params necessary in production.
        retry_count: int | None = None,
        scheduled_task_id: int | None = None,
        job_name: str | None = None,
        retry_intervals: list | None = None,
        meta: dict | None = None,
        dependencies: list | None = None,
        on_success_path: str = "",
        on_failure_path: str = "",
        ttl: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        parent_job_id: int | None = None,
    ):
        """Create a new job in the database.

        Returns:
            Job instance (Django QueuedJob or SQLAlchemy Job)
        """
        pass

    @abstractmethod
    def claim_job(self, queues: list[str], worker_id: str):
        """Atomically claim next available job from specified queues.

        Uses SELECT FOR UPDATE SKIP LOCKED.

        Returns:
            Job instance if found, None otherwise
        """
        pass

    def is_worker_paused(self, worker_id: str) -> bool:
        """Check if worker is currently paused (paused_until is in the future)."""
        return False

    def update_job_child_pid(self, job_id: int, child_pid: int):
        """Store the forked child PID on the job row.

        Used by WorkerProcess to record which child process is running a job,
        enabling admin "Stop Job" to target the correct process.

        Args:
            job_id: Job ID
            child_pid: PID of the forked child process
        """
        pass

    def delete_worker_registration(self, worker_id: str) -> int:
        """Delete stale Worker row from a previous crash.

        Called once at worker startup so subsequent heartbeat creates a fresh row.

        Args:
            worker_id: Worker identifier string

        Returns:
            Number of rows deleted (0 or 1)
        """
        return 0

    def release_claimed_job(
        self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs
    ):
        """Release a job after processing and update worker state.

        Args:
            job: Job instance
            worker_id: Worker identifier string
            status: Final job status ('success' or 'failed')
            jobs_processed: Total jobs processed by this worker
            **kwargs: Additional fields to update on the job
        """
        pass

    def claim_queue_leases(
        self,
        queues: list[str],
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
    ) -> list[str]:
        """Claim scheduler leases for the given queues.

        Returns the subset of queues successfully claimed. Expired leases are
        taken over atomically; live leases held by other daemons are skipped.

        Args:
            queues: List of queue names to claim
            daemon_id: Unique daemon identifier
            node_id: Node/host identifier
            pid: Daemon process ID
            lease_secs: Lease duration in seconds

        Returns:
            List of queue names successfully claimed
        """
        return list(queues)

    def renew_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
        lease_secs: int,
    ) -> None:
        """Extend expires_at for all owned leases by lease_secs from now.

        Args:
            owned_queues: List of owned queue names
            daemon_id: Daemon identifier that owns the leases
            lease_secs: New lease duration from now
        """
        pass

    def release_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
    ) -> None:
        """Delete lease rows for all owned queues on clean shutdown.

        Args:
            owned_queues: List of owned queue names
            daemon_id: Daemon identifier that owns the leases
        """
        pass

    # ===== Claiming query primitives (used by core claiming algorithm) =====

    def count_running_with_tag(self, tag: str) -> int:
        """Count currently running jobs that have the given tag.

        Args:
            tag: Tag string to check

        Returns:
            Number of running jobs with this tag
        """
        return 0

    def count_started_with_tag_since(self, tag: str, threshold: datetime) -> int:
        """Count jobs with the given tag that started since threshold.

        Includes running, success, and failed jobs (all states that sent requests).

        Args:
            tag: Tag string to check
            threshold: Only count jobs started after this time

        Returns:
            Number of jobs started with this tag since threshold
        """
        return 0

    def get_expired_ttl_jobs(self) -> list:
        """Get queued jobs whose TTL has expired.

        Returns:
            List of job instances whose created_at + ttl < now
        """
        return []

    def acquire_tag_locks(self, tags: list[str]) -> None:
        """Acquire exclusive locks on tag coordination rows.

        Used to serialize workers checking concurrency/rate limits for the same tags.
        Must be called within a transaction.

        Args:
            tags: Sorted list of tag strings to lock
        """
        pass

    def get_claimable_jobs(
        self,
        queues: list[str],
        priority_weights: dict[str, int] | None = None,
        limit: int = 1,
    ) -> list:
        """Get next claimable jobs ordered by queue priority, job priority, age.

        Uses database-appropriate locking (SELECT FOR UPDATE SKIP LOCKED on Postgres).

        Args:
            queues: List of queue names
            priority_weights: Optional {queue_name: weight} for ordering
            limit: Max jobs to return

        Returns:
            List of claimable job instances
        """
        return []

    def atomic_claim_job(self, job, worker) -> bool:
        """Atomically claim a specific job for a worker.

        Handles both Postgres (already locked by SELECT FOR UPDATE) and
        SQLite (optimistic locking with version field).

        Args:
            job: Job instance to claim
            worker: Worker instance claiming the job

        Returns:
            True if successfully claimed, False if lost race
        """
        return False

    def claim_due_scheduled_task(self, task_id: int):
        """Atomically claim a scheduled task for processing.

        Uses SELECT FOR UPDATE SKIP LOCKED to prevent duplicate enqueueing.

        Args:
            task_id: Scheduled task ID

        Returns:
            ScheduledTask instance if claimed, None if already claimed
        """
        return None

    @abstractmethod
    def get_queue_stats(self, queue_name: str | None = None) -> dict:
        """Get queue statistics (counts by status)."""
        pass

    @abstractmethod
    def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued job.

        Returns:
            True if cancelled, False if not found or already running
        """
        pass

    @abstractmethod
    def retry_failed_jobs(self, queue_name: str | None = None, max_jobs: int | None = None) -> int:
        """Retry failed jobs by resetting them to queued status.

        Returns:
            Number of jobs retried
        """
        pass

    @abstractmethod
    def get_due_scheduled_tasks(self):
        """Get scheduled tasks that are due to run.

        Returns:
            List of scheduled task instances
        """
        pass

    @abstractmethod
    def create_scheduled_task(
        self,
        name: str,
        task_path: str,
        cron_expression: str,
        queue_name: str,
        priority: int,
        enabled: bool = True,
    ):
        """Create a new scheduled task.

        Returns:
            ScheduledTask instance
        """
        pass

    @abstractmethod
    def get_worker_heartbeats(self, active_only: bool = True):
        """Get worker heartbeats.

        Args:
            active_only: Only return workers active in last 60 seconds

        Returns:
            List of worker heartbeat records
        """
        pass

    @abstractmethod
    def update_worker_heartbeat(
        self, worker_id: str, status: str, current_job_id: int | None = None
    ):
        """Update or create worker heartbeat.

        Args:
            worker_id: Unique worker identifier
            status: Worker status (idle, busy, stopping)
            current_job_id: ID of job currently being processed
        """
        pass

    def refresh_worker_heartbeat(self, worker_id):
        """Update only last_heartbeat for a worker, without touching status or current_job."""
        pass

    @abstractmethod
    def cleanup_jobs(
        self,
        status: str | None = None,
        max_age_days: int | None = None,
        max_count: int | None = None,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up old jobs based on retention policy.

        Args:
            status: Filter by job status
            max_age_days: Delete jobs older than this many days
            max_count: Maximum number of jobs to delete
            queue_name: Filter by queue name
            dry_run: If True, return count without deleting

        Returns:
            Dict with cleanup statistics
        """
        pass

    @abstractmethod
    def cleanup_jobs_by_count(
        self,
        status: str | None = None,
        keep_count: int = 1000,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up jobs by keeping only the most recent N jobs.

        Args:
            status: Filter by job status
            keep_count: Number of recent jobs to keep
            queue_name: Filter by queue name
            dry_run: If True, return count without deleting

        Returns:
            Dict with cleanup statistics
        """
        pass

    @abstractmethod
    def get_database_stats(self) -> dict:
        """Get database statistics (job counts, registry counts, etc.).

        Returns:
            Dict with database statistics
        """
        pass

    @abstractmethod
    def vacuum_database(self) -> dict:
        """Run database vacuum/optimize (PostgreSQL VACUUM).

        Returns:
            Dict with vacuum results
        """
        pass

    @abstractmethod
    def add_job_to_registry(
        self,
        job_id: int,
        registry_type: str,
        metadata: dict | None = None,
    ):
        """Add job to a registry for lifecycle tracking.

        Args:
            job_id: Job ID
            registry_type: Registry type (started/finished/failed/etc)
            metadata: Additional metadata
        """
        pass

    @abstractmethod
    def remove_job_from_registry(self, job_id: int, registry_type: str):
        """Remove job from a registry.

        Args:
            job_id: Job ID
            registry_type: Registry type
        """
        pass

    @abstractmethod
    def get_registry_jobs(
        self,
        registry_type: str,
        queue_name: str | None = None,
        limit: int | None = None,
    ) -> list:
        """Get jobs in a specific registry.

        Args:
            registry_type: Registry type (started/finished/failed/etc)
            queue_name: Optional queue name filter
            limit: Maximum number of jobs to return

        Returns:
            List of job instances
        """
        pass

    @abstractmethod
    def cleanup_registry(
        self,
        registry_type: str | None = None,
        max_age_days: int | None = None,
    ) -> dict:
        """Clean up old registry entries.

        Returns:
            Dict with cleanup statistics
        """
        pass

    @abstractmethod
    def get_job_by_id(self, job_id: int):
        """Get job by ID.

        Returns:
            Job instance or None if not found
        """
        pass

    @abstractmethod
    def mark_job_success(self, job_id: int, output: str = ""):
        """Mark job as successful.

        Args:
            job_id: Job ID
            output: Job output/result

        Returns:
            Updated job instance
        """
        pass

    @abstractmethod
    def mark_job_failed(self, job_id: int, error: str, traceback: str = ""):
        """Mark job as failed.

        Args:
            job_id: Job ID
            error: Error message
            traceback: Full traceback

        Returns:
            Updated job instance
        """
        pass

    @abstractmethod
    def mark_job_archived(self, job_id: int):
        """Mark a failed job as archived (a retry has been created for it).

        Args:
            job_id: Job ID
        """
        pass

    @abstractmethod
    def cascade_ancestor_status(self, job_id: int, status: str):
        """Walk parent_job_id chain and set all ancestors to the given status.

        Args:
            job_id: Starting job ID (walks upward from this job's parent)
            status: Status to set on ancestors (e.g. 'success')
        """
        pass

    @abstractmethod
    def has_pending_job_for_scheduled_task(self, task_id: int) -> bool:
        """Check if scheduled task has pending jobs.

        Args:
            task_id: Scheduled task ID

        Returns:
            True if has pending jobs, False otherwise
        """
        pass

    @abstractmethod
    def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
        """Update scheduled task's next run time.

        Args:
            task_id: Scheduled task ID
            next_run_at: Next run datetime
        """
        pass

    @abstractmethod
    def update_scheduled_task(self, task_id: int, **updates) -> Any:
        """Update scheduled task fields.

        Args:
            task_id: Scheduled task ID
            **updates: Fields to update

        Returns:
            Updated scheduled task instance
        """
        pass

    @abstractmethod
    def delete_scheduled_task(self, task_id: int) -> bool:
        """Delete scheduled task.

        Args:
            task_id: Scheduled task ID

        Returns:
            True if deleted, False if not found
        """
        pass

    @abstractmethod
    def get_scheduled_tasks(self, enabled_only: bool = False) -> list:
        """Get all scheduled tasks.

        Args:
            enabled_only: Only return enabled tasks

        Returns:
            List of scheduled task instances
        """
        pass

    @abstractmethod
    def get_scheduled_task(self, task_id: int):
        """Get scheduled task by ID.

        Args:
            task_id: Scheduled task ID

        Returns:
            Scheduled task instance or None if not found
        """
        pass

    @abstractmethod
    def get_running_jobs(self, queue_name: str | None = None) -> list:
        """Get currently running jobs.

        Args:
            queue_name: Optional queue name filter

        Returns:
            List of running job instances
        """
        pass

    @abstractmethod
    def get_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        """Return liveness snapshots for every running job (mode-agnostic).

        Each element is a ``sqlery.core.liveness.RunningJobLiveness`` record
        describing a ``status='running'`` job and its assigned worker. The
        daemon's zombie-detection logic consumes these instead of touching the
        ORM directly, so it works in both Django and standalone modes.

        Args:
            queue_names: If provided, only include jobs in these queues.
                         None means all queues.

        Returns:
            List of RunningJobLiveness records (datetimes tz-aware UTC).
        """
        pass

    @abstractmethod
    def fail_zombie_job(self, job_id: int, reason: str) -> bool:
        """Mark a running job failed because it was detected as a zombie.

        Sets error=reason and termination_reason="zombie_job".

        Args:
            job_id: Job to fail.
            reason: Human-readable reason recorded as the job error.

        Returns:
            True if the job was found and marked failed, else False.
        """
        pass

    @abstractmethod
    def has_running_jobs_in_queue(self, queue_name: str, exclude_job_id: int | None = None) -> bool:
        """Check if queue has running jobs.

        Args:
            queue_name: Queue name
            exclude_job_id: Exclude this job ID from check

        Returns:
            True if has running jobs, False otherwise
        """
        pass

    @abstractmethod
    def release_job(self, job_id: int):
        """Release a claimed job back to queued status.

        Args:
            job_id: Job ID
        """
        pass

    @abstractmethod
    def get_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Get jobs with optional filtering and pagination.

        Args:
            status: Filter by status (queued/running/success/failed)
            queue_name: Filter by queue name
            limit: Maximum number of jobs to return
            offset: Number of jobs to skip (for pagination)

        Returns:
            List of job instances
        """
        pass

    @abstractmethod
    def count_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
    ) -> int:
        """Count jobs with optional filtering.

        Args:
            status: Filter by status
            queue_name: Filter by queue name

        Returns:
            Total count of matching jobs
        """
        pass


class AsyncDatabaseBackend(ABC):
    """Abstract async database backend interface.

    Hot-path async analog of :class:`DatabaseBackend`. Only the methods the
    AsyncWorker (ASYN-04) and serverless async paths actually await are
    declared here -- daemon/scheduler internals remain on the sync ABC.

    Method signatures mirror their sync counterparts (same args, awaitable
    return). Concrete implementations live in DjangoAsyncBackend (ASYN-02)
    and SQLAlchemyAsyncBackend (ASYN-03).
    """

    @abstractmethod
    async def aclaim_job(self, queues: list[str], worker_id: str):
        """Async analog of DatabaseBackend.claim_job."""
        pass

    @abstractmethod
    async def amark_running(self, job_id, worker_id) -> None:
        """Async analog of DatabaseBackend.update_worker_heartbeat (running)."""
        pass

    @abstractmethod
    async def amark_success(self, job_id, result) -> None:
        """Async analog of DatabaseBackend.mark_job_success."""
        pass

    @abstractmethod
    async def amark_failed(self, job_id, error: str, traceback: str | None = None) -> None:
        """Async analog of DatabaseBackend.mark_job_failed."""
        pass

    @abstractmethod
    async def amark_shutting_down(self, job_id) -> None:
        """Mark a job as transitioning to shutting-down (ASYN-05 transient state)."""
        pass

    @abstractmethod
    async def aget_status(self, job_id) -> str | None:
        """Async fetch of a job's current status string. Returns None if missing."""
        pass

    @abstractmethod
    async def aget_job(self, job_id):
        """Async analog of DatabaseBackend.get_job_by_id."""
        pass

    @abstractmethod
    async def aupdate_heartbeat(self, worker_id) -> None:
        """Async analog of DatabaseBackend.refresh_worker_heartbeat."""
        pass

    @abstractmethod
    async def aregister_worker(self, worker_id, metadata: dict) -> None:
        """Async analog of DatabaseBackend.update_worker_heartbeat (register)."""
        pass

    @abstractmethod
    async def aunregister_worker(self, worker_id) -> None:
        """Async analog of DatabaseBackend.delete_worker_registration."""
        pass

    @abstractmethod
    async def aclaim_lease(self, queue_name: str, worker_id: str, ttl_seconds: int) -> bool:
        """Async analog of DatabaseBackend.claim_queue_leases (single queue)."""
        pass

    @abstractmethod
    async def arenew_lease(self, queue_name: str, worker_id: str) -> bool:
        """Async analog of DatabaseBackend.renew_queue_leases (single queue)."""
        pass

    @abstractmethod
    async def arelease_lease(self, queue_name: str, worker_id: str) -> None:
        """Async analog of DatabaseBackend.release_queue_leases (single queue)."""
        pass

    @abstractmethod
    async def aget_due_scheduled_tasks(self, now) -> list:
        """Async analog of DatabaseBackend.get_due_scheduled_tasks."""
        pass

    @abstractmethod
    async def aregistry_add(self, registry_name: str, job_id) -> None:
        """Async analog of DatabaseBackend.add_job_to_registry."""
        pass

    @abstractmethod
    async def aregistry_remove(self, registry_name: str, job_id) -> None:
        """Async analog of DatabaseBackend.remove_job_from_registry."""
        pass


class Config(ABC):
    """Abstract configuration interface."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        pass

    @abstractmethod
    def set(self, key: str, value: Any):
        """Set configuration value (standalone mode only)."""
        pass

    @abstractmethod
    def all(self) -> dict:
        """Get all configuration values."""
        pass


def _detect_mode() -> str:
    """Detect if running in Django or standalone mode.

    Returns:
        'django' or 'standalone'
    """
    # Check if Django is configured and available
    if "django" in sys.modules:
        try:
            # from django.conf import settings  # moved to top-level
            # Check if Django settings are configured
            if _django_settings is not None and _django_settings.configured:
                return "django"
        except ImportError:
            pass

    return "standalone"


def _initialize_backend():
    """Initialize the appropriate backend based on detected mode."""
    global _backend

    if _backend is not None:
        return _backend

    mode = _detect_mode()

    if mode == "django":
        # from .django_sqlery.backend import DjangoBackend  # Wrong: looks in compat/django_sqlery/
        from sqlery.django_sqlery.backend import (
            DjangoBackend,
        )  # Inline to avoid circular import: compat -> backend -> compat

        _backend = DjangoBackend()
    else:
        # from .fastapi_sqlery.backend import SQLAlchemyBackend  # Wrong: looks in compat/fastapi_sqlery/
        from sqlery.fastapi_sqlery.backend import (
            SQLAlchemyBackend,
        )  # Inline to avoid circular import: compat -> backend -> compat

        _backend = SQLAlchemyBackend()

    return _backend


def _initialize_config():
    """Initialize the appropriate config based on detected mode."""
    global _config

    if _config is not None:
        return _config

    mode = _detect_mode()

    if mode == "django":
        # from .django_sqlery.config import DjangoConfig  # Wrong: looks in compat/django_sqlery/
        from sqlery.django_sqlery.config import (
            DjangoConfig,
        )  # Inline to avoid circular import: compat -> backend -> compat

        _config = DjangoConfig()
    else:
        # from .fastapi_sqlery.config import StandaloneConfig  # Wrong: looks in compat/fastapi_sqlery/
        from sqlery.fastapi_sqlery.config import (
            StandaloneConfig,
        )  # Inline to avoid circular import: compat -> backend -> compat

        _config = StandaloneConfig()

    return _config


def _reset_backend():
    """Reset the cached backend + config singletons.

    Intended for tests that rebuild process-wide state between cases (e.g. the
    `tests/integration/` matrix that switches integration modes per parametrize
    cell). Not a public API — callers outside the test suite should not depend
    on this function.
    """
    global _backend, _config
    _backend = None
    _config = None


def get_backend() -> DatabaseBackend:
    """Get the active database backend.

    Auto-detects Django vs standalone mode and returns appropriate backend.

    Returns:
        DatabaseBackend instance (DjangoBackend or SQLAlchemyBackend)
    """
    return _initialize_backend()


def get_config(key: str, default: Any = None) -> Any:
    """Get configuration value.

    Works in both Django and standalone modes.

    Args:
        key: Configuration key (e.g., 'DEFAULT_QUEUE', 'MAX_WORKERS_PER_NODE')
        default: Default value if key not found

    Returns:
        Configuration value

    Example:
        >>> get_config('DEFAULT_QUEUE', 'default')
        'default'
        >>> get_config('MAX_WORKERS_PER_NODE', 3)
        5
    """
    config = _initialize_config()
    return config.get(key, default)


def set_config(key: str, value: Any):
    """Set configuration value (standalone mode only).

    In Django mode, this is a no-op (use Django settings instead).

    Args:
        key: Configuration key
        value: Configuration value
    """
    config = _initialize_config()
    config.set(key, value)


def get_all_config() -> dict:
    """Get all configuration values as a dictionary.

    Returns:
        Dict of all config values
    """
    config = _initialize_config()
    return config.all()


def is_django_mode() -> bool:
    """Check if running in Django mode.

    Returns:
        True if Django mode, False if standalone
    """
    return _detect_mode() == "django"


def is_standalone_mode() -> bool:
    """Check if running in standalone mode.

    Returns:
        True if standalone mode, False if Django
    """
    return _detect_mode() == "standalone"


def initialize(
    database_url: str | None = None,
    # max_workers: int = 3,
    max_workers: int = 1,
    worker_queues: list[str] | None = None,
    enable_daemon: bool = True,
    daemon_check_interval: int = 10,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    **kwargs,
):
    """Initialize sqlery in standalone mode.

    This is only needed in standalone mode. Django mode auto-configures from settings.

    Args:
        database_url: PostgreSQL connection URL
        max_workers: Maximum worker processes per node
        worker_queues: List of queue names to process
        enable_daemon: Enable daemon mode
        daemon_check_interval: Seconds between daemon checks
        pool_size: Connection pool size
        max_overflow: Max overflow connections beyond pool_size
        pool_timeout: Seconds to wait for a connection from the pool
        pool_recycle: Seconds before recycling connections (prevents stale connections)
        **kwargs: Additional configuration options

    Example:
        >>> from sqlery import initialize
        >>> initialize(
        ...     database_url='postgresql://localhost/jobs',
        ...     max_workers=5,
        ...     worker_queues=['high', 'default', 'low'],
        ...     enable_daemon=True,
        ...     pool_size=10,
        ...     max_overflow=20,
        ...     pool_timeout=60,
        ... )
    """
    if is_django_mode():
        raise RuntimeError(
            "initialize() is only for standalone mode. "
            "In Django mode, configure via settings.py instead."
        )

    # Set core configuration
    if database_url:
        set_config("DATABASE_URL", database_url)

    set_config("MAX_WORKERS_PER_NODE", max_workers)
    set_config("WORKER_QUEUES", worker_queues or ["default"])
    set_config("ENABLE_DAEMON", enable_daemon)
    set_config("DAEMON_CHECK_INTERVAL", daemon_check_interval)
    set_config("POOL_SIZE", pool_size)
    set_config("MAX_OVERFLOW", max_overflow)
    set_config("POOL_TIMEOUT", pool_timeout)
    set_config("POOL_RECYCLE", pool_recycle)

    # Set any additional config
    for key, value in kwargs.items():
        set_config(key, value)

    # Initialize database (creates tables)
    # from .fastapi_sqlery.database import init_database  # Wrong: looks in compat/fastapi_sqlery/
    from sqlery.fastapi_sqlery.database import (
        init_database,
    )  # Inline to avoid circular import: compat -> backend -> compat

    # init_database(database_url or get_config('DATABASE_URL'))
    init_database(
        database_url or get_config("DATABASE_URL"),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
    )


# Export main API
__all__ = [
    "DatabaseBackend",
    "AsyncDatabaseBackend",
    "Config",
    "get_backend",
    "get_config",
    "set_config",
    "get_all_config",
    "is_django_mode",
    "is_standalone_mode",
    "initialize",
]
