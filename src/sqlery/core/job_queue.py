"""Django-agnostic job enqueuing and queue management."""

from datetime import datetime, timezone as tz

from ..compat import get_backend, get_config
from .claiming import expire_ttl_jobs


class Queue:
    """A queue for enqueueing jobs.

    Provides RQ-style API for job enqueueing with queue-specific defaults.

    Example:
        >>> from sqlery import get_queue
        >>> queue = get_queue('email')
        >>> job = queue.enqueue('myapp.tasks.send_email', to='user@example.com')
        >>> job = queue.enqueue_at(datetime.now() + timedelta(hours=1), 'myapp.tasks.generate_report')
    """

    def __init__(
        self,
        name: str = "default",
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
    ):
        """Initialize a queue.

        Args:
            name: Queue name
            priority: Default priority for jobs in this queue
            max_retries: Default max retries for jobs in this queue
            retry_backoff: Default retry backoff for jobs in this queue
            allow_parallel: Default allow_parallel for jobs in this queue
            timeout_seconds: Default timeout for jobs in this queue
        """
        self.name = name
        self._priority = priority
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._allow_parallel = allow_parallel
        self._timeout_seconds = timeout_seconds

    def enqueue(
        self,
        task_path: str,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
        **kwargs,
    ):
        """Enqueue a job for immediate execution.

        Args:
            task_path: Python path to callable (e.g., 'myapp.tasks.send_email')
            priority: Priority override for this job
            max_retries: Max retries override for this job
            retry_backoff: Retry backoff override for this job
            allow_parallel: Allow parallel override for this job
            timeout_seconds: Timeout override for this job
            **kwargs: Keyword arguments to pass to the task function

        Returns:
            Job instance

        Example:
            >>> queue = Queue('email', priority=10, max_retries=3)
            >>> job = queue.enqueue('myapp.tasks.send_email', to='user@example.com')
        """
        return enqueue(
            task_path=task_path,
            queue=self.name,
            priority=priority if priority is not None else self._priority,
            max_retries=max_retries if max_retries is not None else self._max_retries,
            retry_backoff=retry_backoff if retry_backoff is not None else self._retry_backoff,
            allow_parallel=allow_parallel if allow_parallel is not None else self._allow_parallel,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else self._timeout_seconds,
            **kwargs,
        )

    def enqueue_at(
        self,
        run_at: datetime,
        task_path: str,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
        **kwargs,
    ):
        """Enqueue a job to run at a specific datetime.

        Args:
            run_at: When to run the job (timezone-aware recommended)
            task_path: Python path to callable
            priority: Priority override for this job
            max_retries: Max retries override for this job
            retry_backoff: Retry backoff override for this job
            allow_parallel: Allow parallel override for this job
            timeout_seconds: Timeout override for this job
            **kwargs: Keyword arguments to pass to the task function

        Returns:
            Job instance

        Example:
            >>> from datetime import datetime, timedelta
            >>> queue = Queue('reports')
            >>> run_time = datetime.now() + timedelta(hours=1)
            >>> job = queue.enqueue_at(run_time, 'myapp.tasks.generate_report')
        """
        return enqueue_at(
            task_path=task_path,
            run_at=run_at,
            queue=self.name,
            priority=priority if priority is not None else self._priority,
            max_retries=max_retries if max_retries is not None else self._max_retries,
            retry_backoff=retry_backoff if retry_backoff is not None else self._retry_backoff,
            allow_parallel=allow_parallel if allow_parallel is not None else self._allow_parallel,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else self._timeout_seconds,
            **kwargs,
        )

    def __repr__(self):
        return f"Queue('{self.name}')"


