"""Django-agnostic job decorator and enqueueing logic."""

from typing import Callable, Any
from datetime import datetime
from functools import update_wrapper

from .job_queue import enqueue as core_enqueue, enqueue_at as core_enqueue_at


class JobWrapper:
    """Wrapper for functions decorated with @job.

    Provides .enqueue(), .delay(), and .enqueue_at() methods on the decorated function.
    This is the core job wrapper that works in both Django and standalone modes.
    """

    def __init__(
        self,
        func: Callable,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
        tags: list[str] | None = None,
        autoretry_for: tuple | list | None = None,
        retry_backoff_max: float | None = None,
        retry_jitter: bool | None = None,
        bind: bool = False,
        soft_time_limit: float | None = None,
    ):
        """Initialize job wrapper.

        Args:
            func: The function to wrap
            queue: Default queue name for this job
            priority: Default priority for this job
            max_retries: Default maximum retry attempts
            retry_backoff: Default retry backoff multiplier
            allow_parallel: Default allow parallel execution in same queue
            timeout_seconds: Default maximum execution time in seconds
        """
        self.func = func
        self.queue = queue
        self.priority = priority
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.allow_parallel = allow_parallel
        self.timeout_seconds = timeout_seconds
        self.tags = list(tags) if tags else []
        # Retry policy (read by the executors; see sqlery.core.retry)
        self.autoretry_for = tuple(autoretry_for) if autoretry_for else None
        self.retry_backoff_max = retry_backoff_max
        self.retry_jitter = retry_jitter
        self.bind = bind
        self.soft_time_limit = soft_time_limit

        # Calculate task path (module.path.to.function)
        self.task_path = f"{func.__module__}.{func.__qualname__}"

        # Preserve function metadata using update_wrapper (correct for class-based wrappers)
        # See: https://docs.python.org/3/library/functools.html#functools.update_wrapper
        # Note: wraps(func)(self) is unusual and can break pickling/introspection
        update_wrapper(self, func)
        self.__annotations__ = getattr(func, "__annotations__", {})

    def __call__(self, *args, **kwargs) -> Any:
        """Call the original function directly.

        This allows decorated functions to still be called normally:
        >>> send_email()  # Direct call

        For ``bind=True`` tasks a detached context is injected as the first argument.
        """
        if self.bind:
            from .retry import TaskContext

            return self.func(TaskContext(task_path=self.task_path), *args, **kwargs)
        return self.func(*args, **kwargs)

    def enqueue(
        self,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
        **kwargs,
    ):
        """Enqueue this job for immediate execution.

        Args:
            queue: Queue name (overrides decorator default)
            priority: Priority (overrides decorator default)
            max_retries: Maximum retry attempts (overrides decorator default)
            retry_backoff: Retry backoff multiplier (overrides decorator default)
            allow_parallel: Allow parallel execution in same queue (overrides decorator default)
            timeout_seconds: Maximum execution time in seconds (overrides decorator default)
            **kwargs: Keyword arguments to pass to the task function

        Returns:
            Job instance (backend-specific)

        Example:
            >>> send_email.enqueue()
            >>> send_email.enqueue(
            ...     queue='high-priority',
            ...     priority=10,
            ...     max_retries=3,
            ...     allow_parallel=True,
            ...     timeout_seconds=300,
            ...     to_email='user@example.com'
            ... )
        """
        # from .job_queue import enqueue as core_enqueue  # moved to top-level

        return core_enqueue(
            self.task_path,
            queue=queue if queue is not None else self.queue,
            priority=priority if priority is not None else self.priority,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            retry_backoff=retry_backoff if retry_backoff is not None else self.retry_backoff,
            allow_parallel=allow_parallel if allow_parallel is not None else self.allow_parallel,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
            _tags=self.tags or None,
            **kwargs,
        )

    def delay(
        self,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
        **kwargs,
    ):
        """Alias for .enqueue() (Celery-style API).

        Args:
            queue: Queue name (overrides decorator default)
            priority: Priority (overrides decorator default)
            max_retries: Maximum retry attempts (overrides decorator default)
            retry_backoff: Retry backoff multiplier (overrides decorator default)
            allow_parallel: Allow parallel execution in same queue (overrides decorator default)
            timeout_seconds: Maximum execution time in seconds (overrides decorator default)
            **kwargs: Keyword arguments to pass to the task function

        Returns:
            Job instance (backend-specific)

        Example:
            >>> send_email.delay()  # Same as .enqueue()
            >>> send_email.delay(to_email='user@example.com')
        """
        return self.enqueue(
            queue=queue,
            priority=priority,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )

    def enqueue_at(
        self,
        run_at: datetime,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
        **kwargs,
    ):
        """Enqueue this job to run at a specific datetime.

        Args:
            run_at: When to run the job
            queue: Queue name (overrides decorator default)
            priority: Priority (overrides decorator default)
            max_retries: Maximum retry attempts (overrides decorator default)
            retry_backoff: Retry backoff multiplier (overrides decorator default)
            allow_parallel: Allow parallel execution in same queue (overrides decorator default)
            timeout_seconds: Maximum execution time in seconds (overrides decorator default)
            **kwargs: Keyword arguments to pass to the task function

        Returns:
            Job instance (backend-specific)

        Example:
            >>> from datetime import datetime, timezone, timedelta
            >>> run_time = datetime.now(timezone.utc) + timedelta(hours=1)
            >>> send_email.enqueue_at(
            ...     run_time,
            ...     max_retries=3,
            ...     allow_parallel=True,
            ...     timeout_seconds=300,
            ...     to_email='user@example.com'
            ... )
        """
        # from .job_queue import enqueue_at as core_enqueue_at  # moved to top-level

        return core_enqueue_at(
            self.task_path,
            run_at,
            queue=queue if queue is not None else self.queue,
            priority=priority if priority is not None else self.priority,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            retry_backoff=retry_backoff if retry_backoff is not None else self.retry_backoff,
            allow_parallel=allow_parallel if allow_parallel is not None else self.allow_parallel,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
            _tags=self.tags or None,
            **kwargs,
        )

    def apply_async(
        self,
        args: tuple | list | None = None,
        kwargs: dict | None = None,
        *,
        countdown: float | None = None,
        eta: datetime | None = None,
        expires: float | datetime | None = None,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        allow_parallel: bool | None = None,
        timeout_seconds: int | None = None,
    ):
        """Celery-style enqueue returning an :class:`~sqlery.core.result.AsyncResult`.

        Unlike :meth:`enqueue`, task arguments are passed explicitly (``args``/``kwargs``),
        so option names can never collide with a task's own keyword arguments.

        Args:
            args: Positional arguments for the task.
            kwargs: Keyword arguments for the task.
            countdown: Delay execution by this many seconds.
            eta: Run at this datetime (naive values are treated as UTC).
            expires: Seconds (or a datetime) after which a still-queued job is failed, not run.

        Example:
            >>> res = add.apply_async(args=(2, 3), countdown=10, expires=300)
            >>> res.get(timeout=60)
            5
        """
        from .result import apply_async_job

        payload = dict(kwargs or {})
        if args:
            payload["_args"] = list(args)
        return apply_async_job(
            self.task_path,
            payload,
            queue=queue if queue is not None else self.queue,
            priority=priority if priority is not None else self.priority,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            retry_backoff=retry_backoff if retry_backoff is not None else self.retry_backoff,
            allow_parallel=allow_parallel if allow_parallel is not None else self.allow_parallel,
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else self.timeout_seconds
            ),
            tags=self.tags,
            countdown=countdown,
            eta=eta,
            expires=expires,
        )

    def __repr__(self) -> str:
        """String representation of the job wrapper."""
        return f"<JobWrapper: {self.task_path} queue={self.queue} priority={self.priority}>"


