"""Django-agnostic worker execution logic: prefork pool or fork-per-job (#161)."""

import asyncio
import contextvars
import inspect
import json
import logging
import re
import socket
import sys
import traceback as tb
import signal
import os
import select
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from ..compat import get_backend, get_config, initialize, is_standalone_mode, JobFencingError
from .attempts import attempt_kwargs, attempt_of, require_attempt_table
from .history import require_history_table
from .ready import require_ready_tables
from .utils import describe_task_args, import_task
from .result import serialize_result
from .execution import invoke_task, run_failure_callback, run_success_callback
from .retry import (
    pending_reason,
    policy_allows_retry,
    retry_budget_left,
    retry_countdown,
    shape_retry_delay,
)
from .signals import task_retry
from .result import REVOKED_ERROR
from .scheduler import Scheduler
from .security import check_task_module_allowed, warn_if_unconfigured
from .fork_safety import ForkSafeExecutor
from .pool import (
    POOL_FORK,
    POOL_PREFORK,
    db_refresh_due,
    encode_frame,
    load_pool_settings,
    max_rss_kib,
    read_frame,
    refresh_db_connections,
    should_recycle,
    write_all,
)
from sqlery.core.db_resilience import (
    configure_connection_resilience,
    discard_connections,
    is_connection_lost,
    probe_database,
    reconnect_backoff_settings,
    reconnect_delay,
)

try:
    from django.db import connections, close_old_connections
except ImportError:
    connections = None
    close_old_connections = None

# Phase 18: guard-import psycopg3 for LISTEN/NOTIFY wake-up (PG-only, opt-in).
# psycopg is already a declared dependency; this guard is for environments
# where the standalone mode is used without psycopg installed.
try:
    import psycopg as _psycopg
    import psycopg.sql as _psycopg_sql
    _psycopg_available = True
except ImportError:
    _psycopg = None  # type: ignore[assignment]
    _psycopg_sql = None  # type: ignore[assignment]
    _psycopg_available = False

# Phase 18: guard-import sanitize_queue_name_to_channel for channel naming.
try:
    from sqlery.core.pg_notify import sanitize_queue_name_to_channel
    _pg_notify_import_ok = True
except ImportError:
    sanitize_queue_name_to_channel = None  # type: ignore[assignment]
    _pg_notify_import_ok = False

# Inherited LISTEN connections disowned by a forked child (see WorkerProcess._orphan_listen_conn).
# Held here so they are never garbage-collected (PQfinish) in the child.
_ORPHANED_LISTEN_CONNS: list = []

_current_job_var: contextvars.ContextVar = contextvars.ContextVar('current_job', default=None)

logger = logging.getLogger(__name__)


def _disarm_alarm() -> None:
    """Cancel a job's SIGALRM (hard timeout and soft limit) and drop its handler."""
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass  # no setitimer (Windows) or not the main thread


def compute_retry_delay(
    backoff: float, retry_count: int, retry_intervals: list | None = None
) -> float:
    """Delay (seconds) before the next retry attempt.

    Fixed ``retry_intervals`` win; otherwise ``backoff * 2**retry_count``. Celery-style
    ``RETRY_BACKOFF_MAX`` (cap) and ``RETRY_JITTER`` (full jitter, avoids thundering herd)
    are opt-in via config; defaults leave the delay unchanged.
    """
    import random

    if retry_intervals:
        return float(retry_intervals[min(retry_count, len(retry_intervals) - 1)])
    delay = backoff * (2 ** retry_count)
    try:
        cap = get_config("RETRY_BACKOFF_MAX", None)
        if cap:
            delay = min(delay, float(cap))
        if get_config("RETRY_JITTER", False):
            delay = random.uniform(0, delay)
    except Exception:
        pass
    return delay


@dataclass
class UnpersistedOutcome:
    """A job outcome a worker child could not write because its DB connection was lost.

    The child sends it to the worker parent (the supervisor), which reconnects and persists
    it (``JobExecutor.persist_outcome``). The job is never run again for it.
    """

    job_id: int
    success: bool
    expected_version: int | None = None
    attempt: object = None
    output: str | None = None
    error: str = ""
    traceback: str = ""
    will_retry: bool = False
    countdown: float | None = None