def enqueue(
    task_path: str,
    queue: str | None = None,
    priority: int | None = None,
    max_retries: int | None = None,
    retry_backoff: float | None = None,
    allow_parallel: bool | None = None,
    timeout_seconds: int | None = None,
    **kwargs,
):
    """Enqueue a job for immediate execution.

    Works in both Django and standalone modes by delegating to the active backend.

    Args:
        task_path: Python path to callable (e.g., 'myapp.tasks.send_email')
        queue: Queue name. Defaults to config DEFAULT_QUEUE.
        priority: Priority. Higher = sooner. Defaults to config DEFAULT_PRIORITY.
        max_retries: Maximum retry attempts. Defaults to 0 (no retries).
        retry_backoff: Exponential backoff multiplier in seconds. Defaults to 1.0.
        allow_parallel: Allow parallel execution in same queue. Defaults to False.
        timeout_seconds: Maximum execution time in seconds. Defaults to None (no timeout).
        **kwargs: Keyword arguments to pass to the task function.

    Returns:
        Job instance (Django QueuedJob or SQLAlchemy Job)

    Example:
        >>> from sqlery.core.job_queue import enqueue
        >>> job = enqueue(
        ...     'myapp.tasks.send_email',
        ...     queue='email',
        ...     priority=10,
        ...     max_retries=3,
        ...     allow_parallel=True,
        ...     timeout_seconds=300,
        ...     to_email='user@example.com',
        ...     subject='Welcome'
        ... )
        >>> job.id
        42
        >>> job.status
        'queued'
    """
    # from ..compat import get_backend, get_config  # moved to top-level

    # Get defaults from config
    if queue is None:
        queue = get_config("DEFAULT_QUEUE", "default")

    if priority is None:
        priority = get_config("DEFAULT_PRIORITY", 0)

    if max_retries is None:
        max_retries = get_config("DEFAULT_MAX_RETRIES", 0)

    if retry_backoff is None:
        retry_backoff = get_config("DEFAULT_RETRY_BACKOFF", 1.0)

    if allow_parallel is None:
        allow_parallel = False

    # Use backend to create job
    backend = get_backend()
    job = backend.create_job(
        task_path=task_path,
        kwargs=kwargs,
        queue_name=queue,
        priority=priority,
        scheduled_at=None,  # Run immediately
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout_seconds,
    )

    # Trigger worker if configured
    _trigger_worker_if_needed()

    return job


def enqueue_at(
    task_path: str,
    run_at: datetime,
    queue: str | None = None,
    priority: int | None = None,
    max_retries: int | None = None,
    retry_backoff: float | None = None,
    allow_parallel: bool | None = None,
    timeout_seconds: int | None = None,
    **kwargs,
):
    """Enqueue a job to run at a specific datetime.

    Works in both Django and standalone modes by delegating to the active backend.

    Args:
        task_path: Python path to callable (e.g., 'myapp.tasks.send_email')
        run_at: When to run the job (timezone-aware recommended)
        queue: Queue name. Defaults to config DEFAULT_QUEUE.
        priority: Priority. Higher = sooner. Defaults to config DEFAULT_PRIORITY.
        max_retries: Maximum retry attempts. Defaults to 0 (no retries).
        retry_backoff: Exponential backoff multiplier in seconds. Defaults to 1.0.
        allow_parallel: Allow parallel execution in same queue. Defaults to False.
        timeout_seconds: Maximum execution time in seconds. Defaults to None (no timeout).
        **kwargs: Keyword arguments to pass to the task function.

    Returns:
        Job instance (Django QueuedJob or SQLAlchemy Job)

    Example:
        >>> from sqlery.core.job_queue import enqueue_at
        >>> from datetime import datetime, timezone, timedelta
        >>> run_time = datetime.now(timezone.utc) + timedelta(hours=1)
        >>> job = enqueue_at(
        ...     'myapp.tasks.send_email',
        ...     run_time,
        ...     queue='email',
        ...     max_retries=3,
        ...     allow_parallel=True,
        ...     timeout_seconds=300,
        ...     to_email='user@example.com'
        ... )
        >>> job.scheduled_at
        datetime.datetime(...)
    """
    # from ..compat import get_backend, get_config  # moved to top-level

    # Get defaults from config
    if queue is None:
        queue = get_config("DEFAULT_QUEUE", "default")

    if priority is None:
        priority = get_config("DEFAULT_PRIORITY", 0)

    if max_retries is None:
        max_retries = get_config("DEFAULT_MAX_RETRIES", 0)

    if retry_backoff is None:
        retry_backoff = get_config("DEFAULT_RETRY_BACKOFF", 1.0)

    if allow_parallel is None:
        allow_parallel = False

    # Ensure timezone-aware
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=tz.utc)

    # Use backend to create job
    backend = get_backend()
    job = backend.create_job(
        task_path=task_path,
        kwargs=kwargs,
        queue_name=queue,
        priority=priority,
        scheduled_at=run_at,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout_seconds,
    )

    # Note: Worker will skip this until scheduled_at is reached
    return job


