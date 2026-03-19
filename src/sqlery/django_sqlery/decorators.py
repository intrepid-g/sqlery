"""Decorator API for sqlery job enqueueing.

Provides @job and @async_job decorators for easy task definition.
"""

import functools
import inspect
import logging
from typing import Any, Callable

from sqlery.compat import get_backend, get_config

# from sqlery.async_queue import AsyncQueue
try:
    from sqlery.async_queue import AsyncQueue
except ImportError:
    AsyncQueue = None  # async_queue depends on removed backends package

logger = logging.getLogger(__name__)


class JobFunction:
    """Wrapper for decorated sync function with .delay() method."""

    def __init__(
        self,
        func: Callable,
        queue: str | None = None,
        priority: int | None = None,
        timeout: int | None = None,
        max_retries: int = 0,
        retry_backoff: float = 1.0,
        allow_parallel: bool = True,
    ):
        """Initialize job function wrapper.

        Args:
            func: The function to wrap
            queue: Queue name (None = use system default)
            priority: Job priority (None = use system default)
            timeout: Job timeout in seconds (default: None)
            max_retries: Maximum retry attempts (default: 0)
            retry_backoff: Backoff multiplier for retries (default: 1.0)
            allow_parallel: Allow parallel execution (default: True)
        """
        self.func = func
        self.queue_name = queue
        self.priority = priority
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.allow_parallel = allow_parallel

        # Copy function metadata
        functools.update_wrapper(self, func)

    @property
    def queue(self):
        """Alias for queue_name for backward compatibility."""
        return self.queue_name

    @property
    def task_path(self):
        """Return the task path for this job."""
        return f"{self.func.__module__}.{self.func.__qualname__}"

    def __repr__(self):
        """Return a string representation of this job wrapper."""
        return f"JobWrapper({self.task_path}, queue={self.queue_name}, priority={self.priority})"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the original function directly."""
        return self.func(*args, **kwargs)

    def enqueue(self, *args: Any, queue: str | None = None, priority: int | None = None, max_retries: int | None = None, retry_backoff: float | None = None, **kwargs: Any) -> Any:
        """Enqueue the job for execution (RQ-style).

        Args:
            *args: Positional arguments for the function
            queue: Override queue name (optional)
            priority: Override priority (optional)
            **kwargs: Keyword arguments for the function

        Returns:
            Job instance from backend

        Example:
            @job(queue='emails')
            def send_email(to, subject, body):
                # Send email logic
                pass

            # Enqueue job (RQ-style)
            job = send_email.enqueue('user@example.com', 'Hello', 'World')
            print(f"Job {job.id} enqueued")

            # Override queue
            job = send_email.enqueue('user@example.com', 'Hello', 'World', queue='urgent')
        """
        # from sqlery.compat import get_backend, get_config  # moved to top-level

        # Build task path
        task_path = f"{self.func.__module__}.{self.func.__qualname__}"

        # Prepare job kwargs (merge args into kwargs if needed)
        job_kwargs = kwargs.copy()

        # Handle positional arguments by binding them to the function signature
        if args:
            sig = inspect.signature(self.func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            job_kwargs = dict(bound.arguments)

        # Determine queue and priority: override > decorator > system default
        if queue is not None:
            effective_queue = queue
        elif self.queue_name is not None:
            effective_queue = self.queue_name
        else:
            effective_queue = get_config("DEFAULT_QUEUE", "default")

        if priority is not None:
            effective_priority = priority
        elif self.priority is not None:
            effective_priority = self.priority
        else:
            effective_priority = get_config("DEFAULT_PRIORITY", 0)

        # Use override max_retries and retry_backoff if provided
        effective_max_retries = max_retries if max_retries is not None else self.max_retries
        effective_retry_backoff = retry_backoff if retry_backoff is not None else self.retry_backoff

        # Use backend to create job
        backend = get_backend()
        job = backend.create_job(
            task_path=task_path,
            kwargs=job_kwargs,
            queue_name=effective_queue,
            priority=effective_priority,
            scheduled_at=None,  # Run immediately
            max_retries=effective_max_retries,
            retry_backoff=effective_retry_backoff,
            allow_parallel=self.allow_parallel,
            timeout_seconds=self.timeout,
        )

        return job

    def delay(self, *args: Any, queue: str | None = None, priority: int | None = None, max_retries: int | None = None, **kwargs: Any) -> dict[str, Any]:
        """Enqueue the job for execution (Celery-style alias).

        This is an alias for .enqueue() for Celery compatibility.

        Args:
            *args: Positional arguments for the function
            queue: Override queue name (optional)
            priority: Override priority (optional)
            **kwargs: Keyword arguments for the function

        Returns:
            Job dict from backend

        Example:
            @job(queue='emails')
            def send_email(to, subject, body):
                # Send email logic
                pass

            # Enqueue job (Celery-style)
            job = send_email.delay('user@example.com', 'Hello', 'World')
            print(f"Job {job['id']} enqueued")
        """
        return self.enqueue(*args, queue=queue, priority=priority, max_retries=max_retries, **kwargs)

    def enqueue_at(self, scheduled_at, *args: Any, queue: str | None = None, priority: int | None = None, **kwargs: Any) -> Any:
        """Enqueue the job to run at a specific time.

        Args:
            scheduled_at: When to run the job (datetime)
            *args: Positional arguments for the function
            queue: Override queue name (optional)
            priority: Override priority (optional)
            **kwargs: Keyword arguments for the function

        Returns:
            Job instance from backend
        """
        # from sqlery.compat import get_backend, get_config  # moved to top-level

        # Build task path
        task_path = f"{self.func.__module__}.{self.func.__qualname__}"

        # Prepare job kwargs
        job_kwargs = kwargs.copy()

        if args:
            sig = inspect.signature(self.func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            job_kwargs = dict(bound.arguments)

        # Determine queue and priority: override > decorator > system default
        if queue is not None:
            effective_queue = queue
        elif self.queue_name is not None:
            effective_queue = self.queue_name
        else:
            effective_queue = get_config("DEFAULT_QUEUE", "default")

        if priority is not None:
            effective_priority = priority
        elif self.priority is not None:
            effective_priority = self.priority
        else:
            effective_priority = get_config("DEFAULT_PRIORITY", 0)

        # Use backend to create job
        backend = get_backend()
        job = backend.create_job(
            task_path=task_path,
            kwargs=job_kwargs,
            queue_name=effective_queue,
            priority=effective_priority,
            scheduled_at=scheduled_at,
            max_retries=self.max_retries,
            retry_backoff=self.retry_backoff,
            allow_parallel=self.allow_parallel,
            timeout_seconds=self.timeout,
        )

        return job


class AsyncJobFunction:
    """Wrapper for decorated async function with .delay() method."""

    def __init__(
        self,
        func: Callable,
        queue: str = 'default',
        priority: int = 0,
        timeout: int | None = None,
        max_retries: int = 0,
        retry_backoff: float = 1.0,
        allow_parallel: bool = True,
    ):
        """Initialize async job function wrapper.

        Args:
            func: The async function to wrap
            queue: Queue name (default: 'default')
            priority: Job priority (default: 0)
            timeout: Job timeout in seconds (default: None)
            max_retries: Maximum retry attempts (default: 0)
            retry_backoff: Backoff multiplier for retries (default: 1.0)
            allow_parallel: Allow parallel execution (default: True)
        """
        self.func = func
        self.queue_name = queue
        self.priority = priority
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.allow_parallel = allow_parallel

        # Copy function metadata
        functools.update_wrapper(self, func)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the original async function directly."""
        return await self.func(*args, **kwargs)

    async def enqueue(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Enqueue the job for execution (RQ-style, async).

        Args:
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Job dict from backend

        Example:
            @async_job(queue='emails')
            async def send_email_async(to, subject, body):
                # Async email logic
                pass

            # Enqueue job (RQ-style)
            job = await send_email_async.enqueue('user@example.com', 'Hello', 'World')
            print(f"Job {job['id']} enqueued")
        """
        # Get default queue instance
        if AsyncQueue._default_backend is None:
            raise RuntimeError(
                "No default backend configured. Call AsyncQueue.configure(backend) first."
            )

        queue = AsyncQueue(name=self.queue_name, backend=AsyncQueue._default_backend)

        # Enqueue with configured options
        return await queue.enqueue(
            self.func,
            *args,
            queue=self.queue_name,
            priority=self.priority,
            timeout=self.timeout,
            max_retries=self.max_retries,
            retry_backoff=self.retry_backoff,
            allow_parallel=self.allow_parallel,
            **kwargs
        )

    async def delay(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Enqueue the job for execution (Celery-style alias, async).

        This is an alias for .enqueue() for Celery compatibility.

        Args:
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Job dict from backend

        Example:
            @async_job(queue='emails')
            async def send_email_async(to, subject, body):
                # Async email logic
                pass

            # Enqueue job (Celery-style)
            job = await send_email_async.delay('user@example.com', 'Hello', 'World')
            print(f"Job {job['id']} enqueued")
        """
        return await self.enqueue(*args, **kwargs)


def job(
    func: Callable | None = None,
    queue: str | None = None,
    priority: int | None = None,
    timeout: int | None = None,
    timeout_seconds: int | None = None,  # Alias for timeout
    max_retries: int = 0,
    retry_backoff: float = 1.0,
    retry: Any = None,  # d-t-s / RQ Retry compat
    allow_parallel: bool = True,
) -> Callable[[Callable], JobFunction] | JobFunction:
    """Decorator for marking sync functions as enqueueable jobs.

    Args:
        func: The function to decorate (when used as @job without parentheses)
        queue: Queue name (None = use system default)
        priority: Job priority (None = use system default)
        timeout: Job timeout in seconds (default: None)
        max_retries: Maximum retry attempts (default: 0)
        retry_backoff: Backoff multiplier for retries (default: 1.0)
        allow_parallel: Allow parallel execution (default: True)

    Returns:
        Decorated function with .delay() method

    Example:
        from sqlery import job
        # from sqlery.backends import BackendFactory  # REMOVED in v0.13
        from sqlery.compat import get_backend

        # Define job (both syntaxes supported)
        @job
        def simple_task():
            pass

        @job(queue='emails', timeout=300)
        def send_email(to, subject, body):
            # Send email logic
            pass

        # Enqueue job
        job = send_email.delay('user@example.com', 'Hello', 'World')
        print(f"Job {job['id']} enqueued")

        # Or call directly
        send_email('user@example.com', 'Hello', 'World')
    """
    # timeout_seconds is an alias for timeout
    effective_timeout = timeout_seconds if timeout_seconds is not None else timeout

    # d-t-s / RQ Retry compat: extract max_retries and retry_backoff
    effective_max_retries = max_retries
    effective_retry_backoff = retry_backoff
    if retry is not None:
        effective_max_retries = getattr(retry, "max", max_retries)
        intervals = getattr(retry, "intervals", None)
        if intervals:
            effective_retry_backoff = float(intervals[0])

    def decorator(f: Callable) -> JobFunction:
        return JobFunction(
            func=f,
            queue=queue,
            priority=priority,
            timeout=effective_timeout,
            max_retries=effective_max_retries,
            retry_backoff=effective_retry_backoff,
            allow_parallel=allow_parallel,
        )

    if func is not None and callable(func):
        # Called as @job without parentheses
        return decorator(func)
    elif func is not None:
        # Called as @job('queue_name') — d-t-s positional queue arg
        return job(queue=func, priority=priority, timeout=timeout,
                   timeout_seconds=timeout_seconds, max_retries=max_retries,
                   retry_backoff=retry_backoff, retry=retry,
                   allow_parallel=allow_parallel)
    else:
        # Called as @job() or @job(queue='name') with parentheses
        return decorator


def async_job(
    func: Callable | None = None,
    queue: str = 'default',
    priority: int = 0,
    timeout: int | None = None,
    timeout_seconds: int | None = None,  # Alias for timeout
    max_retries: int = 0,
    retry_backoff: float = 1.0,
    allow_parallel: bool = True,
) -> Callable[[Callable], AsyncJobFunction] | AsyncJobFunction:
    """Decorator for marking async functions as enqueueable jobs.

    Args:
        func: The async function to decorate (when used as @async_job without parentheses)
        queue: Queue name (default: 'default')
        priority: Job priority (default: 0)
        timeout: Job timeout in seconds (default: None)
        max_retries: Maximum retry attempts (default: 0)
        retry_backoff: Backoff multiplier for retries (default: 1.0)
        allow_parallel: Allow parallel execution (default: True)

    Returns:
        Decorated async function with .delay() method

    Example:
        import asyncio
        from sqlery import async_job
        # from sqlery.backends import BackendFactory  # REMOVED in v0.13
        from sqlery.compat import get_backend

        async def main():
            backend = get_backend()

            # Define job (both syntaxes supported)
            @async_job
            async def simple_async_task():
                pass

            @async_job(queue='emails', timeout=300)
            async def send_email_async(to, subject, body):
                # Async email logic
                pass

            # Enqueue job
            job = await send_email_async.delay('user@example.com', 'Hello', 'World')
            print(f"Job {job['id']} enqueued")

            # Or call directly
            await send_email_async('user@example.com', 'Hello', 'World')

        asyncio.run(main())
    """
    # timeout_seconds is an alias for timeout
    effective_timeout = timeout_seconds if timeout_seconds is not None else timeout

    def decorator(f: Callable) -> AsyncJobFunction:
        return AsyncJobFunction(
            func=f,
            queue=queue,
            priority=priority,
            timeout=effective_timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
        )

    if func is not None:
        # Called as @async_job without parentheses
        return decorator(func)
    else:
        # Called as @async_job() with parentheses
        return decorator