class JobExecutor:
    """Executes jobs with retry logic, timeout support, and crash recovery.

    Works in both Django and standalone modes via backend abstraction.
    """

    def __init__(self, backend=None):
        if backend is None:
            # from ..compat import get_backend  # moved to top-level
            backend = get_backend()
        self.backend = backend
        # Set in a worker child when the connection was lost while recording the outcome;
        # sent to the parent, which persists it (#161).
        self.unpersisted: UnpersistedOutcome | None = None
        # Fork pool child: the pipe that sends ``unpersisted`` to the parent.
        self.outcome_fd: int | None = None

    def execute_job(self, job):
        """Execute a single job with error handling and retry logic."""
        logger.info(f"Executing job {job.id}: {job.task_path}")

        # The claim's attempt handle rides on the claimed job object; capture it before the
        # refresh below replaces the object. Every completion write closes exactly this
        # attempt, so a worker superseded by recovery cannot record an outcome.
        attempt = attempt_of(job)

        # Refresh job state from database
        job = self.backend.get_job_by_id(job.id)
        # Captured before execution runs so mark_job_success/failed below can
        # fence the completion write against this exact version — see
        # JobFencingError docstring for why a fresh re-fetch there would
        # defeat the CAS if the job was reclaimed mid-execution.
        expected_version = getattr(job, 'version', None)

        # Skip if already running
        if job.status == 'running':
            logger.info(f"Job {job.id} already running, proceeding with execution")
        elif job.status != 'queued':
            logger.warning(f"Job {job.id} has status '{job.status}', skipping execution")
            return job

        try:
            # Apply global default timeout if not set on the job
            if not job.timeout_seconds:
                # from ..compat import get_config  # moved to top-level
                job.timeout_seconds = get_config('DEFAULT_TIMEOUT_SECONDS', 600)

            # Set up timeout. SIGALRM can only be installed from the main thread; threaded
            # callers (e.g. an HTTP trigger under runserver/gthread) run without a hard timeout.
            use_alarm = bool(job.timeout_seconds) and (
                threading.current_thread() is threading.main_thread()
            )
            if job.timeout_seconds and not use_alarm:
                logger.warning(
                    f"Job {job.id} is running outside the main thread; the "
                    f"{job.timeout_seconds}s hard timeout cannot be enforced"
                )
            if use_alarm:
                def timeout_handler(signum, frame):
                    raise TimeoutError(
                        f"Job {job.id} exceeded timeout of {job.timeout_seconds} seconds"
                    )

                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(job.timeout_seconds)
                logger.info(f"Job {job.id} timeout set to {job.timeout_seconds} seconds")

            try:
                task_func = self._import_task(job.task_path)

                kwargs = job.kwargs if isinstance(job.kwargs, dict) else {}
                positional_args = kwargs.pop('_args', ())
                logger.info(f"Job {job.id} executing with {describe_task_args(positional_args, kwargs)}")
                _token = _current_job_var.set(job)
                try:
                    result = invoke_task(task_func, job, positional_args, kwargs)
                    # REGRESSION 2026-05-25: @async_job silently "succeeded" without running
                    # Root cause: coroutine returned by async task was never awaited
                    # Fix: detect coroutine result and run it to completion via asyncio.run()
                    if inspect.iscoroutine(result):
                        result = asyncio.run(result)
                finally:
                    _current_job_var.reset(_token)

                try:
                    job = self.backend.mark_job_success(
                        job.id,
                        output=serialize_result(result),
                        expected_version=expected_version,
                        **attempt_kwargs(attempt, self.backend.mark_job_success),
                    )
                except JobFencingError as e:
                    logger.warning(
                        f"Job {job.id} succeeded but was superseded by another worker "
                        f"before completion could be recorded — discarding this result: {e}"
                    )
                    return job
                # If this is a retry, cascade success to all ancestor attempts
                if getattr(job, 'parent_job_id', None):
                    try:
                        self.backend.cascade_ancestor_status(job.id, 'success')
                    except Exception as e:
                        logger.warning(f"Failed to cascade success for job {job.id}: {e}")
                run_success_callback(job, result)
                logger.info(f"Job {job.id} completed successfully")

            finally:
                if use_alarm:
                    signal.alarm(0)

        except Exception as e:
            error_msg = str(e)
            error_traceback = tb.format_exc()
            # Decide BEFORE failing the job: a job that will be retried is failed with the
            # retry_pending marker so a crash before the retry exists can be recovered.
            will_retry = self._should_retry(job, e)
            try:
                job = self._mark_failed(
                    job.id, error_msg, error_traceback, expected_version, will_retry,
                    attempt=attempt,
                )
            except JobFencingError as fence_err:
                logger.warning(
                    f"Job {job.id} failed but was superseded by another worker "
                    f"before completion could be recorded — discarding this result: {fence_err}"
                )
                return job
            logger.error(f"Job {job.id} failed: {error_msg}")
            run_failure_callback(job)

            if will_retry:
                retry_job = self._retry_job(job, exc=e)
                logger.info(
                    f"Job {job.id} will be retried as job {retry_job.id} "
                    f"(attempt {retry_job.retry_count + 1}/{retry_job.max_retries + 1})"
                )

        return job

    def execute_job_in_child(self, job):
        """Execute a job in a forked child process (called after fork).

        Child writes results directly to DB. No pipe IPC needed — the parent
        reads final status from the DB after waitpid() returns.
        This method never returns.
        """
        exit_code = self.run_job_in_child(job)
        if self.outcome_fd is not None and self.unpersisted is not None:
            try:
                write_all(self.outcome_fd, encode_frame(self.unpersisted))
            except Exception as e:
                logger.error(f"Could not hand job {self.unpersisted.job_id} to the parent: {e}")
        os._exit(exit_code)

    def run_job_in_child(self, job) -> int:
        """Run one job in a worker child and return its exit code (0 success, 1 failure).

        The fork pool's child exits with it; the prefork pool's long-lived child (#161)
        reports it to the parent and waits for the next job, so the job's alarm is
        disarmed before returning. If the DB connection is lost while the outcome is
        recorded, the outcome is left in ``self.unpersisted`` for the parent.
        """
        self.unpersisted = None
        # write_fd = int(os.environ.pop('_SQLERY_RESULT_FD'))
        expected_version = None  # set below; kept here so the except: block never NameErrors
        # The claim's attempt handle (inherited through fork); captured before the refresh
        # below replaces the job object.
        attempt = attempt_of(job)

        try:
            # Configure DB resilience for child's fresh connection.
            # for_job_child=True skips statement_timeout — user task queries can
            # legitimately take longer than the daemon/worker guard value.
            # from sqlery.core.db_resilience import configure_connection_resilience  # moved to top-level
            configure_connection_resilience(for_job_child=True)

            # Reset signal handlers — child doesn't need heartbeat
            signal.signal(signal.SIGUSR1, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)

            # Refresh job from DB (new connection)
            job = self.backend.get_job_by_id(job.id)
            # Captured before execution so the completion write below is
            # fenced against this exact version (see JobFencingError).
            expected_version = getattr(job, 'version', None)

            # Apply timeout
            if not job.timeout_seconds:
                # from ..compat import get_config  # moved to top-level
                job.timeout_seconds = get_config('DEFAULT_TIMEOUT_SECONDS', 600)

            if job.timeout_seconds:
                def timeout_handler(signum, frame):
                    raise TimeoutError(
                        f"Job {job.id} exceeded timeout of {job.timeout_seconds} seconds"
                    )
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(job.timeout_seconds)

            # Import and execute
            task_func = self._import_task(job.task_path)
            kwargs = job.kwargs if isinstance(job.kwargs, dict) else {}
            positional_args = kwargs.pop('_args', ())
            logger.info(f"Job {job.id} executing with {describe_task_args(positional_args, kwargs)}")

            _token = _current_job_var.set(job)
            try:
                result = invoke_task(task_func, job, positional_args, kwargs)
                if inspect.iscoroutine(result):
                    result = asyncio.run(result)
            finally:
                _current_job_var.reset(_token)

            # Mark success in DB from child, fenced against the version this
            # child observed at start-of-execution — if another worker
            # reclaimed the job (this worker's lease/heartbeat went stale
            # mid-run), the version has moved on and the write is rejected
            # instead of clobbering the reclaiming worker's outcome.
            try:
                job = self.backend.mark_job_success(
                    job.id,
                    output=serialize_result(result),
                    expected_version=expected_version,
                    **attempt_kwargs(attempt, self.backend.mark_job_success),
                )
            except JobFencingError as e:
                logger.warning(
                    f"Job {job.id} succeeded but was superseded by another worker "
                    f"before completion could be recorded — discarding this result: {e}"
                )
                return 0
            except Exception as e:
                if not is_connection_lost(e):
                    raise
                # The job ran: never run it again. The supervisor persists the result.
                self._hand_over(UnpersistedOutcome(
                    job_id=job.id, success=True, output=serialize_result(result),
                    expected_version=expected_version, attempt=attempt,
                ), e)
                return 0
            # If this is a retry, cascade success to all ancestor attempts
            if getattr(job, 'parent_job_id', None):
                try:
                    self.backend.cascade_ancestor_status(job.id, 'success')
                except Exception as e:
                    logger.warning(f"Failed to cascade success for job {job.id}: {e}")
            run_success_callback(job, result)
            logger.info(f"Job {job.id} completed successfully")

            # # Pipe write removed — parent reads status from DB
            # result_payload = json.dumps({
            #     'success': True,
            #     'output': str(result or ''),
            # }).encode()
            # os.write(write_fd, result_payload)
            # os.close(write_fd)
            return 0

        except Exception as e:
            # Disarm first: a soft limit re-arms the alarm for the hard limit, which must
            # not fire while the failure is being recorded.
            _disarm_alarm()
            error_msg = str(e)
            error_traceback = tb.format_exc()
            logger.error(f"Job {job.id} failed: {error_msg}")

            try:
                # Reset DB connections before marking failure — the task may
                # have left the connection in InFailedSqlTransaction state
                # (e.g. a ProgrammingError in bulk_insert), which would cause
                # mark_job_failed to fail on the same broken connection.
                self._reset_db_connections()
                will_retry = self._should_retry(job, e)
                try:
                    failed_job = self._mark_failed(
                        job.id, error_msg, error_traceback, expected_version, will_retry,
                        attempt=attempt,
                    )
                except Exception as lost:
                    if not is_connection_lost(lost):
                        raise
                    self._hand_over(UnpersistedOutcome(
                        job_id=job.id, success=False, error=error_msg,
                        traceback=error_traceback, expected_version=expected_version,
                        attempt=attempt, will_retry=will_retry, countdown=retry_countdown(e),
                    ), lost)
                    return 1

                run_failure_callback(failed_job)

                # Handle retry in child (it has the DB connection)
                if will_retry:
                    retry_job = self._retry_job(failed_job, exc=e)
                    logger.info(
                        f"Job {job.id} will be retried as job {retry_job.id} "
                        f"(attempt {retry_job.retry_count + 1}/{retry_job.max_retries + 1})"
                    )
            except JobFencingError as fence_err:
                logger.warning(
                    f"Job {job.id} failed but was superseded by another worker "
                    f"before completion could be recorded — discarding this result: {fence_err}"
                )
            except Exception as mark_err:
                logger.error(f"Failed to mark job {job.id} as failed: {mark_err}")

            # # Pipe write removed — parent reads status from DB
            # try:
            #     result_payload = json.dumps({
            #         'success': False,
            #         'error': error_msg,
            #         'traceback': error_traceback[:4096],
            #     }).encode()
            #     os.write(write_fd, result_payload)
            #     os.close(write_fd)
            # except Exception:
            #     pass

            return 1
        finally:
            _disarm_alarm()

    def _hand_over(self, outcome: "UnpersistedOutcome", error: BaseException) -> None:
        """Child: the connection is gone, so leave ``outcome`` for the supervisor to persist."""
        logger.warning(
            f"Job {outcome.job_id}: DB connection lost while recording its outcome ({error}); "
            f"handing it to the worker parent"
        )
        discard_connections()
        self.unpersisted = outcome

    def persist_outcome(self, outcome: "UnpersistedOutcome") -> None:
        """Supervisor: write an outcome a child could not, exactly as the child would have.

        Success and failure callbacks are not run: they are task code and belong in the
        child. Fenced like the child's write, so a superseded attempt records nothing.
        """
        try:
            if outcome.success:
                job = self.backend.mark_job_success(
                    outcome.job_id,
                    output=outcome.output,
                    expected_version=outcome.expected_version,
                    **attempt_kwargs(outcome.attempt, self.backend.mark_job_success),
                )
                if getattr(job, "parent_job_id", None):
                    try:
                        self.backend.cascade_ancestor_status(job.id, "success")
                    except Exception as e:
                        logger.warning(f"Failed to cascade success for job {job.id}: {e}")
            else:
                failed_job = self._mark_failed(
                    outcome.job_id, outcome.error, outcome.traceback, outcome.expected_version,
                    outcome.will_retry, attempt=outcome.attempt,
                )
                if outcome.will_retry:
                    self._retry_job(failed_job, countdown=outcome.countdown)
        except JobFencingError as e:
            logger.warning(
                f"Job {outcome.job_id} was superseded before its outcome could be recorded — "
                f"discarding it: {e}"
            )
            return
        logger.info(
            f"Job {outcome.job_id}: outcome persisted by the worker parent "
            f"({'success' if outcome.success else 'failed'}; callbacks not run)"
        )

    def _reset_db_connections(self):
        """Close inherited DB connections after fork."""
        try:
            # from django.db import connections  # moved to top-level (try/except)
            if connections is not None:
                connections.close_all()
        except Exception:
            pass

    def _import_task(self, task_path: str):
        # SEC-04 is now enforced inside import_task() itself.
        return import_task(task_path)

    def _mark_failed(self, job_id, error, traceback, expected_version=None, will_retry=False,
                     reason="", attempt=None):
        """Fail a job; if it will be retried, tag it ``retry_pending`` in the same write.

        Custom/older backends whose ``mark_job_failed`` lacks ``termination_reason`` are called
        the old way (no marker, so no crash recovery for them, but nothing breaks).
        """
        kwargs = {"error": error, "traceback": traceback}
        if expected_version is not None:
            kwargs["expected_version"] = expected_version
        kwargs.update(attempt_kwargs(attempt, self.backend.mark_job_failed))
        if will_retry and "termination_reason" in inspect.signature(
            self.backend.mark_job_failed
        ).parameters:
            kwargs["termination_reason"] = pending_reason(reason)
        return self.backend.mark_job_failed(job_id, **kwargs)

    def _finish_retry_bookkeeping(self, failed_job):
        """The retry exists: archive the original and clear its ``retry_pending`` marker."""
        # Mark original failed job as archived (retry created, no longer relevant to dashboard)
        try:
            self.backend.mark_job_archived(failed_job.id)
        except Exception as e:
            logger.warning(f"Failed to mark job {failed_job.id} as archived: {e}")
        try:
            self.backend.settle_retry_pending(failed_job.id)
        except Exception as e:
            logger.warning(f"Failed to clear retry marker on job {failed_job.id}: {e}")

    def recover_lost_retries(self, grace_seconds: float = 60, limit: int = 100) -> int:
        """Create the retry for jobs whose worker died between failing them and retrying them.

        Finds failed jobs tagged ``retry_pending`` for longer than ``grace_seconds`` and calls
        the (idempotent) :meth:`_retry_job`. Returns how many were recovered. Never raises.
        """
        recovered = 0
        try:
            pending = self.backend.get_jobs_pending_retry(grace_seconds, limit)
        except Exception as e:
            logger.warning(f"Could not look for lost retries: {e}")
            return 0
        for job in pending:
            try:
                if retry_budget_left(job):
                    self._retry_job(job)
                    recovered += 1
                    logger.warning(f"Recovered lost retry for failed job {job.id}")
                else:
                    self.backend.settle_retry_pending(job.id)
            except Exception as e:
                logger.error(f"Failed to recover retry for job {job.id}: {e}")
        return recovered

    def _should_retry(self, job, exc: BaseException | None = None) -> bool:
        if job.max_retries is None or job.max_retries <= 0:
            return False
        if job.retry_count is not None and job.retry_count >= job.max_retries:
            return False
        # Task-declared policy (autoretry_for / Retry); crash-recovery callers pass no exc.
        return policy_allows_retry(job, exc)

    def _retry_job(self, failed_job, exc: BaseException | None = None,
                   countdown: float | None = None):
        # Idempotent: if a retry already exists (e.g. recovery after a crash, or a stale-job
        # sweep racing the worker that failed it), reuse it instead of creating a duplicate.
        existing = self.backend.get_latest_retry(failed_job.id)
        if existing is not None:
            self._finish_retry_bookkeeping(failed_job)
            return existing
        retry_count = failed_job.retry_count if failed_job.retry_count is not None else 0
        backoff = failed_job.retry_backoff if failed_job.retry_backoff else 1.0
        delay_seconds = compute_retry_delay(
            backoff, retry_count, getattr(failed_job, "retry_intervals", None)
        )
        delay_seconds = shape_retry_delay(
            delay_seconds,
            failed_job,
            countdown=countdown if countdown is not None else retry_countdown(exc),
        )

        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

        # Derive a retry job_name so overlap/duplicate checks can find it.
        # Strip ALL ":retry:N" segments (they can appear mid-name when
        # handle_response appends :[page] after the retry suffix).
        retry_job_name = None
        original_name = getattr(failed_job, 'job_name', None)
        if original_name:
            # import re  # moved to top-level
            # base_name = re.sub(r':retry:\d+$', '', original_name)
            base_name = re.sub(r':retry:\d+', '', original_name)
            retry_job_name = f"{base_name}:retry:{retry_count + 1}"

        retry_kwargs = dict(
            task_path=failed_job.task_path,
            kwargs=failed_job.kwargs.copy() if isinstance(failed_job.kwargs, dict) else {},
            queue_name=failed_job.queue_name,
            priority=failed_job.priority,
            scheduled_at=scheduled_at,
            job_name=retry_job_name,
            max_retries=failed_job.max_retries,
            retry_backoff=failed_job.retry_backoff,
            allow_parallel=failed_job.allow_parallel,
            timeout_seconds=failed_job.timeout_seconds,
            retry_count=retry_count + 1,
            parent_job_id=failed_job.id,
        )
        # Carry the rest of the job spec (previously dropped on retry). Only pass fields the
        # backend's create_job accepts, so custom/older DatabaseBackend subclasses keep working.
        carried = dict(
            retry_intervals=getattr(failed_job, "retry_intervals", None),
            dependencies=list(getattr(failed_job, "dependencies", None) or []),
            tags=list(getattr(failed_job, "tags", None) or []),
            meta=getattr(failed_job, "meta", None),
            on_success_path=getattr(failed_job, "on_success_path", "") or "",
            on_failure_path=getattr(failed_job, "on_failure_path", "") or "",
            result_ttl=getattr(failed_job, "result_ttl", None),
            failure_ttl=getattr(failed_job, "failure_ttl", None),
        )
        accepted = inspect.signature(self.backend.create_job).parameters
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in accepted.values()):
            carried = {k: v for k, v in carried.items() if k in accepted}
        retry_job = self.backend.create_job(**retry_kwargs, **carried)

        self._finish_retry_bookkeeping(failed_job)

        logger.info(
            f"Created retry job {retry_job.id} for failed job {failed_job.id}, "
            f"scheduled in {delay_seconds}s (attempt {retry_count + 2}/{failed_job.max_retries + 1})"
        )

        task_retry.send(failed_job.task_path, job=failed_job, retry_job=retry_job, exc=exc)
        return retry_job

    def cleanup_stale_jobs(self, queue_name: str | None = None):
        """Reset stale jobs stuck in 'running' state (crashed workers)."""
        now = datetime.now(timezone.utc)
        running_jobs = self.backend.get_running_jobs(queue_name)
        # Claiming PIDs live on the open attempts (job-row column only for pre-attempt jobs).
        attempts = self.backend.get_open_attempts([j.id for j in running_jobs])
        if not isinstance(attempts, dict):  # custom backends that predate attempts
            attempts = {}

        for job in running_jobs:
            if not job.started_at:
                self.backend.mark_job_failed(
                    job.id,
                    error="Worker crashed before job execution started",
                    traceback="Job was stuck in 'running' state with no started_at timestamp"
                )
                logger.warning(f"Cleaned up stale job {job.id} (no started_at)")
                continue

            if job.timeout_seconds:
                threshold_seconds = job.timeout_seconds * 2
            else:
                threshold_seconds = 3600

            started_at = job.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            running_duration = (now - started_at).total_seconds()

            if running_duration > threshold_seconds:
                open_attempt = attempts.get(job.id)
                worker_pid = (
                    open_attempt.worker_pid if open_attempt else getattr(job, 'worker_pid', None)
                )
                if worker_pid:
                    killed = self._kill_worker_process(worker_pid)
                    if killed:
                        logger.warning(
                            f"Killed hung worker process {worker_pid} for job {job.id}"
                        )

                self._mark_failed(
                    job.id,
                    f"Worker crashed or was killed (running for {int(running_duration)}s, threshold {threshold_seconds}s)",
                    "Job was stuck in 'running' state - likely worker process crashed, killed by OOM, or terminated by signal",
                    will_retry=self._should_retry(job),
                )
                logger.warning(
                    f"Cleaned up stale job {job.id} (running {int(running_duration)}s > {threshold_seconds}s threshold)"
                )

                job = self.backend.get_job_by_id(job.id)
                if self._should_retry(job):
                    retry_job = self._retry_job(job)
                    logger.info(
                        f"Stale job {job.id} will be retried as job {retry_job.id}"
                    )

    @staticmethod
    def _reaped(pid: int) -> bool:
        """Non-blocking check-and-reap: True once `pid` has exited and been reaped.

        Uses `os.waitpid(pid, os.WNOHANG)` instead of `os.kill(pid, 0)` for
        liveness — a zombie (exited but un-reaped) child still answers
        `os.kill(pid, 0)` successfully because it's still in the process
        table, so that check alone can never observe termination and the
        child is left as a permanent zombie under a PID-1 worker (no init to
        reap orphans). `waitpid(WNOHANG)` both detects exit and reaps in one
        step.
        """
        try:
            reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # Not our child (or already reaped by something else) — treat as gone.
            return True
        return reaped_pid == pid

    def _kill_worker_process(self, pid: int) -> bool:
        """Kill a worker process by PID. Tries SIGTERM then SIGKILL."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False

        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to worker process {pid}")

            for _ in range(10):
                time.sleep(0.5)
                if self._reaped(pid):
                    logger.info(f"Worker process {pid} terminated gracefully")
                    return True

            logger.warning(f"Worker process {pid} did not terminate, sending SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

            time.sleep(0.5)
            if self._reaped(pid):
                logger.info(f"Worker process {pid} killed with SIGKILL")
                return True

            logger.error(f"Failed to kill worker process {pid}")
            return False

        except Exception as e:
            logger.error(f"Error killing worker process {pid}: {e}")
            return False

    def can_execute_job(self, job) -> bool:
        """Check if job can be executed based on queue-level concurrency."""
        if job.allow_parallel:
            return True

        has_running_in_queue = self.backend.has_running_jobs_in_queue(
            job.queue_name,
            exclude_job_id=job.id
        )

        return not has_running_in_queue


@dataclass
class _PoolChild:
    """The prefork pool's long-lived child as the parent sees it (#161)."""

    pid: int
    job_fd: int  # parent writes claimed jobs here
    result_fd: int  # child reports each job's outcome here
    recycling: bool = False  # the child said it exits after the job it just reported
    reaped: bool = False