def job(
    func: Callable | None = None,
    *,
    queue: str | None = None,
    priority: int | None = None,
    max_retries: int | None = None,
    retry_backoff: float | None = None,
    allow_parallel: bool | None = None,
    timeout_seconds: int | None = None,
    tags: list[str] | None = None,
    autoretry_for: tuple | list | None = None,
    retry_backoff_max: float | None = None,
    retry_jitter: bool | None = None,
    bind: bool = False,
    soft_time_limit: float | None = None,
) -> JobWrapper | Callable:
    """Decorator to mark a function as an enqueueable job.

    Works in both Django and standalone modes with identical API.

    Can be used with or without arguments:

    Without arguments:
        >>> @job
        >>> def my_task():
        ...     pass

    With arguments:
        >>> @job(queue='email', priority=10, max_retries=3, allow_parallel=True, timeout_seconds=300)
        >>> def send_email():
        ...     pass

    Usage:
        >>> send_email.enqueue()           # Immediate execution
        >>> send_email.delay()             # Alias for .enqueue()
        >>> send_email.enqueue_at(run_time)  # Scheduled execution

        >>> # Override decorator defaults
        >>> send_email.enqueue(queue='high-priority', priority=100, max_retries=5)

        >>> # Still callable normally
        >>> send_email()  # Direct call

    Args:
        func: The function to decorate (when used without arguments)
        queue: Default queue name for this job
        priority: Default priority for this job
        max_retries: Default maximum retry attempts
        retry_backoff: Default retry backoff multiplier
        allow_parallel: Default allow parallel execution in same queue
        timeout_seconds: Default maximum execution time in seconds
        tags: Tags for TAG_CONCURRENCY_LIMITS / TAG_RATE_LIMITS (e.g. ['acme-api'])
        autoretry_for: Exception classes that trigger a retry (needs max_retries > 0).
            Unset keeps the legacy behaviour of retrying on any exception.
        retry_backoff_max: Cap (seconds) on the exponential retry delay
        retry_jitter: Randomise the retry delay (full jitter)
        soft_time_limit: Seconds after which ``SoftTimeLimitExceeded`` is raised inside the task
            (catchable, for cleanup). ``timeout_seconds`` stays the hard limit that follows.
        bind: Pass a ``TaskContext`` as the first argument; ``self.retry(...)`` retries the job

    Returns:
        JobWrapper instance with .enqueue(), .delay(), and .enqueue_at() methods
    """

    def decorator(f: Callable) -> JobWrapper:
        return JobWrapper(
            f,
            queue=queue,
            priority=priority,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
            timeout_seconds=timeout_seconds,
            tags=tags,
            autoretry_for=autoretry_for,
            retry_backoff_max=retry_backoff_max,
            retry_jitter=retry_jitter,
            bind=bind,
            soft_time_limit=soft_time_limit,
        )

    # Handle both @job and @job(...) syntax
    if func is None:
        # Called with arguments: @job(queue='email')
        return decorator
    else:
        # Called without arguments: @job
        return decorator(func)
