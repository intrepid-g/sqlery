"""AsyncWorker — coroutine-based job worker (ASYN-04 + ASYN-05).

Rewrite of the legacy ``sqlery.async_worker.AsyncWorker`` (broken since v0.13
when ``AsyncStorageBackend`` was removed). Built directly on the new
:class:`sqlery.compat.AsyncDatabaseBackend` ABC, mirroring the sync sibling
``sqlery.core.worker``.

Key design points:
- Async-defined jobs run as ``asyncio.create_task(coro)``; sync-defined jobs
  are off-loaded via ``loop.run_in_executor(None, fn, *args, **kwargs)``.
- Signal handling uses ``loop.add_signal_handler`` — NEVER ````signal.signal`` (the sync API)``
  (which races with the event loop, RESEARCH §9 pitfall).
- On SIGTERM / SIGINT polling stops, and for each in-flight job the worker
  FIRST writes the transient ``shutting_down`` row state via
  ``backend.amark_shutting_down(job_id)``, THEN races the job task against an
  ``asyncio.sleep(deadline)`` timer (drain-with-deadline, decision C).
- If the deadline wins, the job task is cancelled and marked ``failed`` with
  the canonical error string :data:`SHUTDOWN_TIMEOUT_ERROR`; the standard
  failed-with-retries path requeues a fresh row when
  ``max_retries > 0``.
- The ``shutting_down`` state is write-once at the terminal boundary: once a
  ``finished`` / ``failed`` write lands, the row never reverts.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import socket
import time
import traceback as tb_module
from typing import Any, Awaitable, Callable

from sqlery.compat import AsyncDatabaseBackend

logger = logging.getLogger(__name__)


SHUTDOWN_TIMEOUT_ERROR = "shutdown_timeout: worker terminated before job finished"
_DEFAULT_SHUTDOWN_DEADLINE_ENV = "SQLERY_ASYNC_SHUTDOWN_DEADLINE_SECONDS"
_DEFAULT_SHUTDOWN_DEADLINE_SECONDS = 60


def _generate_worker_id():
    """Generate a worker id. Prefers uuid7 (time-sortable, matches sync path).

    Returns a UUID instance so it round-trips through SQLAlchemy's native UUID
    column on the standalone ``Worker.id`` field.
    """
    try:
        from uuid6 import uuid7  # type: ignore

        return uuid7()
    except Exception:  # pragma: no cover - uuid6 is a declared dep
        import uuid

        return uuid.uuid4()


def _load_task(task_path: str) -> Callable[..., Any]:
    """Import a dotted ``module.attr`` task path and return the callable.

    Delegates to :func:`sqlery.core.utils.import_task` which enforces the
    SEC-04 ``ALLOWED_TASK_MODULES`` allowlist before importing.
    """
    from .utils import import_task
    func = import_task(task_path)
    # Unwrap @job / @async_job decorations if present.
    inner = getattr(func, "func", None)
    if inner is not None and callable(inner):
        return inner
    return func


def _deserialize_kwargs(kwargs: Any) -> tuple[tuple, dict]:
    """Split persisted kwargs blob into (positional_args, kwargs) tuple.

    The persisted dict may carry an ``_args`` key holding a positional tuple
    (legacy convention).
    """
    if kwargs is None:
        return (), {}
    if isinstance(kwargs, dict):
        d = dict(kwargs)
        args = tuple(d.pop("_args", ()) or ())
        return args, d
    # Fallback: treat anything else as positional-only.
    return (kwargs,), {}


class AsyncWorker:
    """Coroutine-based worker (one OS process, many in-flight asyncio jobs).

    Args:
        backend: An :class:`AsyncDatabaseBackend` implementation.
        queues: List of queue names to poll (e.g. ``["default"]``).
        worker_id: Optional explicit worker id; auto-generated via uuid7 by
            default.
        poll_interval: Sleep seconds between empty polls. Default 1.0.
        shutdown_deadline_seconds: Grace period for in-flight jobs on
            SIGTERM/SIGINT. Default reads ``$SQLERY_ASYNC_SHUTDOWN_DEADLINE_SECONDS``
            or 60.
    """

    def __init__(
        self,
        backend: AsyncDatabaseBackend,
        queues: list[str] | None = None,
        worker_id: str | None = None,
        poll_interval: float = 1.0,
        shutdown_deadline_seconds: int | float | None = None,
    ) -> None:
        if backend is None:
            raise ValueError("backend is required")
        self.backend = backend
        self.queues: list[str] = list(queues or ["default"])
        self.worker_id = worker_id or _generate_worker_id()
        self.poll_interval = float(poll_interval)
        if shutdown_deadline_seconds is None:
            env = os.environ.get(_DEFAULT_SHUTDOWN_DEADLINE_ENV)
            try:
                shutdown_deadline_seconds = (
                    float(env) if env is not None else _DEFAULT_SHUTDOWN_DEADLINE_SECONDS
                )
            except ValueError:
                shutdown_deadline_seconds = _DEFAULT_SHUTDOWN_DEADLINE_SECONDS
        self.shutdown_deadline_seconds = shutdown_deadline_seconds

        self._shutting_down: bool = False
        self._shutdown_event: asyncio.Event | None = None
        # Map of job_id -> asyncio.Task for in-flight jobs (currently single-job).
        self._inflight: dict[Any, asyncio.Task] = {}

    # ------------------------------------------------------------------ run

    async def run(
        self,
        *,
        max_jobs: int | None = None,
        max_polls: int | None = None,
    ) -> None:
        """Poll the configured queues and execute claimed jobs.

        Test/bounded-run controls (both default to ``None`` = unbounded):

        - ``max_jobs``: stop after executing this many jobs.
        - ``max_polls``: stop after this many poll iterations (claimed or
          empty).
        """
        self._shutdown_event = asyncio.Event()
        self._install_signal_handlers()
        await self._register()
        jobs_done = 0
        polls = 0
        try:
            while not self._shutting_down:
                if max_polls is not None and polls >= max_polls:
                    break
                if max_jobs is not None and jobs_done >= max_jobs:
                    break
                polls += 1
                try:
                    await self.backend.aupdate_heartbeat(self.worker_id)
                except Exception as e:  # pragma: no cover - heartbeat is best-effort
                    logger.warning(f"Heartbeat update failed: {e}")

                try:
                    job = await self.backend.aclaim_job(self.queues, self.worker_id)
                except Exception as e:
                    logger.exception(f"aclaim_job failed: {e}")
                    await asyncio.sleep(self.poll_interval)
                    continue

                if job is None:
                    await asyncio.sleep(self.poll_interval)
                    continue

                await self._execute_job(job)
                jobs_done += 1
        finally:
            # On shutdown, drain in-flight jobs with deadline (Task 2 logic).
            if self._shutting_down and self._inflight:
                await self._drain_with_deadline()
            try:
                await self.backend.aunregister_worker(self.worker_id)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    # ----------------------------------------------------------- execution

    async def _execute_job(self, job: Any) -> None:
        """Dispatch a single claimed job. Cooperates with shutdown drain."""
        job_id = job.id
        try:
            func = _load_task(job.task_path)
            args, kwargs = _deserialize_kwargs(getattr(job, "kwargs", {}))
        except Exception as e:
            tb = tb_module.format_exc()
            await self.backend.amark_failed(job_id, error=str(e), traceback=tb)
            return

        loop = asyncio.get_running_loop()
        if asyncio.iscoroutinefunction(func):
            task = asyncio.create_task(func(*args, **kwargs))
        else:
            # Sync function: offload to default executor (thread pool).
            def _runner() -> Any:
                return func(*args, **kwargs)

            task = asyncio.ensure_future(loop.run_in_executor(None, _runner))

        self._inflight[job_id] = task

        # Race the job task against the shutdown event. If shutdown fires
        # mid-flight, return immediately — drain_with_deadline takes over
        # writing the terminal status.
        shutdown_event = self._shutdown_event
        if shutdown_event is None:
            self._inflight.pop(job_id, None)
            return
        if self._shutting_down:
            return

        shutdown_wait = asyncio.create_task(shutdown_event.wait())
        try:
            done, _ = await asyncio.wait(
                {task, shutdown_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not shutdown_wait.done():
                shutdown_wait.cancel()

        if task in done:
            # Job-wins normal path: write terminal status here.
            try:
                result = task.result()
            except Exception as e:
                tb = tb_module.format_exc()
                await self._on_job_failed(job, error=str(e), traceback=tb)
            else:
                await self._on_job_success(job, result)
            self._inflight.pop(job_id, None)
        else:
            # Shutdown fired; leave task in self._inflight for drain handler.
            return

    async def _on_job_success(self, job: Any, result: Any) -> None:
        await self.backend.amark_success(job.id, result)

    async def _on_job_failed(
        self, job: Any, *, error: str, traceback: str | None = None
    ) -> None:
        await self.backend.amark_failed(job.id, error=error, traceback=traceback)
        if self._should_retry(job):
            try:
                await self._requeue_for_retry(job)
            except Exception as e:  # pragma: no cover - logged
                logger.exception(f"Retry-requeue failed for job {job.id}: {e}")

    # --------------------------------------------------------- retry path

    def _should_retry(self, job: Any) -> bool:
        max_retries = getattr(job, "max_retries", 0) or 0
        retry_count = getattr(job, "retry_count", 0) or 0
        return max_retries > 0 and retry_count < max_retries

    async def _requeue_for_retry(self, failed_job: Any) -> None:
        """Insert a fresh ``queued`` row carrying the retry chain.

        Delegates to ``AsyncDatabaseBackend.arequeue_retry``, which every
        concrete async backend (Django, SQLAlchemy) must implement -- see
        ``sqlery.compat.AsyncDatabaseBackend``.
        """
        await self.backend.arequeue_retry(failed_job)

    # ------------------------------------------------------------ shutdown

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM/SIGINT handlers on the running loop.

        Uses ``loop.add_signal_handler`` — NEVER ````signal.signal`` (the sync API)`` (which
        races with the event loop, RESEARCH §9).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._initiate_shutdown)
            except (NotImplementedError, ValueError):  # pragma: no cover
                # add_signal_handler is unsupported on some platforms (Windows,
                # threads); tests trigger _initiate_shutdown directly there.
                pass

    def _initiate_shutdown(self) -> None:
        """Flag the worker to stop polling and begin draining in-flight jobs."""
        if self._shutting_down:
            return
        logger.info(f"AsyncWorker {self.worker_id} entering shutdown")
        self._shutting_down = True
        if self._shutdown_event is not None:
            try:
                self._shutdown_event.set()
            except Exception:  # pragma: no cover
                pass

    async def _drain_with_deadline(self) -> None:
        """Race in-flight job tasks against the shutdown deadline.

        For each in-flight job:

        1. Write the transient ``shutting_down`` row state FIRST via
           ``amark_shutting_down`` (BEFORE the race begins).
        2. Race the job task against ``asyncio.sleep(deadline)``.
        3. If the job wins, mark it ``success`` / ``failed`` via the normal
           result-handling path (overwrites the transient state).
        4. If the deadline wins, cancel the job task and mark ``failed`` with
           :data:`SHUTDOWN_TIMEOUT_ERROR`. The retry-on-failure path then
           re-enqueues a fresh row when ``max_retries > 0``.
        """
        # Snapshot in-flight (we'll mutate the dict on completion).
        inflight = list(self._inflight.items())
        for job_id, task in inflight:
            await self._drain_one(job_id, task)

    async def _drain_one(self, job_id: Any, task: asyncio.Task) -> None:
        # Look up the job row so retry path has all the metadata.
        try:
            job = await self.backend.aget_job(job_id)
        except Exception:  # pragma: no cover
            job = None

        # Step 1: transient state FIRST (before the race).
        try:
            await self.backend.amark_shutting_down(job_id)
        except Exception as e:  # pragma: no cover
            logger.warning(f"amark_shutting_down({job_id}) failed: {e}")

        # Step 2: race.
        deadline_task = asyncio.create_task(asyncio.sleep(self.shutdown_deadline_seconds))
        done, _pending = await asyncio.wait(
            {task, deadline_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if task in done:
            deadline_task.cancel()
            # Step 3: terminal write overwrites transient ``shutting_down``.
            try:
                result = task.result()
            except Exception as e:
                tb = tb_module.format_exc()
                if job is not None:
                    await self._on_job_failed(job, error=str(e), traceback=tb)
                else:  # pragma: no cover
                    await self.backend.amark_failed(job_id, error=str(e), traceback=tb)
            else:
                await self.backend.amark_success(job_id, result)
        else:
            # Step 4: deadline wins → cancel + mark failed + retry path.
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            await self.backend.amark_failed(job_id, error=SHUTDOWN_TIMEOUT_ERROR)
            if job is not None and self._should_retry(job):
                try:
                    await self._requeue_for_retry(job)
                except Exception as e:  # pragma: no cover
                    logger.exception(f"Shutdown retry-requeue failed: {e}")

        self._inflight.pop(job_id, None)

    # ------------------------------------------------------------ registry

    async def _register(self) -> None:
        try:
            await self.backend.aregister_worker(
                self.worker_id,
                {
                    "node_id": socket.gethostname(),
                    "pid": os.getpid(),
                    "status": "idle",
                    "queues": list(self.queues),
                },
            )
        except Exception as e:  # pragma: no cover - tests pre-register
            logger.debug(f"aregister_worker failed (continuing): {e}")


__all__ = ["AsyncWorker", "SHUTDOWN_TIMEOUT_ERROR"]
