"""Asynchronous Worker implementation for sqlery.

Provides async job processing and execution logic.
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import importlib
import json
import logging
import time
import traceback as tb_module
from typing import Any, Callable
from datetime import datetime
# REMOVED in v0.13: backends abstraction layer was removed
# from .backends.base import AsyncStorageBackend
AsyncStorageBackend = None
from .async_queue import AsyncQueue
from .decorators import JobFunction, AsyncJobFunction

logger = logging.getLogger(__name__)


class AsyncWorker:
    """Asynchronous worker for processing jobs.

    Claims and executes jobs from one or more queues.
    """

    def __init__(
        self,
        queues: list[AsyncQueue] | list[str],
        backend: AsyncStorageBackend | None = None,
        worker_id: str | None = None,
        burst: bool = False,
        poll_interval: float = 1.0,
    ):
        """Initialize async worker.

        Args:
            queues: List of AsyncQueue instances or queue names
            backend: Storage backend (required if queues are strings)
            worker_id: Worker identifier (default: generated)
            burst: Process available jobs then exit (default: False)
            poll_interval: Time to wait between polling (default: 1.0 seconds)
        """
        self.queues = self._normalize_queues(queues, backend)
        self.backend = self._get_backend(backend)
        self.worker_id = worker_id or self._generate_worker_id()
        self.burst = burst
        self.poll_interval = poll_interval
        self._should_stop = False
        self._setup_signal_handlers()

    async def work(self) -> None:
        """Start processing jobs (async).

        Continues until stopped via signal or burst mode completes.
        """
        logger.info(f"Worker {self.worker_id} starting")

        while not self._should_stop:
            # Update heartbeat
            await self._update_heartbeat('busy')

            # Try to claim a job
            job = await self._claim_job()

            if job:
                logger.info(f"Worker {self.worker_id} processing job {job['id']}")
                await self._process_job(job)
            else:
                # No jobs available
                if self.burst:
                    logger.info(f"Worker {self.worker_id} burst mode complete, exiting")
                    break

                # Wait before polling again
                await self._update_heartbeat('idle')
                await asyncio.sleep(self.poll_interval)

        logger.info(f"Worker {self.worker_id} stopped")

    def stop(self) -> None:
        """Stop the worker gracefully."""
        logger.info(f"Worker {self.worker_id} stopping")
        self._should_stop = True

    async def _claim_job(self) -> dict[str, Any] | None:
        """Claim next available job from queues (async).

        Returns:
            Job dict or None if no jobs available
        """
        queue_names = [q.name for q in self.queues]
        return await self.backend.claim_job(queue_names, self.worker_id)

    async def _process_job(self, job: dict[str, Any]) -> None:
        """Process a single job (async).

        Args:
            job: Job dict from backend
        """
        job_id = job['id']

        try:
            # Load the task function
            func = self._load_task(job['task_path'])

            # Deserialize arguments
            args, kwargs = self._deserialize_args(job['kwargs'])

            # Execute the task
            logger.debug(f"Executing {job['task_path']} with args={args}, kwargs={kwargs}")

            # Check if function is async
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                # Run sync function in executor to avoid blocking
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))

            # Mark as successful
            output = str(result) if result is not None else ""
            await self.backend.mark_job_success(job_id, output=output)
            logger.info(f"Job {job_id} completed successfully")

        except Exception as e:
            # Mark as failed
            error = str(e)
            traceback = tb_module.format_exc()
            await self.backend.mark_job_failed(job_id, error=error, traceback=traceback)
            logger.error(f"Job {job_id} failed: {error}", exc_info=True)

    def _load_task(self, task_path: str) -> Callable:
        """Load task function from import path.

        Args:
            task_path: Import path (e.g., 'myapp.tasks.send_email')

        Returns:
            Callable function

        Raises:
            ImportError: If module or function cannot be loaded
        """
        # Split into module and function name
        parts = task_path.rsplit('.', 1)
        if len(parts) != 2:
            raise ImportError(f"Invalid task path: {task_path}")

        module_name, func_name = parts

        # Import module
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Cannot import module {module_name}: {e}")

        # Get function
        try:
            func = getattr(module, func_name)
        except AttributeError:
            raise ImportError(f"Module {module_name} has no attribute {func_name}")

        # Unwrap decorated functions
        # If function is decorated with @job or @async_job, extract the original function
        # from .decorators import JobFunction, AsyncJobFunction  # moved to top-level
        if isinstance(func, (JobFunction, AsyncJobFunction)):
            func = func.func

        return func

    def _deserialize_args(self, kwargs_json: str) -> tuple[tuple, dict]:
        """Deserialize job arguments.

        Args:
            kwargs_json: JSON string of kwargs dict

        Returns:
            Tuple of (args, kwargs)
        """
        kwargs = json.loads(kwargs_json)

        # Extract positional args if present
        args = kwargs.pop('_args', ())

        return args, kwargs

    async def _update_heartbeat(self, status: str) -> None:
        """Update worker heartbeat (async).

        Args:
            status: Worker status ('idle' or 'busy')
        """
        try:
            await self.backend.update_worker_heartbeat(
                worker_id=self.worker_id,
                status=status,
                current_job_id=None
            )
        except Exception as e:
            # print(f"Warning: Failed to update heartbeat: {e}")
            logger.warning(f"Failed to update heartbeat: {e}")

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            # print(f"\nReceived signal {signum}, stopping worker...")
            logger.info(f"Received signal {signum}, stopping worker...")
            self.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _generate_worker_id(self) -> str:
        """Generate unique worker ID.

        Returns:
            Worker ID string
        """
        # import socket  # moved to top-level
        # import os  # moved to top-level
        # import time  # moved to top-level
        hostname = socket.gethostname()
        pid = os.getpid()
        timestamp = int(time.time())
        return f"worker-{hostname}-{pid}-{timestamp}"

    def _normalize_queues(
        self,
        queues: list[AsyncQueue] | list[str],
        backend: AsyncStorageBackend | None
    ) -> list[AsyncQueue]:
        """Normalize queue input to list of AsyncQueue instances.

        Args:
            queues: List of AsyncQueue instances or queue names
            backend: Backend to use for string queues

        Returns:
            List of AsyncQueue instances

        Raises:
            ValueError: If queues are strings but no backend provided
        """
        if not queues:
            raise ValueError("At least one queue is required")

        # If first element is an AsyncQueue instance, return as-is
        if isinstance(queues[0], AsyncQueue):
            return queues

        # If strings, create AsyncQueue instances
        if backend is None:
            raise ValueError("backend is required when queues are specified as strings")

        return [AsyncQueue(name=name, backend=backend) for name in queues]

    def _get_backend(self, backend: AsyncStorageBackend | None) -> AsyncStorageBackend:
        """Get backend from queues or parameter.

        Args:
            backend: Explicit backend (optional)

        Returns:
            Storage backend

        Raises:
            RuntimeError: If no backend available
        """
        if backend:
            return backend

        if self.queues:
            return self.queues[0].backend

        raise RuntimeError("No backend available")
