"""Synchronous Queue implementation for sqlery.

Provides high-level API for job enqueueing and management.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, UTC
from typing import Any, Callable

from django.db.models import Max

from .models import QueuedJob
from .settings import get_setting

# REMOVED in v0.13: backends abstraction layer was removed
# from sqlery.backends.base import SyncStorageBackend
SyncStorageBackend = None

logger = logging.getLogger(__name__)


class Queue:
    """Synchronous queue for job management.

    Provides a high-level API for enqueueing jobs, scheduling tasks,
    and managing job lifecycle.
    """

    _default_backend: SyncStorageBackend | None = None

    def __init__(
        self,
        name: str = 'default',
        backend: SyncStorageBackend | None = None,
        default_timeout: int | None = None,
    ):
        """Initialize queue.

        Args:
            name: Queue name (default: 'default')
            backend: Storage backend to use (uses default if not provided)
            default_timeout: Default timeout for jobs in seconds
        """
        self.name = name
        self.backend = backend or self._get_default_backend()
        if default_timeout is not None:
            self.default_timeout = default_timeout
        else:
            # from .settings import get_setting  # moved to top-level
            self.default_timeout = get_setting('DEFAULT_TIMEOUT_SECONDS')

    @classmethod
    def configure(cls, backend: SyncStorageBackend) -> None:
        """Configure default backend for all Queue instances.

        Args:
            backend: Storage backend to use as default
        """
        cls._default_backend = backend
        logger.info("Configured default backend for Queue")

    @classmethod
    def _get_default_backend(cls) -> SyncStorageBackend:
        """Get default backend or raise error."""
        if cls._default_backend is None:
            raise RuntimeError(
                "No backend configured. Either pass backend to Queue() "
                "or call Queue.configure(backend) first."
            )
        return cls._default_backend

    def enqueue(
        self,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a function for execution.

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
            queue = Queue(name='emails')
            job = queue.enqueue(send_email, 'user@example.com', subject='Hello')
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
            'job_name': kwargs.pop('job_name', None),
            'retry_intervals': kwargs.pop('retry_intervals', None),
            'result_ttl': kwargs.pop('result_ttl', None),
            'job_id': kwargs.pop('job_id', None),
            # RQ compat: positional args passed as kwarg, retry policy, job_timeout
            '_rq_args': kwargs.pop('args', None),
            '_rq_retry': kwargs.pop('retry', None),
            '_rq_job_timeout': kwargs.pop('job_timeout', None),
            '_rq_kwargs': kwargs.pop('kwargs', None),
            # RQ compat: at_front bumps priority to ensure job runs next
            '_rq_at_front': kwargs.pop('at_front', False),
            # RQ compat: consume options that have no sqlery equivalent
            '_rq_meta': kwargs.pop('meta', None),
            '_rq_depends_on': kwargs.pop('depends_on', None),
            '_rq_on_success': kwargs.pop('on_success', None),
            '_rq_on_failure': kwargs.pop('on_failure', None),
            '_rq_on_stopped': kwargs.pop('on_stopped', None),
            '_rq_description': kwargs.pop('description', None),
            'ttl': kwargs.pop('ttl', None),
            'failure_ttl': kwargs.pop('failure_ttl', None),
        }

        # Get function path
        task_path = self._get_task_path(func)

        # RQ compat: if args were passed as kwargs['args'], merge into positional args
        rq_args = queue_options.get('_rq_args')
        if rq_args:
            args = tuple(rq_args) + tuple(args)

        # RQ compat: if kwargs were passed as kwargs['kwargs'], merge into task kwargs
        rq_kwargs = queue_options.get('_rq_kwargs')
        if rq_kwargs and isinstance(rq_kwargs, dict):
            kwargs.update(rq_kwargs)

        # RQ compat: use job_timeout as fallback for timeout
        timeout = queue_options['timeout']
        if timeout is None and queue_options.get('_rq_job_timeout'):
            timeout = queue_options['_rq_job_timeout']

        # Parse RQ-style timeout strings ('10m', '1h', '30s', '300') into seconds
        timeout = self._parse_timeout(timeout)

        # RQ compat: at_front=True sets priority to 1 above the current max in the queue
        priority = queue_options['priority']
        if queue_options.get('_rq_at_front'):
            # from .models import QueuedJob  # moved to top-level
            # from django.db.models import Max  # moved to top-level
            max_priority = (
                QueuedJob.objects
                .filter(queue_name=queue_options['queue'], status='queued')
                .aggregate(max_p=Max('priority'))['max_p']
            )
            priority = (max_priority or 0) + 1

        # RQ compat: job_id maps to job_name (string identifier)
        job_name = queue_options['job_name']
        if not job_name and queue_options.get('job_id'):
            job_name = str(queue_options['job_id'])

        # RQ compat: description falls back to job_name
        if not job_name and queue_options.get('_rq_description'):
            job_name = queue_options['_rq_description']

        # RQ compat: meta dict passed through to job
        meta = queue_options.get('_rq_meta')

        # RQ compat: depends_on can be a single job/id or list of jobs/ids
        dependencies = self._normalize_depends_on(queue_options.get('_rq_depends_on'))

        # RQ compat: retry object → max_retries + retry_intervals
        max_retries = queue_options['max_retries']
        retry_intervals = queue_options['retry_intervals']
        rq_retry = queue_options.get('_rq_retry')
        if rq_retry and max_retries == 0:
            max_retries = getattr(rq_retry, 'max', 0) or 0
            intervals = getattr(rq_retry, 'interval', None)
            if intervals is not None and retry_intervals is None:
                retry_intervals = intervals if isinstance(intervals, list) else [intervals]

        # RQ compat: resolve callback callables to import paths
        on_success_path = ''
        on_failure_path = ''
        rq_on_success = queue_options.get('_rq_on_success')
        if rq_on_success:
            on_success_path = self._get_task_path(rq_on_success)
        rq_on_failure = queue_options.get('_rq_on_failure')
        if rq_on_failure:
            on_failure_path = self._get_task_path(rq_on_failure)
        # on_stopped maps to on_failure in sqlery (stopped = failed)
        rq_on_stopped = queue_options.get('_rq_on_stopped')
        if rq_on_stopped and not on_failure_path:
            on_failure_path = self._get_task_path(rq_on_stopped)

        # TTL values
        ttl = queue_options.get('ttl')
        if isinstance(ttl, str):
            ttl = self._parse_timeout(ttl)
        failure_ttl = queue_options.get('failure_ttl')
        if isinstance(failure_ttl, str):
            failure_ttl = self._parse_timeout(failure_ttl)

        # Combine args and kwargs
        task_kwargs = self._serialize_args(args, kwargs)

        # Create job
        job = self.backend.create_job(
            task_path=task_path,
            kwargs=task_kwargs,
            queue_name=queue_options['queue'],
            priority=priority,
            scheduled_at=queue_options['scheduled_at'],
            max_retries=max_retries,
            retry_backoff=queue_options['retry_backoff'],
            allow_parallel=queue_options['allow_parallel'],
            timeout_seconds=timeout,
            job_name=job_name,
            retry_intervals=retry_intervals,
            meta=meta,
            dependencies=dependencies,
            on_success_path=on_success_path,
            on_failure_path=on_failure_path,
            ttl=ttl,
            result_ttl=queue_options.get('result_ttl'),
            failure_ttl=failure_ttl,
        )

        job_id = job.id if hasattr(job, 'id') else job.get('id', '?')
        logger.info(
            f"Enqueued job {job_id} for task {task_path} on queue {queue_options['queue']}"
        )
        return job

    def enqueue_at(
        self,
        scheduled_at: datetime,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a function to run at specific time.

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
            job = queue.enqueue_at(run_at, send_report, 'admin@example.com')
        """
        kwargs['scheduled_at'] = scheduled_at
        return self.enqueue(func, *args, **kwargs)

    def enqueue_in(
        self,
        delay: timedelta,
        func: Callable,
        *args: Any,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a function to run after delay.

        Args:
            delay: How long to wait before running
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Job dict

        Example:
            from datetime import timedelta
            job = queue.enqueue_in(timedelta(minutes=5), process_data, data_id=123)
        """
        scheduled_at = datetime.now(UTC) + delay
        return self.enqueue_at(scheduled_at, func, *args, **kwargs)

    def schedule(
        self,
        cron: str,
        func: Callable,
        name: str | None = None,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Schedule a recurring task using cron syntax.

        Args:
            cron: Cron expression (e.g., '0 2 * * *' for daily at 2 AM)
            func: Function to execute
            name: Task name (default: function name)
            **kwargs: Additional queue options

        Returns:
            Scheduled task dict

        Example:
            queue.schedule(
                cron='0 2 * * *',
                func=cleanup_old_data,
                name='daily-cleanup'
            )
        """
        task_name = name or func.__name__
        task_path = self._get_task_path(func)
        queue_name = kwargs.get('queue', self.name)
        priority = kwargs.get('priority', 0)

        task = self.backend.create_scheduled_task(
            name=task_name,
            task_path=task_path,
            cron_expression=cron,
            queue_name=queue_name,
            priority=priority,
            enabled=True,
        )

        return task

    def fetch_job(self, job_id: str | int) -> Any | None:
        """Fetch a job by its string ID (RQ compat).

        In RQ, job IDs are strings passed at enqueue time. In sqlery,
        these are stored as job_name. Falls back to integer PK lookup.

        Args:
            job_id: String job name or integer PK

        Returns:
            QueuedJob instance or None if not found
        """
        # from .models import QueuedJob  # moved to top-level

        # Try job_name first (RQ string ID)
        if isinstance(job_id, str):
            job = QueuedJob.objects.filter(job_name=job_id).first()
            if job:
                return job

        # Fall back to integer PK
        try:
            return QueuedJob.objects.get(id=int(job_id))
        except (QueuedJob.DoesNotExist, ValueError, TypeError):
            return None

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        """Get job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job dict or None if not found
        """
        return self.backend.get_job_by_id(job_id)

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued job.

        Args:
            job_id: Job ID

        Returns:
            True if cancelled, False otherwise
        """
        result = self.backend.cancel_job(job_id)
        if result:
            logger.info(f"Cancelled job {job_id}")
        else:
            logger.warning(f"Failed to cancel job {job_id} (not found or already processed)")
        return result

    def count(self, status: str | None = None) -> int:
        """Count jobs in queue.

        Args:
            status: Filter by status (optional)

        Returns:
            Number of jobs
        """
        return self.backend.count_jobs(status=status, queue_name=self.name)

    def get_stats(self) -> dict[str, int]:
        """Get queue statistics.

        Returns:
            Dict with counts by status
        """
        return self.backend.get_queue_stats(queue_name=self.name)

    def is_empty(self) -> bool:
        """Check if queue is empty.

        Returns:
            True if no queued jobs
        """
        return self.count(status='queued') == 0

    def get_jobs(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[dict[str, Any]]:
        """Get jobs from queue.

        Args:
            status: Filter by status (optional)
            limit: Max number of jobs to return
            offset: Number of jobs to skip

        Returns:
            List of job dicts
        """
        return self.backend.get_jobs(
            status=status,
            queue_name=self.name,
            limit=limit,
            offset=offset
        )

    @staticmethod
    def _get_task_path(func: Callable | str) -> str:
        """Get importable path for function.

        Args:
            func: Function or dotted import path string

        Returns:
            Import path (e.g., 'myapp.tasks.send_email')
        """
        if isinstance(func, str):
            return func

        module = inspect.getmodule(func)
        if module is None:
            # Function defined in __main__ or interactive session
            return f"__main__.{func.__name__}"

        module_name = module.__name__
        func_name = func.__name__

        return f"{module_name}.{func_name}"

    @staticmethod
    def _parse_timeout(value) -> int | None:
        """Parse timeout value into seconds.

        Accepts int, None, or RQ-style strings like '10m', '1h', '30s', '300'.
        """
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if value.endswith('m'):
                return int(value[:-1]) * 60
            elif value.endswith('h'):
                return int(value[:-1]) * 3600
            elif value.endswith('s'):
                return int(value[:-1])
            else:
                return int(value)
        return None

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

    @staticmethod
    def _normalize_depends_on(depends_on) -> list | None:
        """Normalize RQ depends_on into a list of job IDs.

        RQ accepts a single Job/id or a list of Jobs/ids.
        """
        if depends_on is None:
            return None
        if not isinstance(depends_on, (list, tuple)):
            depends_on = [depends_on]
        result = []
        for dep in depends_on:
            if isinstance(dep, (int, str)):
                result.append(dep)
            elif hasattr(dep, 'id'):
                result.append(dep.id)
        return result or None
