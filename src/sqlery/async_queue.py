"""Asynchronous Queue implementation for sqlery.

Provides high-level async API for job enqueueing and management.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable
from datetime import datetime, timedelta, UTC
# REMOVED in v0.13: backends abstraction layer was removed
# from .backends.base import AsyncStorageBackend
AsyncStorageBackend = None

logger = logging.getLogger(__name__)


class AsyncQueue:
    """Asynchronous queue for job management.

    Provides a high-level async API for enqueueing jobs, scheduling tasks,
    and managing job lifecycle.
    """

    _default_backend: AsyncStorageBackend | None = None

    def __init__(
        self,
        name: str = 'default',
        backend: AsyncStorageBackend | None = None,
        default_timeout: int | None = None,
    ):
        """Initialize async queue.

        Args:
            name: Queue name (default: 'default')
            backend: Storage backend to use (uses default if not provided)
            default_timeout: Default timeout for jobs in seconds
        """
        self.name = name
        # ponytail: the async backend is only for workers; enqueue writes through the sync
        # backend (compat.get_backend), so no async backend is needed to enqueue (#181).
        self.backend = backend or self._default_backend
        self.default_timeout = default_timeout

    @classmethod
    def configure(cls, backend: AsyncStorageBackend) -> None:
        """Configure default backend for all AsyncQueue instances.

        Args:
            backend: Storage backend to use as default
        """
        cls._default_backend = backend
        logger.info("Configured default backend for AsyncQueue")

    async def enqueue(
        self,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a function for execution (async).

        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function (including queue options)

        Keyword Args (Queue Options):
            queue: Queue name (default: self.name)
            priority: Job priority (default: 0)
            timeout: Job timeout in seconds (default: self.default_timeout)
            max_retries: Maximum retry attempts (default: 0)
            retry_backoff: Backoff multiplier for retries (default: 1.0)
            allow_parallel: Allow parallel execution (default: True)
            scheduled_at: Schedule for future execution (default: None)

        Returns:
            Job dict with id, status, etc.

        Example:
            queue = AsyncQueue(name='emails')
            job = await queue.enqueue(send_email, 'user@example.com', subject='Hello')
        """
        # Extract queue options from kwargs
        queue_options = {
            'queue': kwargs.pop('queue', self.name),
            'priority': kwargs.pop('priority', 0),
            'timeout': kwargs.pop('timeout', self.default_timeout),
            'max_retries': kwargs.pop('max_retries', 0),
            'retry_backoff': kwargs.pop('retry_backoff', 1.0),
            'allow_parallel': kwargs.pop('allow_parallel', True),
            'scheduled_at': kwargs.pop('scheduled_at', None),
        }

        # Get function path
        task_path = self._get_task_path(func)

        # Combine args and kwargs
        task_kwargs = self._serialize_args(args, kwargs)

        # Create job
        job = await self._create_job(
            task_path=task_path,
            kwargs=task_kwargs,
            queue_name=queue_options['queue'],
            priority=queue_options['priority'],
            scheduled_at=queue_options['scheduled_at'],
            max_retries=queue_options['max_retries'],
            retry_backoff=queue_options['retry_backoff'],
            allow_parallel=queue_options['allow_parallel'],
            timeout_seconds=queue_options['timeout'],
        )

        logger.info(
            f"Enqueued job {job.id} for task {task_path} on queue {queue_options['queue']}"
        )
        return job

    @staticmethod
    async def _create_job(**fields: Any) -> Any:
        """Create the job row via the sync backend, off the event loop (#181).

        The async backends (AsyncDatabaseBackend) have no ``create_job``; the sync
        backend for the active mode (Django or standalone) does.
        """
        return await AsyncQueue._call_sync_backend("create_job", **fields)

    @staticmethod
    async def _call_sync_backend(method: str, *args: Any, **kwargs: Any) -> Any:
        from .compat import get_backend, is_django_mode

        call = getattr(get_backend(), method)
        if is_django_mode():
            from asgiref.sync import sync_to_async

            return await sync_to_async(call)(*args, **kwargs)
        return await asyncio.to_thread(call, *args, **kwargs)

    async def enqueue_at(
        self,
        scheduled_at: datetime,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a function to run at specific time (async).

        Args:
            scheduled_at: When to run the job
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Job dict

        Example:
            from datetime import datetime, timedelta
            run_at = datetime.now() + timedelta(hours=1)
            job = await queue.enqueue_at(run_at, send_report, 'admin@example.com')
        """
        kwargs['scheduled_at'] = scheduled_at
        return await self.enqueue(func, *args, **kwargs)

    async def enqueue_in(
        self,
        delay: timedelta,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a function to run after delay (async).

        Args:
            delay: How long to wait before running
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Job dict

        Example:
            from datetime import timedelta
            job = await queue.enqueue_in(timedelta(minutes=5), process_data, data_id=123)
        """
        scheduled_at = datetime.now(UTC) + delay
        return await self.enqueue_at(scheduled_at, func, *args, **kwargs)

    async def schedule(
        self,
        cron: str,
        func: Callable,
        name: str | None = None,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Schedule a recurring task using cron syntax (async).

        Args:
            cron: Cron expression (e.g., '0 2 * * *' for daily at 2 AM)
            func: Function to execute
            name: Task name (default: function name)
            **kwargs: Additional queue options

        Returns:
            Scheduled task dict

        Example:
            await queue.schedule(
                cron='0 2 * * *',
                func=cleanup_old_data,
                name='daily-cleanup'
            )
        """
        task_name = name or func.__name__
        task_path = self._get_task_path(func)
        queue_name = kwargs.get('queue', self.name)
        priority = kwargs.get('priority', 0)

        task = await self.backend.create_scheduled_task(
            name=task_name,
            task_path=task_path,
            cron_expression=cron,
            queue_name=queue_name,
            priority=priority,
            enabled=True,
        )

        return task

    async def get_job(self, job_id: int) -> Any:
        """Return the stored job, or None if the ID does not exist."""
        if self.backend is None:
            return await self._call_sync_backend("get_job_by_id", job_id)

        read_job = getattr(self.backend, "aget_job", None)
        if read_job is not None:
            return await read_job(job_id)
        return await self.backend.get_job_by_id(job_id)

    async def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued job (async).

        Args:
            job_id: Job ID

        Returns:
            True if cancelled, False otherwise
        """
        result = await self.backend.cancel_job(job_id)
        if result:
            logger.info(f"Cancelled job {job_id}")
        else:
            logger.warning(f"Failed to cancel job {job_id} (not found or already processed)")
        return result

    async def count(self, status: str | None = None) -> int:
        """Count jobs in queue (async).

        Args:
            status: Filter by status (optional)

        Returns:
            Number of jobs
        """
        return await self.backend.count_jobs(status=status, queue_name=self.name)

    async def get_stats(self) -> dict[str, int]:
        """Get queue statistics (async).

        Returns:
            Dict with counts by status
        """
        return await self.backend.get_queue_stats(queue_name=self.name)

    async def is_empty(self) -> bool:
        """Check if queue is empty (async).

        Returns:
            True if no queued jobs
        """
        count = await self.count(status='queued')
        return count == 0

    async def get_jobs(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[dict[str, Any]]:
        """Get jobs from queue (async).

        Args:
            status: Filter by status (optional)
            limit: Max number of jobs to return
            offset: Number of jobs to skip

        Returns:
            List of job dicts
        """
        return await self.backend.get_jobs(
            status=status,
            queue_name=self.name,
            limit=limit,
            offset=offset
        )

    @staticmethod
    def _get_task_path(func: Callable) -> str:
        """Get importable path for function.

        Args:
            func: Function to get path for

        Returns:
            Import path (e.g., 'myapp.tasks.send_email')
        """
        module = inspect.getmodule(func)
        if module is None:
            # Function defined in __main__ or interactive session
            return f"__main__.{func.__name__}"

        module_name = module.__name__
        func_name = func.__name__

        return f"{module_name}.{func_name}"

    @staticmethod
    def _serialize_args(args: tuple, kwargs: dict) -> dict[str, Any]:
        """Serialize positional and keyword arguments.

        Args:
            args: Positional arguments tuple
            kwargs: Keyword arguments dict

        Returns:
            Dict with '_args' and other keys
        """
        result = dict(kwargs)
        if args:
            result['_args'] = args
        return result
