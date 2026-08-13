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
        """
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
            **kwargs,
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
        )

    # Handle both @job and @job(...) syntax
    if func is None:
        # Called with arguments: @job(queue='email')
        return decorator
    else:
        # Called without arguments: @job
        return decorator(func)