def _trigger_worker_if_needed():
    """Trigger worker to process queue (if enabled)."""
    # from ..compat import get_config  # moved to top-level

    # In traditional deployment with middleware, workers are already running
    # In serverless, this is a no-op (external scheduler triggers workers)
    # Future: Could invoke Lambda here for immediate execution

    auto_trigger = get_config("AUTO_TRIGGER_WORKER", False)
    if not auto_trigger:
        return

    # Placeholder for future implementation
    # Could use django-tasks, Lambda invoke, etc.
    pass


def claim_job(queues: list[str], worker_id: str):
    """Claim next available job from specified queues.

    Uses SELECT FOR UPDATE SKIP LOCKED for atomic job claiming.

    Args:
        queues: List of queue names to check (in priority order)
        worker_id: Unique identifier for the claiming worker

    Returns:
        Job instance if found, None otherwise
    """
    # from ..compat import get_backend  # moved to top-level

    backend = get_backend()
    # H1 follow-up: claim_job no longer expires TTL jobs for free (that moved
    # to the persistent worker loops); this one-shot entry point must expire
    # explicitly before claiming.
    expire_ttl_jobs(backend)
    return backend.claim_job(queues, worker_id)


def get_queue_stats(queue_name: str | None = None) -> dict:
    """Get statistics for a queue or all queues.

    Args:
        queue_name: Queue name, or None for all queues

    Returns:
        Dict with queue statistics (counts by status)
    """
    # from ..compat import get_backend  # moved to top-level

    backend = get_backend()
    return backend.get_queue_stats(queue_name)


def cancel_job(job_id: int) -> bool:
    """Cancel a queued or scheduled job.

    Args:
        job_id: Job ID to cancel

    Returns:
        True if cancelled, False if not found or already running
    """
    # from ..compat import get_backend  # moved to top-level

    backend = get_backend()
    return backend.cancel_job(job_id)


def retry_failed_jobs(queue_name: str | None = None, max_jobs: int | None = None) -> int:
    """Retry failed jobs by resetting them to queued status.

    Args:
        queue_name: Queue name, or None for all queues
        max_jobs: Maximum number of jobs to retry, or None for all

    Returns:
        Number of jobs retried
    """
    # from ..compat import get_backend  # moved to top-level

    backend = get_backend()
    return backend.retry_failed_jobs(queue_name, max_jobs)


def get_queue(
    name: str = "default",
    priority: int | None = None,
    max_retries: int | None = None,
    retry_backoff: float | None = None,
    allow_parallel: bool | None = None,
    timeout_seconds: int | None = None,
) -> Queue:
    """Get a Queue instance for enqueueing jobs.

    Provides RQ-style API with queue-specific defaults.

    Args:
        name: Queue name
        priority: Default priority for jobs in this queue
        max_retries: Default max retries for jobs in this queue
        retry_backoff: Default retry backoff for jobs in this queue
        allow_parallel: Default allow_parallel for jobs in this queue
        timeout_seconds: Default timeout for jobs in this queue

    Returns:
        Queue instance

    Example:
        >>> from sqlery import get_queue
        >>> email_queue = get_queue('email', priority=10, max_retries=3, allow_parallel=True)
        >>> job = email_queue.enqueue('myapp.tasks.send_email', to='user@example.com')
        >>>
        >>> # Or schedule for later
        >>> from datetime import datetime, timedelta
        >>> run_time = datetime.now() + timedelta(hours=1)
        >>> job = email_queue.enqueue_at(run_time, 'myapp.tasks.send_reminder')
    """
    return Queue(
        name=name,
        priority=priority,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout_seconds,
    )
