"""AsyncWorker — coroutine-based job worker (ASYN-04 + ASYN-05).

Rewrite of the legacy ``sqlery.async_worker.AsyncWorker`` (broken since v0.13
when ``AsyncStorageBackend`` was removed). Built directly on the new
:class:`sqlery.compat.AsyncDatabaseBackend` ABC, mirroring the sync sibling
``sqlery.core.worker``.

Key design points:
- Up to ``concurrency`` claimed jobs run at once, each as its own asyncio task.
- Every job body goes through :func:`sqlery.core.execution.ainvoke_task` (upstream results,
  ``bind=True`` context, task signals). Async-defined jobs are awaited; sync-defined jobs
  are off-loaded to a thread.
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
import logging
import os
import signal
import socket
import time
import traceback as tb_module
from typing import Any, Awaitable, Callable

from sqlery.compat import AsyncDatabaseBackend, JobFencingError

from .attempts import MISSING_TABLE_MESSAGE, AttemptTableMissingError, attempt_kwargs, attempt_of
from .history import MISSING_TABLE_MESSAGE as HISTORY_MISSING_TABLE_MESSAGE
from .history import HistoryTableMissingError
from .ready import MISSING_TABLE_MESSAGE as READY_MISSING_TABLE_MESSAGE
from .ready import ReadyTablesMissingError

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


class _SyncLivenessAdapter:
    """Sync view of an async backend's liveness methods, for the daemon's zombie sweep.

    The sweep runs in a helper thread; each call is marshalled back onto the worker's
    event loop so the async backend's sessions stay on the loop that created them.
    """

    def __init__(self, backend: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._backend = backend
        self._loop = loop

    def _call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def get_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        return self._call(self._backend.aget_running_jobs_for_liveness(queue_names))

    def fail_zombie_job(self, job_id: Any, reason: str) -> bool:
        return self._call(self._backend.afail_zombie_job(job_id, reason))


class _SkipJobsBackend:
    """Backend view for the zombie sweep that hides jobs this worker is running itself.

    A worker whose id is not a UUID cannot register, so its claims carry no owner and the
    sweep would otherwise fail its own live jobs once they outlive ``WORKER_ALIVE_TIMEOUT``.
    """

    def __init__(self, backend: Any, skip_ids: Any) -> None:
        self._backend = backend
        self._skip_ids = skip_ids

    def get_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        records = self._backend.get_running_jobs_for_liveness(queue_names)
        skip = set(self._skip_ids())
        return [r for r in records if r.job_id not in skip]

    def fail_zombie_job(self, job_id: Any, reason: str) -> bool:
        return self._backend.fail_zombie_job(job_id, reason)


class AsyncWorker:
    """Coroutine-based worker (one OS process, many in-flight asyncio jobs).

    Args:
        backend: An :class:`AsyncDatabaseBackend` implementation.
        queues: List of queue names to poll (e.g. ``["default"]``).
        worker_id: Optional explicit worker id; auto-generated via uuid7 by
            default.
        poll_interval: Sleep seconds between empty polls. Default 1.0.
        concurrency: Maximum number of jobs in flight at once. Default 10.
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
        concurrency: int = 10,
    ) -> None:
        if backend is None:
            raise ValueError("backend is required")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.concurrency = int(concurrency)
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
        # Set by the optional LISTEN task (SQLERY_PG_NOTIFY + PostgreSQL) on each NOTIFY.
        self._notify_event: asyncio.Event | None = None
        self._listen_task: asyncio.Task | None = None
        # Map of job_id -> asyncio.Task for in-flight jobs (up to ``concurrency``).
        self._inflight: dict[Any, asyncio.Task] = {}
        # job_id -> attempt handle of the claim (a re-fetched job row does not carry it).
        self._attempts: dict[Any, Any] = {}

    def _attempt_kwargs(self, job_id: Any, method: str) -> dict:
        """``{"attempt": handle}`` for the backend completion method ``method``; empty when the
        claim had no handle or the backend method predates attempts."""
        if job_id not in self._attempts:
            return {}
        return attempt_kwargs(self._attempts[job_id], getattr(self.backend, method))

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
        # Refuse to run without the attempt table: completions could not be fenced.
        table_check = getattr(self.backend, "aattempt_table_exists", None)
        if table_check is not None and not await table_check():
            raise AttemptTableMissingError(MISSING_TABLE_MESSAGE)
        # ... and without the ready-queue tables there would be nothing to claim from.
        ready_check = getattr(self.backend, "aready_tables_exist", None)
        if ready_check is not None and not await ready_check():
            raise ReadyTablesMissingError(READY_MISSING_TABLE_MESSAGE)
        # ... and without the history table a finished job could not be recorded.
        history_check = getattr(self.backend, "ahistory_table_exists", None)
        if history_check is not None and not await history_check():
            raise HistoryTableMissingError(HISTORY_MISSING_TABLE_MESSAGE)
        self._shutdown_event = asyncio.Event()
        self._install_signal_handlers()
        await self._register()
        await self._recover_orphans()
        self._start_listener()
        last_sweep = time.monotonic()
        last_heartbeat = float("-inf")
        sweep_interval = self._orphan_sweep_interval()
        jobs_started = 0
        polls = 0
        running: set[asyncio.Task] = set()
        try:
            while not self._shutting_down:
                if max_polls is not None and polls >= max_polls:
                    break
                if max_jobs is not None and jobs_started >= max_jobs:
                    break
                polls += 1
                if time.monotonic() - last_sweep >= sweep_interval:
                    last_sweep = time.monotonic()
                    await self._recover_orphans()
                if time.monotonic() - last_heartbeat >= self._heartbeat_interval():
                    last_heartbeat = time.monotonic()
                    try:
                        await self.backend.aupdate_heartbeat(self.worker_id)
                    except Exception as e:  # pragma: no cover - heartbeat is best-effort
                        logger.warning(f"Heartbeat update failed: {e}")

                if len(running) >= self.concurrency:
                    # At capacity: wait for a slot (or the next heartbeat tick).
                    await asyncio.wait(
                        running, timeout=self.poll_interval, return_when=asyncio.FIRST_COMPLETED
                    )
                    continue

                if self._notify_event is not None:
                    # Cleared BEFORE claiming: a NOTIFY landing after this point still
                    # cuts the idle wait short, so no wake-up is lost between claim and wait.
                    self._notify_event.clear()
                try:
                    job = await self.backend.aclaim_job(self.queues, self.worker_id)
                except Exception as e:
                    logger.exception(f"aclaim_job failed: {e}")
                    await self._idle_wait(self.poll_interval)
                    continue

                if job is None:
                    await self._idle_wait(await self._idle_timeout())
                    continue

                handle = attempt_of(job)
                if handle is not None:
                    self._attempts[job.id] = handle
                runner = asyncio.create_task(self._execute_job(job))
                running.add(runner)
                runner.add_done_callback(running.discard)
                jobs_started += 1
        finally:
            await self._stop_listener()
            # Let started jobs finish (or hand over to the drain on shutdown).
            if running:
                await asyncio.gather(*list(running), return_exceptions=True)
            # On shutdown, drain in-flight jobs with deadline (Task 2 logic).
            if self._shutting_down and self._inflight:
                await self._drain_with_deadline()
            try:
                await self.backend.aunregister_worker(self.worker_id)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    # ------------------------------------------------------------- idle wait

    def _heartbeat_interval(self) -> float:
        """Minimum seconds between heartbeats: the configured interval, capped at
        (roughly) one poll interval so a normal poll cycle still beats once, while
        passes shortened by a due-job wake-up do not beat every time."""
        try:
            from sqlery.compat import get_config

            interval = float(get_config("WORKER_HEARTBEAT_INTERVAL", 5))
        except Exception:
            interval = 5.0
        return max(min(interval, self.poll_interval * 0.9), 0.0)

    async def _idle_timeout(self) -> float:
        """poll_interval, shortened to the earliest queued job that is not due yet."""
        try:
            delay = await self.backend.anext_due_delay(self.queues)
        except Exception as e:  # best-effort: fall back to the plain poll interval
            logger.debug(f"AsyncWorker {self.worker_id}: anext_due_delay failed: {e}")
            return self.poll_interval
        if delay is None:
            return self.poll_interval
        return min(self.poll_interval, max(delay + 0.05, 0.1))

    async def _idle_wait(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, waking at once on shutdown or NOTIFY."""
        event = self._shutdown_event
        if event is None:
            await asyncio.sleep(timeout)
            return
        notify = self._notify_event
        if notify is None:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            return
        waiters = {asyncio.ensure_future(event.wait()), asyncio.ensure_future(notify.wait())}
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()

    # ------------------------------------------------------- LISTEN / NOTIFY

    def _start_listener(self) -> None:
        """Start the optional LISTEN task. Opt-in, PostgreSQL only, never fatal."""
        try:
            from sqlery.compat import get_config

            if not get_config("SQLERY_PG_NOTIFY", False):
                return
            import psycopg  # noqa: F401
            from .pg_notify import resolve_pg_dsn, sanitize_queue_name_to_channel

            dsn = resolve_pg_dsn()
            if not dsn:
                return
            channels = [sanitize_queue_name_to_channel(q) for q in self.queues]
        except Exception as e:
            logger.debug(f"AsyncWorker {self.worker_id}: LISTEN unavailable ({e}); polling")
            return
        self._notify_event = asyncio.Event()
        self._listen_task = asyncio.create_task(self._listen_loop(dsn, channels))

    async def _listen_loop(self, dsn: str, channels: list[str]) -> None:
        """LISTEN on ``channels`` and set ``_notify_event`` on every NOTIFY.

        Any failure is logged and retried with backoff; meanwhile the worker simply keeps
        polling, so a broken LISTEN connection costs latency, never correctness.
        """
        import psycopg
        import psycopg.sql as psql

        delay = 1.0
        while not self._shutting_down:
            try:
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                    for channel in channels:
                        await conn.execute(psql.SQL("LISTEN {}").format(psql.Identifier(channel)))
                    delay = 1.0
                    logger.info(f"AsyncWorker {self.worker_id}: LISTEN on {channels}")
                    # A job enqueued while we were not listening produces no NOTIFY.
                    self._notify_event.set()
                    async for _ in conn.notifies():
                        self._notify_event.set()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"AsyncWorker {self.worker_id}: LISTEN connection error: {e}; "
                    f"polling, retry in {delay:.0f}s"
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _stop_listener(self) -> None:
        task, self._listen_task = self._listen_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except BaseException:  # CancelledError or a last listener error
            pass

    # ----------------------------------------------------------- execution

    async def _execute_job(self, job: Any) -> None:
        """Dispatch a single claimed job. Cooperates with shutdown drain."""
        job_id = job.id
        try:
            from .utils import import_task

            func = import_task(job.task_path)
            args, kwargs = _deserialize_kwargs(getattr(job, "kwargs", {}))
        except Exception as e:
            tb = tb_module.format_exc()
            try:
                await self.backend.amark_failed(
                    job_id,
                    error=str(e),
                    traceback=tb,
                    **self._attempt_kwargs(job_id, "amark_failed"),
                )
            except JobFencingError as fence_err:
                logger.warning(f"Job {job_id} failed to load but was superseded: {fence_err}")
            finally:
                self._attempts.pop(job_id, None)
            return

        from .execution import ainvoke_task
        from .worker import _current_job_var

        _current_job_var.set(job)  # per-task context; visible to the job body
        task = asyncio.create_task(ainvoke_task(func, job, args, kwargs))

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
        try:
            await self.backend.amark_success(
                job.id, result, **self._attempt_kwargs(job.id, "amark_success")
            )
        except JobFencingError as e:
            logger.warning(
                f"Job {job.id} succeeded but was superseded by recovery before completion "
                f"could be recorded — discarding this result: {e}"
            )
        finally:
            self._attempts.pop(job.id, None)

    async def _on_job_failed(
        self, job: Any, *, error: str, traceback: str | None = None
    ) -> None:
        try:
            await self.backend.amark_failed(
                job.id,
                error=error,
                traceback=traceback,
                **self._attempt_kwargs(job.id, "amark_failed"),
            )
        except JobFencingError as e:
            logger.warning(
                f"Job {job.id} failed but was superseded by recovery before completion "
                f"could be recorded — discarding this result: {e}"
            )
            return  # recovery owns the job now: no retry from this attempt
        finally:
            self._attempts.pop(job.id, None)
        if self._should_retry(job):
            try:
                await self._requeue_for_retry(job)
            except Exception as e:  # pragma: no cover - logged
                logger.exception(f"Retry-requeue failed for job {job.id}: {e}")
        # The failure is final, or its retry now exists: re-check the jobs waiting on it.
        await self._release_dependents(job)

    async def _release_dependents(self, job: Any) -> None:
        """Ask the backend to re-check the jobs blocked on ``job`` (best effort: the
        scheduler leader's periodic sweep repairs a missed release)."""
        release = getattr(self.backend, "arelease_dependents", None)
        if release is None:
            return
        try:
            await release(job.id)
        except Exception as e:  # pragma: no cover - logged
            logger.warning(f"Releasing dependents of job {job.id} failed: {e}")

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
                    await self.backend.amark_failed(
                        job_id,
                        error=str(e),
                        traceback=tb,
                        **self._attempt_kwargs(job_id, "amark_failed"),
                    )
            else:
                try:
                    await self.backend.amark_success(
                        job_id, result, **self._attempt_kwargs(job_id, "amark_success")
                    )
                except JobFencingError as fence_err:
                    logger.warning(f"Job {job_id} superseded before completion: {fence_err}")
        else:
            # Step 4: deadline wins → cancel + mark failed + retry path.
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await self.backend.amark_failed(
                    job_id,
                    error=SHUTDOWN_TIMEOUT_ERROR,
                    **self._attempt_kwargs(job_id, "amark_failed"),
                )
            except JobFencingError as fence_err:
                logger.warning(f"Job {job_id} superseded before shutdown failure: {fence_err}")
                self._attempts.pop(job_id, None)
                self._inflight.pop(job_id, None)
                return
            if job is not None and self._should_retry(job):
                try:
                    await self._requeue_for_retry(job)
                except Exception as e:  # pragma: no cover
                    logger.exception(f"Shutdown retry-requeue failed: {e}")

        self._attempts.pop(job_id, None)
        self._inflight.pop(job_id, None)

    # ------------------------------------------------------------ recovery

    @staticmethod
    def _orphan_sweep_interval() -> float:
        from sqlery.compat import get_config

        return float(get_config("WORKER_ALIVE_TIMEOUT", 30))

    async def _recover_orphans(self) -> None:
        """Fail ``running`` jobs whose worker died (e.g. ``kill -9``) without finishing them.

        There is no daemon in async mode, so each worker runs the daemon's zombie sweep at
        start-up and then periodically. Failing the row lets retries and interval
        ``ScheduledTask`` enqueueing continue. Best-effort: never stops the worker.
        """
        try:
            from .daemon import DaemonManager

            queues = list(self.queues)
            use_thread = False
            if hasattr(self.backend, "aget_running_jobs_for_liveness"):
                # Use the worker's own (async) backend: the global sync backend may not be
                # configured in an async-only standalone deployment.
                sync_backend = _SyncLivenessAdapter(self.backend, asyncio.get_running_loop())
                use_thread = True  # the adapter needs no Django thread affinity
            else:
                from sqlery.compat import get_backend

                sync_backend = get_backend()

            sync_backend = _SkipJobsBackend(sync_backend, lambda: list(self._inflight))

            def sweep() -> None:
                failed = DaemonManager._fail_zombie_running_jobs(
                    sync_backend, queues, concurrent_workers=True
                )
                if failed:
                    logger.info(f"AsyncWorker {self.worker_id} failed {failed} orphaned job(s)")

            if use_thread:
                await asyncio.to_thread(sweep)
                return
            try:
                from asgiref.sync import sync_to_async
            except ImportError:  # standalone install without Django/asgiref
                await asyncio.to_thread(sweep)
            else:
                await sync_to_async(sweep)()
        except Exception as e:
            logger.warning(f"Orphaned-job recovery failed: {e}")

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