class WorkerProcess:
    """Persistent worker process that polls for jobs and forks children to execute them.

    WORKER_POOL picks how (#161): "prefork" (default, like Celery) sends each job to a
    long-lived child recycled after WORKER_MAX_TASKS_PER_CHILD jobs; "fork" (like RQ) is
    the fork-per-job model below.

    RQ-style fork-per-job model:
    - Parent process: claims jobs, forks children, monitors them, handles heartbeats
    - Child process: executes one job, writes result to pipe, exits
    - Memory leaks are cleaned up per-job (OS reclaims child memory)
    - Parent is never blocked by job execution, so heartbeats always work
    """

    def __init__(
        self,
        queues: list[str] | None = None,
        backend=None,
        pool: str | None = None,
        max_tasks_per_child: int | None = None,
        max_memory_per_child: int | None = None,
        db_reuse_max: int | None = None,
    ):
        """Create a worker.

        Args:
            queues: Queues to claim from, in priority order (default ``['default']``).
            backend: Database backend (default: the active one).
            pool: ``"prefork"`` or ``"fork"`` (default: WORKER_POOL, Celery's prefork).
            max_tasks_per_child: Recycle the prefork child after N jobs (default:
                WORKER_MAX_TASKS_PER_CHILD, never).
            max_memory_per_child: ... or once its max RSS exceeds N KiB (default:
                WORKER_MAX_MEMORY_PER_CHILD, never).
            db_reuse_max: Force-refresh DB connections every N jobs (default:
                WORKER_DB_REUSE_MAX, else the recycle rate).
        """
        if backend is None:
            # from ..compat import get_backend  # moved to top-level
            backend = get_backend()
            # Fresh standalone process (e.g. `sqlery worker`): nothing initialised the
            # database yet, so do what core/worker_runner.py does and use the env URL.
            database_url = os.environ.get("SQLERY_DATABASE_URL")
            if database_url and is_standalone_mode():
                from ..fastapi_sqlery import database as _standalone_db

                if _standalone_db._engine is None:
                    initialize(database_url=database_url, enable_daemon=False)
                    backend = get_backend()

        self.backend = backend
        self.queues = queues or ['default']
        self.executor = JobExecutor(backend)
        self._fork_ctx = ForkSafeExecutor.auto_configure()
        self.pool_settings = load_pool_settings(
            pool, max_tasks_per_child, max_memory_per_child, db_reuse_max
        )
        self._pool_child: _PoolChild | None = None
        # An outcome the job's child could not persist (connection lost), set while waiting.
        self._handed_over: UnpersistedOutcome | None = None
        self.shutdown_requested = False
        self.jobs_processed = 0
        self.current_job = None
        self.child_pid = None  # PID of forked child currently executing a job
        # WR-01: scheduler-election state exposed to _fork_and_execute so leases
        # can be renewed during long-running (blocking) jobs. Held queues this
        # worker leads and the lease TTL; populated by run() once election runs.
        self._owned_queues: set[str] = set()
        self._lease_secs: int = 0
        self.total_busy_seconds = 0.0
        self._heartbeat_due = False
        self._last_loop_time = time.monotonic()

        # import socket  # moved to top-level
        self.node_id = os.environ.get("NODE_ID", socket.gethostname())
        self.pid = os.getpid()
        self.worker_id = f"worker_{self.node_id}_{self.pid}"

        # from ..compat import get_config  # moved to top-level
        self.poll_interval = get_config('WORKER_POLL_INTERVAL', 5)
        self.heartbeat_interval = get_config('WORKER_HEARTBEAT_INTERVAL', 5)
        # Phase 18: dedicated psycopg3 AUTOCOMMIT connection for LISTEN/NOTIFY
        # wake-up. None when SQLERY_PG_NOTIFY is False (default) or on SQLite.
        self._listen_conn = None
        # #110: idle-poll housekeeping state (see _idle_tick / _idle_housekeeping)
        self._last_tick: float | None = None
        self._last_lease_renew = 0.0
        self._sched_next_check = 0.0
        self._idle_delay: float | None = None
        self._idle_delay_at = 0.0
        self._idle_dirty = False

    def run(self, max_jobs: int | None = None):
        """Run worker loop: claim jobs, fork children, monitor, heartbeat.

        Args:
            max_jobs: Stop after processing this many jobs (None or 0 = unlimited).
        """
        # import sys  # moved to top-level

        # SEC-04 (W3): production-shaped + unset = one WARNING per worker run.
        # Pinned to first line BEFORE the fork loop so it fires exactly once
        # per WorkerProcess.run, never inside forked children.
        warn_if_unconfigured(get_config("ALLOWED_TASK_MODULES", None))

        # Refuse to run without the attempt table: completions could not be fenced.
        require_attempt_table(self.backend)
        # ... and without the ready-queue tables there would be nothing to claim from.
        require_ready_tables(self.backend)
        # ... and without the history table a finished job could not be recorded.
        require_history_table(self.backend)

        logger.info(
            f"Worker {self.worker_id} starting (queues={self.queues}, poll={self.poll_interval}s, "
            f"pool={self.pool_settings.pool})"
        )

        # Configure DB resilience (WAL mode, busy_timeout, statement_timeout, etc.)
        # from sqlery.core.db_resilience import configure_connection_resilience  # moved to top-level
        configure_connection_resilience()

        # Signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Worker {self.worker_id} received signal {signum}, shutting down...")
            self.shutdown_requested = True
            # If we have a running child, forward the signal to its process group
            if self.child_pid:
                # # Old: killed only the child
                # try:
                #     os.kill(self.child_pid, signal.SIGTERM)
                # except OSError:
                #     pass
                try:
                    os.killpg(os.getpgid(self.child_pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        # SIGUSR1 handler for heartbeat — just sets a flag, no DB call
        # (DB calls in signal handlers corrupt psycopg connections if the
        # main thread is mid-query)
        def heartbeat_signal_handler(signum, frame):
            self._heartbeat_due = True

        signal.signal(signal.SIGUSR1, heartbeat_signal_handler)

        # Clean up stale worker row from previous crash
        try:
            self.backend.delete_worker_registration(self.worker_id)
        except Exception:
            pass

        self._heartbeat('idle')

        # --- Scheduler-election lifecycle (ported from DaemonManager.run) ---
        # A bare sqlery-worker self-elects as scheduler-leader per queue using
        # its own identity. The per-queue lease primitive (Phase 8) skips live
        # foreign leases, so a running daemon stays authoritative (ELECT-05).
        # owned_queues is defined BEFORE the try: so the finally: block can
        # always release it, even if the worker crashes before the loop.
        scheduler = Scheduler(backend=self.backend)
        # TTL mirrors the daemon's check_interval * 3 (≈30s) — failover within
        # one TTL once a dead leader's lease expires (ELECT-06).
        lease_secs = self.poll_interval * 3
        # WR-01: mirror the TTL onto the instance so _fork_and_execute can renew
        # held leases during a long blocking job (otherwise the lease expires
        # mid-job and another worker takes over scheduling — leadership flap).
        self._lease_secs = lease_secs
        tick_interval = self._tick_interval(lease_secs)
        try:
            owned_queues = set(
                self.backend.claim_queue_leases(
                    self.queues, self.worker_id, self.node_id, self.pid, lease_secs
                )
            )
        except Exception as e:
            # Election must never prevent the worker from starting — a worker
            # that can't elect still claims and executes jobs (ELECT-07).
            logger.error(f"Initial scheduler-lease claim failed: {e}", exc_info=True)
            owned_queues = set()
        # WR-01: keep the instance view of held queues in sync with the local.
        self._owned_queues = owned_queues
        self._last_lease_renew = time.monotonic()
        logger.info(
            f"Worker {self.worker_id} scheduler responsibility: "
            f"{sorted(owned_queues) or 'none yet'}"
        )

        # Phase 18: open LISTEN connection (no-op when SQLERY_PG_NOTIFY=False or SQLite).
        self._open_listen_conn()
        # FORK-SAFETY (#117): the LISTEN connection stays open across forks, so a NOTIFY
        # that lands while a job runs is not lost and each job costs no reconnect. The
        # child gets an inherited copy of the socket, so it must never close or finalise
        # it (that would send Terminate and kill the parent's session).
        # Old: self._fork_ctx.register_pre_fork(self._close_listen_conn)
        # Old: self._fork_ctx.register_post_fork_parent(self._open_listen_conn)
        self._fork_ctx.register_post_fork_child(self._orphan_listen_conn)

        try:
            while not self.shutdown_requested:
                if max_jobs and self.jobs_processed >= max_jobs:
                    break
                try:
                    # Prune connections that exceeded CONN_MAX_AGE (like Django request cycle)
                    # from django.db import close_old_connections  # moved to top-level (try/except)
                    if close_old_connections is not None:
                        try:
                            close_old_connections()
                        except Exception:
                            pass

                    self._last_loop_time = time.monotonic()
                    self._check_heartbeat()

                    # --- Idle tick (#110): scheduler election, due-task lookup, heartbeat
                    # and next-delayed-job lookup. Runs at most every tick_interval, and
                    # where the backend supports it (Django) the tick, the lost-retry sweep
                    # and the claim attempt share ONE transaction, so an idle poll is a
                    # single commit. The tick has its own try/except (and savepoint) so an
                    # error logs and the loop continues (ELECT-07 safe-degradation).
                    ticked = False
                    pending: dict = {}
                    with self.backend.idle_transaction():
                        if self._tick_due(tick_interval):
                            ticked = True
                            pending["_last_tick"] = time.monotonic()
                            self._idle_tick(scheduler, lease_secs, pending)

                        self._maybe_recover_lost_retries()

                        # Own savepoint: a failed claim must not roll back the lease
                        # renewal and heartbeat above; it is re-raised after the commit.
                        claim_error = None
                        job = None
                        try:
                            with self.backend.idle_transaction():
                                job = self.backend.claim_job(self.queues, self.worker_id)
                        except Exception as e:
                            claim_error = e
                    # Bookkeeping that says "the lease was renewed / the check ran" is applied
                    # only once the shared transaction has committed: if it rolled back (an
                    # exception above), the worker must not believe the work was done.
                    for attr, value in pending.items():
                        setattr(self, attr, value)
                    if "_owned_queues" in pending:
                        owned_queues = self._owned_queues
                    if claim_error is not None:
                        raise claim_error

                    if not job:
                        if self._idle_dirty and not ticked:
                            # A job just ran: flip the heartbeat back to idle and refresh
                            # the delayed-job lookup the job may have changed.
                            try:
                                with self.backend.idle_transaction():
                                    self._idle_housekeeping()
                            except Exception as e:
                                logger.warning(f"Idle housekeeping failed: {e}")
                        logger.info(".")
                        # elapsed = 0
                        # while elapsed < self.poll_interval and not self.shutdown_requested:
                        #     time.sleep(1)
                        #     elapsed += 1
                        #     self._check_heartbeat()
                        self._wait_for_notify()
                        continue

                    # Check concurrency
                    if not self.executor.can_execute_job(job):
                        logger.info(
                            f"Worker {self.worker_id}: Job {job.id} blocked by running job "
                            f"(allow_parallel={job.allow_parallel}) - releasing"
                        )
                        self.backend.release_job(job.id)
                        self._heartbeat('idle')
                        # Sleep before retrying — without this the worker spins
                        # in a tight loop claiming and releasing the same job
                        # when a zombie running job blocks the queue.
                        # elapsed = 0
                        # while elapsed < self.poll_interval and not self.shutdown_requested:
                        #     time.sleep(1)
                        #     elapsed += 1
                        #     self._check_heartbeat()
                        self._wait_for_notify()
                        continue

                    # Fork and execute
                    sys.stderr.write(f"\n[CLAIMED] Job #{job.id} ({job.task_path})\n")
                    sys.stderr.flush()
                    logger.info(f"Worker {self.worker_id}: Claimed job {job.id} ({job.task_path})")
                    self.current_job = job

                    try:
                        job_start = time.monotonic()
                        # Old: result = self._fork_and_execute(job)
                        result = self._execute_claimed(job)
                        self.total_busy_seconds += time.monotonic() - job_start
                        self.jobs_processed += 1
                        # Prefork parent: refresh its DB connection at the recycle rate.
                        if self.pool_settings.pool == POOL_PREFORK and db_refresh_due(
                            self.jobs_processed, self.pool_settings.db_reuse_max
                        ):
                            refresh_db_connections(force=True)
                        status = 'SUCCESS' if result.get('success') else 'FAILED'
                        sys.stderr.write(f"[{status}] Job #{job.id}\n")
                        sys.stderr.flush()
                        logger.info(f"Worker {self.worker_id}: Completed job {job.id} -> {status.lower()}")
                    finally:
                        self.current_job = None
                        self.child_pid = None
                        self._idle_dirty = True

                    # Don't heartbeat idle between jobs — the next loop iteration
                    # will either claim a new job immediately or heartbeat idle
                    # when entering the poll sleep (line 418). This avoids brief
                    # idle flashes on the dashboard when jobs are back-to-back.
                    # self._heartbeat('idle')

                except KeyboardInterrupt:
                    break
                except Exception as e:
                    if is_connection_lost(e):
                        # Stop claiming until the database answers again (#161).
                        self._wait_for_database(e)
                        continue
                    logger.exception(f"Worker {self.worker_id}: Unhandled error in main loop")
                    # Reset DB connections in case they're corrupted
                    self.executor._reset_db_connections()
                    time.sleep(self.poll_interval)
                    continue

        except KeyboardInterrupt:
            logger.info(f"Worker {self.worker_id} interrupted")
        finally:
            self._stop_pool_child()
            # Phase 18: close LISTEN connection on graceful shutdown.
            self._close_listen_conn()
            try:
                self.backend.update_worker_heartbeat(
                    worker_id=self.worker_id,
                    status='dead',
                    current_job_id=None,
                    jobs_processed=self.jobs_processed,
                )
            except Exception:
                pass
            # Release held scheduler leases on graceful shutdown so another
            # worker/daemon can take over the queues immediately (ELECT-03).
            # SIGTERM/SIGINT set self.shutdown_requested, exiting the loop into
            # this finally; owned_queues is always defined (init'd before try:).
            try:
                self.backend.release_queue_leases(sorted(owned_queues), self.worker_id)
            except Exception as e:
                logger.error(f"Error releasing scheduler leases: {e}")
            logger.info(f"Worker {self.worker_id} stopped (processed {self.jobs_processed} jobs)")

        return self.jobs_processed

    # Reconnect attempts made for an outcome after shutdown was requested.
    SHUTDOWN_RECONNECT_ATTEMPTS = 3
    # How long shutdown waits for an idle prefork child to exit before killing it.
    POOL_CHILD_STOP_SECONDS = 2.0  # under WorkerPoolManager's 5 s SIGTERM -> SIGKILL
    # Parent wait-loop tick bounds (seconds) in _fork_and_execute.
    WAIT_TICK_MIN = 0.005
    WAIT_TICK_MAX = 0.5
    # A wait shorter than this cannot have staled the post-fork connection.
    FRESH_CONN_WAIT_SECONDS = 1.0

    def _maybe_recover_lost_retries(self):
        """Throttled sweep for retries lost to a crash between failing a job and retrying it."""
        try:
            interval = float(get_config("RETRY_RECOVERY_INTERVAL", 30) or 0)
        except (TypeError, ValueError):
            interval = 30.0
        now = time.monotonic()
        if interval <= 0 or now - getattr(self, "_last_retry_recovery", -interval) < interval:
            return
        self._last_retry_recovery = now
        # Own savepoint: recover_lost_retries swallows its own errors, which would
        # otherwise leave the shared idle transaction aborted for the claim after it.
        with self.backend.idle_transaction():
            self.executor.recover_lost_retries()

    def _fork_and_execute(self, job) -> dict:
        """Fork a child process to execute the job.

        Parent waits for child via waitpid, enforces two-layer timeout,
        reads final result from DB. The only pipe carries an outcome the child could not
        persist because its DB connection was lost (#161).
        Returns dict with 'success', 'output'/'error'/'traceback'.
        """
        job_id = job.id

        # The child sends an outcome it could not persist (DB connection lost) here.
        outcome_r, outcome_w = os.pipe()
        child_pid = self._fork_ctx.fork()

        if child_pid == 0:
            # === CHILD PROCESS ===
            try:
                os.close(outcome_r)
                self.executor.outcome_fd = outcome_w
                try:
                    os.setpgrp()
                except OSError:
                    pass
                self.executor.execute_job_in_child(job)
            # Old: except Exception:
            # BaseException: a SystemExit raised by the task must not unwind into the
            # parent's worker loop inside the child (it would run its cleanup on state
            # inherited from the parent).
            except BaseException:
                os._exit(1)
            os._exit(0)

        # === PARENT PROCESS ===
        os.close(outcome_w)
        logger.info(f"Forked child PID {child_pid} for job {job_id}")

        def poll():
            self._receive_handed_over(outcome_r)
            done, exit_code = self._poll_forked_child(child_pid)
            if done:
                self._receive_handed_over(outcome_r)  # written just before the child exited
            return done, exit_code

        try:
            return self._await_child(job, child_pid, poll)
        finally:
            os.close(outcome_r)

    def _receive_handed_over(self, fd: int) -> None:
        """Fork pool: pick up an outcome the child handed over, if one is waiting."""
        readable, _, _ = select.select([fd], [], [], 0)
        if readable:
            outcome = read_frame(fd)  # None at EOF (the child exited without one)
            if outcome is not None:
                self._handed_over = outcome

    @staticmethod
    def _poll_forked_child(child_pid: int) -> tuple[bool, int | None]:
        """Fork pool: the job is over once its child has exited; returns (done, exit code)."""
        try:
            pid, status = os.waitpid(child_pid, os.WNOHANG)
        except ChildProcessError:
            return True, 1  # child already reaped
        if pid != 0:
            return True, os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        return False, None

    def _await_child(self, job, child_pid: int, poll) -> dict:
        """Wait for ``child_pid`` to finish ``job`` while heartbeating, then read the outcome.

        ``poll()`` returns ``(done, exit_code)``. Enforces the parent safety-net timeout and
        revoke requests by killing the child's process group. Shared by both pools.
        """
        timeout = job.timeout_seconds or get_config('DEFAULT_TIMEOUT_SECONDS', 600)
        job_id = job.id
        self.child_pid = child_pid
        self._handed_over = None

        try:
            self.backend.update_job_child_pid(
                job_id, child_pid, **attempt_kwargs(job, self.backend.update_job_child_pid)
            )
        except Exception:
            pass

        # Update heartbeat with child info
        self._heartbeat('busy', job_id=job_id)

        # Two-layer timeout: child SIGALRM fires at `timeout`, parent
        # force-kills at `timeout + 60s` as safety net (like RQ).
        parent_timeout = (timeout + 60) if timeout else None

        # Wait for child with timeout
        start_time = time.monotonic()
        child_exited = False
        exit_status = None
        # Heartbeat-driven job lease renewal: a bare `sqlery-worker` (no daemon)
        # never receives SIGUSR1, so _check_heartbeat()'s deferred heartbeat
        # never fires and worker_last_heartbeat stays frozen at job-claim time
        # for the whole job. The daemon's zombie detector (Check 5, ~3x
        # WORKER_ALIVE_TIMEOUT) then treats a merely-slow job as a dead worker
        # and fails/requeues it while it's still genuinely running — the
        # classic false-positive lease expiry. Send a real heartbeat here on
        # every tick of actual observed liveness (this loop only runs while
        # the parent is alive and polling waitpid) instead of relying on a
        # fixed poll_interval*3 window with no relation to job runtime.
        last_lease_heartbeat = time.monotonic()
        # Revoke polling: a flag set by revoke(job_id, terminate=True), possibly from another
        # host, is noticed here and the child's process group is killed.
        try:
            revoke_poll = float(get_config("REVOKE_POLL_INTERVAL", 2.0) or 0)
        except (TypeError, ValueError):
            revoke_poll = 2.0
        last_revoke_check = time.monotonic()
        # The wait starts with a few-ms sleep and backs off (doubling) to
        # WAIT_TICK_MAX so trivial jobs are reaped promptly; the per-tick DB
        # duties below run once the accumulated sleep reaches WAIT_TICK_MAX (the
        # old 0.5 s cadence). Because 5+10+...+320 ms is the first total past
        # 0.5 s, the first DB pass lands ~0.635 s into a job.
        wait_sleep = self.WAIT_TICK_MIN
        slept_since_duties = 0.0

        while not child_exited:
            # Old: inline os.waitpid(child_pid, os.WNOHANG); now per-pool (_poll_forked_child)
            child_exited, exit_status = poll()
            if child_exited:
                break

            # Check timeout — parent safety net fires after timeout + 60s
            elapsed = time.monotonic() - start_time
            # if timeout and elapsed > timeout:
            if parent_timeout and elapsed > parent_timeout:
                logger.warning(f"Job {job_id} timed out after {int(elapsed)}s (parent safety net), killing child PID {child_pid}")
                self._kill_child(child_pid)
                # # Pipe close removed
                # os.close(read_fd)
                try:
                    self.backend.mark_job_failed(
                        job_id,
                        error=f"Job {job_id} exceeded timeout of {timeout} seconds (parent safety net at {int(elapsed)}s)",
                        traceback=f"Parent killed child PID {child_pid} after {int(elapsed)}s",
                        **attempt_kwargs(job, self.backend.mark_job_failed),
                    )
                except Exception:
                    pass
                return {'success': False, 'error': f'Timeout after {int(elapsed)}s'}

            # Sleep briefly so parent stays responsive to signals
            time.sleep(wait_sleep)
            slept_since_duties += wait_sleep
            wait_sleep = min(wait_sleep * 2, self.WAIT_TICK_MAX)
            self._check_heartbeat()
            if slept_since_duties < self.WAIT_TICK_MAX:
                continue  # fast early tick: just re-poll waitpid
            slept_since_duties = 0.0
            if revoke_poll > 0 and time.monotonic() - last_revoke_check >= revoke_poll:
                last_revoke_check = time.monotonic()
                try:
                    revoked = self.backend.is_job_revoke_requested(job_id)
                except Exception as e:  # never let a poll error abort the wait loop
                    logger.warning(f"Revoke poll for job {job_id} failed: {e}")
                    revoked = False
                if revoked:
                    logger.warning(f"Job {job_id} revoked, killing child PID {child_pid}")
                    self._kill_child(child_pid)
                    try:
                        self.backend.mark_job_failed(
                            job_id,
                            error=REVOKED_ERROR,
                            traceback=f"Child PID {child_pid} terminated on revoke request",
                            **attempt_kwargs(job, self.backend.mark_job_failed),
                        )
                    except Exception as e:
                        logger.error(f"Failed to mark revoked job {job_id}: {e}")
                    return {'success': False, 'error': REVOKED_ERROR}
            # Send an unconditional liveness heartbeat every heartbeat_interval
            # (not gated on SIGUSR1/_heartbeat_due — see comment above the loop
            # init) so the job's effective lease renews at the worker's actual
            # heartbeat cadence instead of expiring on a fixed poll_interval*3
            # window unrelated to how long the job runs.
            now = time.monotonic()
            if now - last_lease_heartbeat >= self.heartbeat_interval:
                self._heartbeat('busy', job_id=job_id)
                last_lease_heartbeat = now
            # WR-01: keep scheduler leadership alive across long jobs. The main
            # loop only renews leases at the top of each iteration, but this
            # wait blocks for up to (timeout + 60s); without renewal here the
            # lease (poll_interval*3) expires mid-job and another worker takes
            # over scheduling (two-leader overlap). Guarded so a renew error
            # never aborts the wait — election must never crash job execution.
            try:
                if self._owned_queues:
                    self.backend.renew_queue_leases(
                        sorted(self._owned_queues), self.worker_id, self._lease_secs
                    )
            except Exception as e:
                logger.warning(f"Lease renew during job execution failed: {e}")

        # # Pipe read removed — read result from DB instead
        # try:
        #     result_data = b''
        #     while True:
        #         chunk = os.read(read_fd, 4096)
        #         if not chunk:
        #             break
        #         result_data += chunk
        #     os.close(read_fd)
        #
        # #CLEANUP 2026-05-14: dead code below — Remove after 2027-05-14.
        #     if result_data:
        #         return json.loads(result_data.decode())
        # except Exception as e:
        #     logger.warning(f"Failed to read result from child for job {job.id}: {e}")

        # Reconnect DB (may be stale after waiting). A child that finished almost
        # at once leaves the connection opened right after the fork fresh, so the
        # close+reopen is skipped for it (#127); a read error still resets and retries.
        # Old: self.executor._reset_db_connections()
        # Old: refreshed = self.backend.get_job_by_id(job_id)
        # The prefork parent keeps its connection across jobs: it is refreshed at the
        # recycle rate (WORKER_DB_REUSE_MAX) instead.
        if (
            self.pool_settings.pool == POOL_FORK
            and time.monotonic() - start_time >= self.FRESH_CONN_WAIT_SECONDS
        ):
            self.executor._reset_db_connections()
        # The child lost its DB connection while recording the outcome: the supervisor
        # reconnects and persists it. The job itself is never run again for this.
        handed_over, self._handed_over = self._handed_over, None
        if handed_over is not None:
            self._with_reconnect(
                lambda: self.executor.persist_outcome(handed_over), f"persisting job {job_id}"
            )

        def read_result():
            try:
                return self.backend.get_job_by_id(job_id)
            except Exception as e:
                if is_connection_lost(e):
                    raise  # _with_reconnect backs off and reconnects
                logger.warning(f"Reading job {job_id} result failed ({e}); reconnecting")
                self.executor._reset_db_connections()
                return self.backend.get_job_by_id(job_id)

        # Old: refreshed = self.backend.get_job_by_id(job_id), reset + one retry on error
        refreshed = self._with_reconnect(read_result, f"reading job {job_id} result")
        if refreshed and refreshed.status == 'success':
            return {'success': True, 'output': refreshed.output or ''}
        elif refreshed and refreshed.status in ('failed', 'archived'):
            # 'archived': the child failed, queued a retry and archived the original;
            # that outcome is already recorded, so don't overwrite it as a crash.
            return {'success': False, 'error': refreshed.error or ''}
        elif exit_status == 0:
            return {'success': True, 'output': ''}
        else:
            # Child crashed — mark failed if not already marked by child
            try:
                self.backend.mark_job_failed(
                    job_id,
                    error=f"Child process exited with status {exit_status}",
                    traceback=f"Child PID {child_pid} exited abnormally",
                    **attempt_kwargs(job, self.backend.mark_job_failed),
                )
            except Exception as e:
                logger.error(f"Failed to mark crashed job {job_id}: {e}")
            return {'success': False, 'error': f'Child exited with status {exit_status}'}

    def _wait_for_database(self, error: BaseException) -> bool:
        """Database unreachable: claim nothing, reconnect with backoff and jitter until it is.

        Every attempt discards the old connections entirely and probes with a new one.
        Returns False when shutdown was requested first.
        """
        logger.error(
            f"Worker {self.worker_id}: database connection lost ({error}); "
            f"not claiming new jobs until it is back"
        )
        base, cap = reconnect_backoff_settings()
        attempt = 0
        while not self.shutdown_requested:
            discard_connections()
            self._sleep_unless_shutdown(reconnect_delay(attempt, base, cap))
            attempt += 1
            if self.shutdown_requested:
                break
            try:
                probe_database()
            except Exception as e:
                logger.warning(f"Worker {self.worker_id}: reconnect attempt {attempt} failed: {e}")
                continue
            logger.info(f"Worker {self.worker_id}: database reachable again, resuming claims")
            return True
        return False

    def _with_reconnect(self, fn, what: str):
        """Run ``fn``; on a lost connection, discard it, back off with jitter and retry.

        Other errors propagate. After shutdown is requested it gives up after
        SHUTDOWN_RECONNECT_ATTEMPTS more tries (the job's attempt then stays open and
        recovery settles it).
        """
        base, cap = reconnect_backoff_settings()
        attempt = 0
        while True:
            try:
                return fn()
            except Exception as e:
                if not is_connection_lost(e):
                    raise
                if self.shutdown_requested and attempt >= self.SHUTDOWN_RECONNECT_ATTEMPTS:
                    logger.error(f"Worker {self.worker_id}: gave up {what} on shutdown: {e}")
                    raise
                delay = reconnect_delay(attempt, base, cap)
                attempt += 1
                logger.warning(
                    f"Worker {self.worker_id}: DB connection lost while {what} ({e}); "
                    f"reconnecting in {delay:.2f}s (attempt {attempt})"
                )
                discard_connections()
                time.sleep(delay)

    def _sleep_unless_shutdown(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self.shutdown_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.2))
            self._check_heartbeat()

    def _execute_claimed(self, job) -> dict:
        """Run a claimed job in the configured pool (WORKER_POOL, #161)."""
        if self.pool_settings.pool == POOL_PREFORK:
            return self._prefork_execute(job)
        return self._fork_and_execute(job)

    def _prefork_execute(self, job) -> dict:
        """Hand ``job`` to the long-lived pool child, starting one if needed, and wait for it.

        A job that cannot be pickled runs in a per-job fork instead.
        """
        try:
            payload = encode_frame(job)
        except Exception as e:
            logger.warning(f"Job {job.id} cannot be sent to the prefork child ({e}); forking it")
            return self._fork_and_execute(job)
        pool = self._ensure_pool_child()
        try:
            write_all(pool.job_fd, payload)
        except BrokenPipeError:
            # The idle child died (e.g. killed from outside): start a fresh one, once.
            self._stop_pool_child()
            pool = self._ensure_pool_child()
            write_all(pool.job_fd, payload)
        logger.info(f"Sent job {job.id} to prefork child PID {pool.pid}")
        try:
            return self._await_child(job, pool.pid, lambda: self._poll_pool_child(pool))
        finally:
            if pool.recycling or not self._pool_child_alive(pool):
                self._stop_pool_child()

    def _poll_pool_child(self, pool: "_PoolChild") -> tuple[bool, int | None]:
        """Prefork pool: the job is over once the child reports it, or once the child dies."""
        readable, _, _ = select.select([pool.result_fd], [], [], 0)
        if readable:
            report = read_frame(pool.result_fd)  # None at EOF: the child died
            if report is not None:
                exit_code, pool.recycling, self._handed_over = report
                return True, exit_code
        done, exit_code = self._poll_forked_child(pool.pid)
        if done:
            pool.reaped = True
        return done, exit_code

    @staticmethod
    def _pool_child_alive(pool: "_PoolChild") -> bool:
        if pool.reaped:
            return False
        try:
            pid, _ = os.waitpid(pool.pid, os.WNOHANG)
        except ChildProcessError:
            pid = pool.pid  # already reaped, e.g. by _kill_child
        if pid != 0:
            pool.reaped = True
        return not pool.reaped

    def _ensure_pool_child(self) -> "_PoolChild":
        """The running prefork child, forking a new one when there is none.

        Forking runs the fork-safety hooks, so the parent's DB connections are also
        refreshed whenever the child is recycled.
        """
        if self._pool_child is not None and self._pool_child_alive(self._pool_child):
            return self._pool_child
        self._stop_pool_child()
        job_r, job_w = os.pipe()
        result_r, result_w = os.pipe()
        child_pid = self._fork_ctx.fork()
        if child_pid == 0:
            # === PREFORK CHILD ===
            exit_code = 1
            try:
                os.close(job_w)
                os.close(result_r)
                exit_code = self._pool_child_main(job_r, result_w)
            except BaseException:
                pass  # e.g. a task's SystemExit: leave, the parent sees a crash
            os._exit(exit_code)
        os.close(job_r)
        os.close(result_w)
        self._pool_child = _PoolChild(pid=child_pid, job_fd=job_w, result_fd=result_r)
        logger.info(f"Worker {self.worker_id}: started prefork child PID {child_pid}")
        return self._pool_child

    def _pool_child_main(self, job_fd: int, result_fd: int) -> int:
        """Prefork child loop: run each job the parent sends until recycled or told to stop.

        Each report to the parent is a frame ``(exit code, recycle, unpersisted)``: whether
        this child exits now (WORKER_MAX_TASKS_PER_CHILD / WORKER_MAX_MEMORY_PER_CHILD
        reached), and an outcome it could not write because its DB connection was lost.
        """
        try:
            os.setpgrp()  # timeout/revoke kill this group: the child and the job's subprocesses
        except OSError:
            pass
        # The parent's handlers came through the fork; an idle child just dies on a signal.
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            signal.signal(signum, signal.SIG_DFL)
        settings = self.pool_settings
        jobs_done = 0
        while True:
            job = read_frame(job_fd)
            if job is None:
                return 0  # the parent closed the pipe: shutdown
            # Like Celery's task_prerun: drop a connection that went stale while idle.
            refresh_db_connections(force=False)
            exit_code = self.executor.run_job_in_child(job)
            jobs_done += 1
            # Like Celery: the job always completes, then the limits decide.
            used_kib = max_rss_kib() if settings.max_memory_per_child else None
            if settings.max_memory_per_child and not used_kib:
                logger.error("Prefork child unable to determine memory usage")
            recycle = should_recycle(
                jobs_done, settings.max_tasks_per_child, used_kib, settings.max_memory_per_child
            )
            limit_kib = settings.max_memory_per_child
            if recycle and used_kib and limit_kib and used_kib > limit_kib:
                logger.warning(
                    f"Prefork child PID {os.getpid()} exiting after exceeding memory limit "
                    f"({used_kib}KiB / {limit_kib}KiB)"
                )
            elif recycle:
                logger.info(
                    f"Prefork child PID {os.getpid()} exiting after {jobs_done} jobs "
                    f"(WORKER_MAX_TASKS_PER_CHILD)"
                )
            if not recycle:
                refresh_db_connections(force=db_refresh_due(jobs_done, settings.db_reuse_max))
            write_all(result_fd, encode_frame((exit_code, recycle, self.executor.unpersisted)))
            if recycle:
                return 0

    def _stop_pool_child(self) -> None:
        """Close the pipes to the prefork child; an idle child then exits. Reaps it."""
        pool, self._pool_child = self._pool_child, None
        if pool is None:
            return
        for fd in (pool.job_fd, pool.result_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        if pool.reaped:
            return
        deadline = time.monotonic() + self.POOL_CHILD_STOP_SECONDS
        while time.monotonic() < deadline:
            if not self._pool_child_alive(pool):
                return
            time.sleep(0.01)
        logger.warning(f"Prefork child PID {pool.pid} did not exit; killing it")
        self._kill_child(pool.pid)

    def _kill_child(self, pid: int):
        """Kill a child process group: SIGTERM, wait, then SIGKILL.

        Uses os.killpg() to kill the entire process group so subprocesses
        spawned by the job are also terminated (like RQ).
        """
        # # Old: killed only the child, leaving orphan subprocesses
        # try:
        #     os.kill(pid, signal.SIGTERM)
        # except OSError:
        #     return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return

        for _ in range(10):
            time.sleep(0.5)
            try:
                os.waitpid(pid, os.WNOHANG)
                os.kill(pid, 0)
            except OSError:
                return

        # # Old: killed only the child
        # try:
        #     os.kill(pid, signal.SIGKILL)
        #     os.waitpid(pid, 0)
        # except OSError:
        #     pass
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            os.waitpid(pid, 0)
        except (OSError, ProcessLookupError):
            pass

    def _open_listen_conn(self) -> None:
        """Open a dedicated AUTOCOMMIT psycopg3 connection and LISTEN on all queue channels.

        No-op when SQLERY_PG_NOTIFY is False, when on SQLite, or when psycopg3
        is unavailable. Any failure is caught and logged — the worker falls back
        to pure polling. Never called from a signal handler.
        """
        if not get_config('SQLERY_PG_NOTIFY', False):
            return
        if not _psycopg_available or sanitize_queue_name_to_channel is None:
            return
        # WR-01: close any existing LISTEN connection before reopening so a
        # reopen never leaks the previous connection.
        if self._listen_conn is not None:
            try:
                self._listen_conn.close()
            except Exception:
                pass
            self._listen_conn = None
        try:
            # Detect PG DSN: standalone mode exposes DATABASE_URL via get_config;
            # Django mode reads from connections['default'].settings_dict.
            database_url = get_config('DATABASE_URL', None)
            if database_url:
                if 'postgresql' not in database_url and 'postgres' not in database_url:
                    return
                dsn = database_url
            elif connections is not None:
                # Django mode: build DSN from connection settings dict.
                try:
                    db_settings = connections['default'].settings_dict
                except Exception:
                    return
                if db_settings.get('ENGINE', '').find('postgresql') == -1 and \
                        db_settings.get('ENGINE', '').find('psycopg') == -1:
                    return
                dsn = _psycopg.conninfo.make_conninfo(
                    dbname=db_settings.get('NAME', ''),
                    host=db_settings.get('HOST', '') or None,
                    port=db_settings.get('PORT', '') or None,
                    user=db_settings.get('USER', '') or None,
                    password=db_settings.get('PASSWORD', '') or None,
                )
            else:
                return

            self._listen_conn = _psycopg.connect(dsn, autocommit=True)
            channels = []
            for queue in self.queues:
                channel = sanitize_queue_name_to_channel(queue)
                self._listen_conn.execute(
                    _psycopg_sql.SQL("LISTEN {}").format(
                        _psycopg_sql.Identifier(channel)
                    )
                )
                channels.append(channel)
            logger.info(
                f"Worker {self.worker_id}: LISTEN connection open on channels {channels}"
            )
        except Exception as e:
            logger.warning(
                f"Worker {self.worker_id}: failed to open LISTEN connection: {e}; "
                f"falling back to polling"
            )
            # WR-01: close the orphaned connection (opened above) before
            # clearing the reference, otherwise it leaks until GC/idle timeout.
            # Old: self._listen_conn = None
            if self._listen_conn is not None:
                try:
                    self._listen_conn.close()
                except Exception:
                    pass
            self._listen_conn = None

    def _orphan_listen_conn(self) -> None:
        """Forked child only: disown the inherited LISTEN connection WITHOUT closing it.

        The child shares the parent's socket. Closing the psycopg connection (or letting
        it be garbage-collected, which runs PQfinish) would send a Terminate message on
        that shared socket and end the parent's LISTEN session. So the child only drops
        its own attribute (nothing in the child can reach it) and parks the object in a
        module-level list so it is never finalised; the child leaves via ``os._exit``.
        """
        conn = self._listen_conn
        if conn is None:
            return
        _ORPHANED_LISTEN_CONNS.append(conn)
        self._listen_conn = None

    def _close_listen_conn(self) -> None:
        """Close the LISTEN connection. Safe to call when connection is None.

        Registered as a pre_fork hook on ForkSafeExecutor so it runs before
        every os.fork(), ensuring the child never inherits the LISTEN connection.
        Also called in run() finally on graceful shutdown.
        """
        if self._listen_conn is None:
            return
        try:
            self._listen_conn.close()
        except Exception:
            pass
        self._listen_conn = None
        logger.debug(f"Worker {self.worker_id}: LISTEN connection closed")

    _LISTEN_RETRY_SECONDS = 30.0

    def _lazy_open_listen_conn(self) -> bool:
        """Open the LISTEN connection on demand; True only if it is open afterwards.

        No-op unless SQLERY_PG_NOTIFY is on. A failed attempt is not retried for
        ``_LISTEN_RETRY_SECONDS`` so a down or non-PG database costs one try, not one
        per idle pass.
        """
        if not get_config('SQLERY_PG_NOTIFY', False):
            return False
        now = time.monotonic()
        if now < getattr(self, '_listen_retry_at', 0.0):
            return False
        self._open_listen_conn()
        if self._listen_conn is None:
            self._listen_retry_at = now + self._LISTEN_RETRY_SECONDS
            return False
        return True

    def _wait_for_notify(self) -> None:
        """Block up to poll_interval for a NOTIFY, in <=1s slices.

        Falls back to plain time.sleep slices if no LISTEN connection is open
        (flag-off path, SQLite, or after a LISTEN connection error). Heartbeat
        is checked between slices so the worker stays responsive to SIGUSR1.

        Mirrors the pgwq reference Worker._wait() pattern exactly.

        The wait is capped at the earliest not-yet-due job (a retry's backoff, a
        delayed enqueue), so such a job starts when due, not a poll interval later.
        """
        timeout = self._idle_timeout()
        if self._listen_conn is None and self._lazy_open_listen_conn():
            # Just subscribed: return so the loop re-claims. A job enqueued before the
            # LISTEN took effect is found by that claim; one after it is NOTIFYed.
            return
        if self._listen_conn is None:
            # Flag-off / SQLite / no connection — original 1s-slice sleep loop
            end = time.monotonic() + timeout
            while not self.shutdown_requested:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return
                time.sleep(min(1.0, remaining))
                self._check_heartbeat()
            return
        # Flag-on + PG: wait for NOTIFY with the idle timeout, in 1s slices
        end = time.monotonic() + timeout
        while not self.shutdown_requested:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            try:
                for _ in self._listen_conn.notifies(
                    timeout=min(remaining, 1.0), stop_after=1
                ):
                    return  # NOTIFY received — wake up to claim immediately
            except Exception as e:
                logger.warning(
                    f"Worker {self.worker_id}: LISTEN connection error: {e}; "
                    f"closing, falling back to polling"
                )
                self._close_listen_conn()
                return
            self._check_heartbeat()
            if self.shutdown_requested:
                return

    def _idle_timeout(self) -> float:
        """poll_interval, shortened to the earliest queued job that is not due yet.

        Uses the delayed-job lookup cached by the last idle tick (#110) rather than
        querying on every poll; a job enqueued since then is seen by the next claim.
        """
        if self._idle_delay_at == 0.0:  # no tick has looked yet
            self._refresh_idle_delay()
        delay = self._idle_delay
        if delay is None:
            return self.poll_interval
        delay -= time.monotonic() - self._idle_delay_at
        return min(self.poll_interval, max(delay + 0.05, 0.1))

    def _tick_interval(self, lease_secs: float) -> float:
        """Minimum seconds between idle ticks.

        A tick renews the scheduler lease and sends the heartbeat, so it must run at
        least every half lease TTL and at least every WORKER_HEARTBEAT_INTERVAL (both
        far below the WORKER_ALIVE_TIMEOUT / 60s staleness thresholds).
        """
        interval = lease_secs / 2
        if self.heartbeat_interval and self.heartbeat_interval > 0:
            interval = min(interval, self.heartbeat_interval)
        return max(interval, 0.1)

    def _tick_due(self, tick_interval: float) -> bool:
        if self._last_tick is None:
            return True
        # 10% slack: polls are at least poll_interval apart, never meaningfully less.
        return time.monotonic() - self._last_tick >= tick_interval * 0.9

    def _idle_housekeeping(self) -> None:
        """Idle heartbeat plus the delayed-job lookup that caps the idle wait."""
        self._heartbeat('idle')
        self._idle_dirty = False
        self._refresh_idle_delay()

    def _refresh_idle_delay(self) -> None:
        try:
            with self.backend.idle_transaction():  # own savepoint, see _heartbeat
                self._idle_delay = self.backend.next_due_delay(self.queues)
        except Exception as e:  # best-effort: fall back to the plain poll interval
            logger.debug(f"Worker {self.worker_id}: next_due_delay failed: {e}")
            self._idle_delay = None
        self._idle_delay_at = time.monotonic()

    def _idle_tick(self, scheduler, lease_secs: float, pending: dict) -> None:
        """Scheduler election, due-task lookup, heartbeat and delayed-job lookup.

        Each step runs in its own savepoint and logs its own failure, so an error in one
        (which aborts only that savepoint) neither undoes the others nor poisons the shared
        transaction (ELECT-07 safe-degradation). State describing completed work is
        written to ``pending`` and applied by the caller after the transaction commits.
        """
        try:
            self._scheduler_election_step(scheduler, lease_secs, pending)
        except Exception as e:
            logger.error(f"Scheduler-election error: {e}", exc_info=True)
        self._idle_housekeeping()

    def _scheduler_election_step(self, scheduler, lease_secs: float, pending: dict) -> None:
        """Renew/claim scheduler leases and fire due cron tasks for held queues.

        Lease renewal and claiming unowned queues run once at least half the TTL has
        elapsed since the last one (the lease is never allowed below half its TTL).
        The due-task lookup is skipped until the earliest known ``next_run_at`` is due,
        but is repeated at least every SCHEDULER_RECHECK_INTERVAL seconds (default 10)
        so a task created or changed by another process is picked up within that bound.
        """
        owned_queues = set(self._owned_queues)
        now = time.monotonic()
        if now - self._last_lease_renew >= lease_secs / 2 * 0.9:
            with self.backend.idle_transaction():
                if owned_queues:
                    self.backend.renew_queue_leases(
                        sorted(owned_queues), self.worker_id, lease_secs
                    )
                unowned = set(self.queues) - owned_queues
                if unowned:
                    newly_claimed = set(
                        self.backend.claim_queue_leases(
                            sorted(unowned), self.worker_id, self.node_id, self.pid, lease_secs
                        )
                    )
                    if newly_claimed:
                        owned_queues |= newly_claimed
                        logger.info(f"Acquired scheduler leases for: {sorted(newly_claimed)}")
            pending["_last_lease_renew"] = now
            pending["_owned_queues"] = owned_queues
            if owned_queues != self._owned_queues:
                pending["_sched_next_check"] = 0.0
        # Fire cron for held queues only (ELECT-01 + ELECT-02)
        next_check = pending.get("_sched_next_check", self._sched_next_check)
        if owned_queues and now >= next_check:
            with self.backend.idle_transaction():
                jobs = scheduler.run_due_tasks(queue_names=owned_queues)
                if jobs:
                    logger.info(f"Scheduler created {len(jobs)} jobs")
                wait = self._scheduler_recheck_interval()
                nxt = self.backend.next_scheduled_run_at(sorted(owned_queues))
            if nxt is not None:
                if nxt.tzinfo is None:
                    nxt = nxt.replace(tzinfo=timezone.utc)
                until_due = (nxt - datetime.now(timezone.utc)).total_seconds()
                wait = min(wait, max(until_due, 0.0))
            pending["_sched_next_check"] = now + wait

    @staticmethod
    def _scheduler_recheck_interval() -> float:
        try:
            return max(float(get_config("SCHEDULER_RECHECK_INTERVAL", 10)), 0.0)
        except (TypeError, ValueError):
            return 10.0

    def _check_heartbeat(self):
        """Process deferred heartbeat from SIGUSR1 signal handler (signal-safe)."""
        if self._heartbeat_due:
            self._heartbeat_due = False
            if self.current_job is not None:
                self._heartbeat('busy', job_id=self.current_job.id)
            else:
                self._heartbeat('idle')
        # Detect stale main loop — if we haven't looped recently, reset DB
        # Skip when a child is running: parent legitimately blocks in waitpid
        # loop_age = time.monotonic() - self._last_loop_time
        # if loop_age > self.poll_interval * 3:
        #     logger.warning(f"Main loop stale ({int(loop_age)}s), resetting DB connections")
        #     self.executor._reset_db_connections()
        if self.child_pid is None:
            loop_age = time.monotonic() - self._last_loop_time
            if loop_age > self.poll_interval * 3:
                logger.warning(f"Main loop stale ({int(loop_age)}s), resetting DB connections")
                self.executor._reset_db_connections()

    def _heartbeat(self, status: str, job_id=None):
        """Send a heartbeat to the database."""
        try:
            # Own savepoint (#110): a swallowed error must not abort the shared transaction.
            with self.backend.idle_transaction():
                self.backend.update_worker_heartbeat(
                    worker_id=self.worker_id,
                    status=status,
                    current_job_id=job_id,
                    jobs_processed=self.jobs_processed,
                    total_busy_seconds=self.total_busy_seconds,
                )
        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")


# Backward compatibility aliases
Worker = WorkerProcess

__all__ = [
    "JobExecutor",
    "TaskExecutor",
    "WorkerProcess",
    "Worker",
    "_current_job_var",
]


def __getattr__(name):
    """Lazy re-export of `TaskExecutor`.

    `TaskExecutor` is the historic public name. In Django mode it resolves
    to `sqlery.django_sqlery._executor_impl.TaskExecutor` (a Django-coupled
    class with scheduled-task helpers). In standalone mode (no Django
    installed) it falls back to the framework-agnostic `JobExecutor` in
    this module.

    This indirection lets callers do `from sqlery.core.worker import
    TaskExecutor` without `core/worker.py` importing Django at module load.
    """
    if name == "TaskExecutor":
        try:
            from sqlery.django_sqlery._executor_impl import TaskExecutor as _TE
            return _TE
        except ImportError:
            return JobExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
