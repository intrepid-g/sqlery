"""Django-agnostic worker execution logic with fork-per-job support."""

import contextvars
import json
import logging
import re
import socket
import sys
import traceback as tb
import signal
import os
import time
from datetime import datetime, timezone, timedelta

from ..compat import get_backend, get_config
from .utils import import_task
from .security import check_task_module_allowed, warn_if_unconfigured
from sqlery.core.db_resilience import configure_connection_resilience

try:
    from django.db import connections, close_old_connections
except ImportError:
    connections = None
    close_old_connections = None

_current_job_var: contextvars.ContextVar = contextvars.ContextVar('current_job', default=None)

logger = logging.getLogger(__name__)


class JobExecutor:
    """Executes jobs with retry logic, timeout support, and crash recovery.

    Works in both Django and standalone modes via backend abstraction.
    """

    def __init__(self, backend=None):
        if backend is None:
            # from ..compat import get_backend  # moved to top-level
            backend = get_backend()
        self.backend = backend

    def execute_job(self, job):
        """Execute a single job with error handling and retry logic."""
        logger.info(f"Executing job {job.id}: {job.task_path}")

        # Refresh job state from database
        job = self.backend.get_job_by_id(job.id)

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

            # Set up timeout
            if job.timeout_seconds:
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
                logger.info(f"Job {job.id} executing with args={positional_args}, kwargs={kwargs}")
                _token = _current_job_var.set(job)
                try:
                    result = task_func(*positional_args, **kwargs)
                finally:
                    _current_job_var.reset(_token)

                job = self.backend.mark_job_success(job.id, output=result or "")
                # If this is a retry, cascade success to all ancestor attempts
                if getattr(job, 'parent_job_id', None):
                    try:
                        self.backend.cascade_ancestor_status(job.id, 'success')
                    except Exception as e:
                        logger.warning(f"Failed to cascade success for job {job.id}: {e}")
                logger.info(f"Job {job.id} completed successfully")

            finally:
                if job.timeout_seconds:
                    signal.alarm(0)

        except Exception as e:
            error_msg = str(e)
            error_traceback = tb.format_exc()
            job = self.backend.mark_job_failed(job.id, error=error_msg, traceback=error_traceback)
            logger.error(f"Job {job.id} failed: {error_msg}")

            if self._should_retry(job):
                retry_job = self._retry_job(job)
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
        # write_fd = int(os.environ.pop('_SQLERY_RESULT_FD'))

        try:
            # Close inherited DB connections — child must open its own
            self._reset_db_connections()

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
            logger.info(f"Job {job.id} executing with args={positional_args}, kwargs={kwargs}")

            _token = _current_job_var.set(job)
            try:
                result = task_func(*positional_args, **kwargs)
            finally:
                _current_job_var.reset(_token)

            # Mark success in DB from child
            job = self.backend.mark_job_success(job.id, output=result or "")
            # If this is a retry, cascade success to all ancestor attempts
            if getattr(job, 'parent_job_id', None):
                try:
                    self.backend.cascade_ancestor_status(job.id, 'success')
                except Exception as e:
                    logger.warning(f"Failed to cascade success for job {job.id}: {e}")
            logger.info(f"Job {job.id} completed successfully")

            # # Pipe write removed — parent reads status from DB
            # result_payload = json.dumps({
            #     'success': True,
            #     'output': str(result or ''),
            # }).encode()
            # os.write(write_fd, result_payload)
            # os.close(write_fd)
            os._exit(0)

        except Exception as e:
            error_msg = str(e)
            error_traceback = tb.format_exc()
            logger.error(f"Job {job.id} failed: {error_msg}")

            try:
                # Reset DB connections before marking failure — the task may
                # have left the connection in InFailedSqlTransaction state
                # (e.g. a ProgrammingError in bulk_insert), which would cause
                # mark_job_failed to fail on the same broken connection.
                self._reset_db_connections()
                failed_job = self.backend.mark_job_failed(job.id, error=error_msg, traceback=error_traceback)

                # Handle retry in child (it has the DB connection)
                if self._should_retry(failed_job):
                    retry_job = self._retry_job(failed_job)
                    logger.info(
                        f"Job {job.id} will be retried as job {retry_job.id} "
                        f"(attempt {retry_job.retry_count + 1}/{retry_job.max_retries + 1})"
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

            os._exit(1)

    def _reset_db_connections(self):
        """Close inherited DB connections after fork."""
        try:
            # from django.db import connections  # moved to top-level (try/except)
            if connections is not None:
                connections.close_all()
        except Exception:
            pass

    def _import_task(self, task_path: str):
        # # Duplicated logic — now delegates to core.utils.import_task
        # try:
        #     module_path, func_name = task_path.rsplit('.', 1)
        #     module = __import__(module_path, fromlist=[func_name])
        #     return getattr(module, func_name)
        # except (ImportError, AttributeError, ValueError) as e:
        #     raise ImportError(f"Cannot import task '{task_path}': {e}")
        # from .utils import import_task  # moved to top-level

        # SEC-04 gate: enforce ALLOWED_TASK_MODULES before importlib resolves
        # the module. Unset / empty list = pass-through (BC).
        module_path = task_path.rsplit(".", 1)[0] if "." in task_path else task_path
        allowed = get_config("ALLOWED_TASK_MODULES", None)
        check_task_module_allowed(module_path, allowed)

        return import_task(task_path)

    def _should_retry(self, job) -> bool:
        if job.max_retries is None or job.max_retries <= 0:
            return False
        if job.retry_count is None:
            return True
        return job.retry_count < job.max_retries

    def _retry_job(self, failed_job):
        retry_count = failed_job.retry_count if failed_job.retry_count is not None else 0
        backoff = failed_job.retry_backoff if failed_job.retry_backoff else 1.0
        delay_seconds = backoff * (2 ** retry_count)

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

        retry_job = self.backend.create_job(
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

        # Mark original failed job as archived (retry created, no longer relevant to dashboard)
        try:
            self.backend.mark_job_archived(failed_job.id)
        except Exception as e:
            logger.warning(f"Failed to mark job {failed_job.id} as archived: {e}")

        logger.info(
            f"Created retry job {retry_job.id} for failed job {failed_job.id}, "
            f"scheduled in {delay_seconds}s (attempt {retry_count + 2}/{failed_job.max_retries + 1})"
        )

        return retry_job

    def cleanup_stale_jobs(self, queue_name: str | None = None):
        """Reset stale jobs stuck in 'running' state (crashed workers)."""
        now = datetime.now(timezone.utc)
        running_jobs = self.backend.get_running_jobs(queue_name)

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
                if hasattr(job, 'worker_pid') and job.worker_pid:
                    killed = self._kill_worker_process(job.worker_pid)
                    if killed:
                        logger.warning(
                            f"Killed hung worker process {job.worker_pid} for job {job.id}"
                        )

                self.backend.mark_job_failed(
                    job.id,
                    error=f"Worker crashed or was killed (running for {int(running_duration)}s, threshold {threshold_seconds}s)",
                    traceback="Job was stuck in 'running' state - likely worker process crashed, killed by OOM, or terminated by signal"
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
                try:
                    os.kill(pid, 0)
                except OSError:
                    logger.info(f"Worker process {pid} terminated gracefully")
                    return True

            logger.warning(f"Worker process {pid} did not terminate, sending SIGKILL")
            os.kill(pid, signal.SIGKILL)

            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
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


class WorkerProcess:
    """Persistent worker process that polls for jobs and forks children to execute them.

    RQ-style fork-per-job model:
    - Parent process: claims jobs, forks children, monitors them, handles heartbeats
    - Child process: executes one job, writes result to pipe, exits
    - Memory leaks are cleaned up per-job (OS reclaims child memory)
    - Parent is never blocked by job execution, so heartbeats always work
    """

    def __init__(self, queues: list[str] | None = None, backend=None):
        if backend is None:
            # from ..compat import get_backend  # moved to top-level
            backend = get_backend()

        self.backend = backend
        self.queues = queues or ['default']
        self.executor = JobExecutor(backend)
        self.shutdown_requested = False
        self.jobs_processed = 0
        self.current_job = None
        self.child_pid = None  # PID of forked child currently executing a job
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

    def run(self):
        """Run worker loop: claim jobs, fork children, monitor, heartbeat."""
        # import sys  # moved to top-level

        # SEC-04 (W3): production-shaped + unset = one WARNING per worker run.
        # Pinned to first line BEFORE the fork loop so it fires exactly once
        # per WorkerProcess.run, never inside forked children.
        warn_if_unconfigured(get_config("ALLOWED_TASK_MODULES", None))

        logger.info(f"Worker {self.worker_id} starting (queues={self.queues}, poll={self.poll_interval}s)")

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

        try:
            while not self.shutdown_requested:
                try:
                    # Prune connections that exceeded CONN_MAX_AGE (like Django request cycle)
                    # from django.db import close_old_connections  # moved to top-level (try/except)
                    if close_old_connections is not None:
                        close_old_connections()

                    self._last_loop_time = time.monotonic()
                    self._check_heartbeat()

                    job = self.backend.claim_job(self.queues, self.worker_id)

                    if not job:
                        self._heartbeat('idle')
                        logger.info(".")
                        elapsed = 0
                        while elapsed < self.poll_interval and not self.shutdown_requested:
                            time.sleep(1)
                            elapsed += 1
                            self._check_heartbeat()
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
                        elapsed = 0
                        while elapsed < self.poll_interval and not self.shutdown_requested:
                            time.sleep(1)
                            elapsed += 1
                            self._check_heartbeat()
                        continue

                    # Fork and execute
                    sys.stderr.write(f"\n[CLAIMED] Job #{job.id} ({job.task_path})\n")
                    sys.stderr.flush()
                    logger.info(f"Worker {self.worker_id}: Claimed job {job.id} ({job.task_path})")
                    self.current_job = job

                    try:
                        job_start = time.monotonic()
                        result = self._fork_and_execute(job)
                        self.total_busy_seconds += time.monotonic() - job_start
                        self.jobs_processed += 1
                        status = 'SUCCESS' if result.get('success') else 'FAILED'
                        sys.stderr.write(f"[{status}] Job #{job.id}\n")
                        sys.stderr.flush()
                        logger.info(f"Worker {self.worker_id}: Completed job {job.id} -> {status.lower()}")
                    finally:
                        self.current_job = None
                        self.child_pid = None

                    # Don't heartbeat idle between jobs — the next loop iteration
                    # will either claim a new job immediately or heartbeat idle
                    # when entering the poll sleep (line 418). This avoids brief
                    # idle flashes on the dashboard when jobs are back-to-back.
                    # self._heartbeat('idle')

                except KeyboardInterrupt:
                    break
                except Exception as e:
                    logger.exception(f"Worker {self.worker_id}: Unhandled error in main loop")
                    # Reset DB connections in case they're corrupted
                    self.executor._reset_db_connections()
                    time.sleep(self.poll_interval)
                    continue

        except KeyboardInterrupt:
            logger.info(f"Worker {self.worker_id} interrupted")
        finally:
            try:
                self.backend.update_worker_heartbeat(
                    worker_id=self.worker_id,
                    status='dead',
                    current_job_id=None,
                    jobs_processed=self.jobs_processed,
                )
            except Exception:
                pass
            logger.info(f"Worker {self.worker_id} stopped (processed {self.jobs_processed} jobs)")

        return self.jobs_processed

    def _fork_and_execute(self, job) -> dict:
        """Fork a child process to execute the job.

        Parent waits for child via waitpid, enforces two-layer timeout,
        reads final result from DB (no pipe IPC).
        Returns dict with 'success', 'output'/'error'/'traceback'.
        """
        # from ..compat import get_config  # moved to top-level
        timeout = job.timeout_seconds or get_config('DEFAULT_TIMEOUT_SECONDS', 600)

        # # Pipe removed — child writes results directly to DB (like RQ)
        # read_fd, write_fd = os.pipe()

        # Close DB connections BEFORE fork so parent and child don't share
        # the same socket FDs. Each side will reconnect on first DB access.
        self.executor._reset_db_connections()

        child_pid = os.fork()

        if child_pid == 0:
            # === CHILD PROCESS ===
            # Process group isolation — kill the whole group on stop/timeout
            os.setpgrp()
            # os.close(read_fd)
            # os.environ['_SQLERY_RESULT_FD'] = str(write_fd)
            try:
                self.executor.execute_job_in_child(job)
            except Exception:
                # execute_job_in_child calls os._exit(), but just in case
                os._exit(1)
            os._exit(0)  # Should never reach here

        # === PARENT PROCESS ===
        # os.close(write_fd)
        self.child_pid = child_pid
        logger.info(f"Forked child PID {child_pid} for job {job.id}")

        # Persist child PID to DB so admin "Stop Job" can target the child
        try:
            self.backend.update_job_child_pid(job.id, child_pid)
        except Exception:
            pass

        # Reconnect DB after fork (close_all before fork severed the connection)
        self.executor._reset_db_connections()

        # Also prune stale connections (parent may wait a long time for child)
        # from django.db import close_old_connections  # moved to top-level (try/except)
        if close_old_connections is not None:
            close_old_connections()

        # Update heartbeat with child info
        self._heartbeat('busy', job_id=job.id)

        # Two-layer timeout: child SIGALRM fires at `timeout`, parent
        # force-kills at `timeout + 60s` as safety net (like RQ).
        parent_timeout = (timeout + 60) if timeout else None

        # Wait for child with timeout
        start_time = time.monotonic()
        child_exited = False
        exit_status = None

        while not child_exited:
            try:
                pid, status = os.waitpid(child_pid, os.WNOHANG)
            except ChildProcessError:
                # Child already reaped
                child_exited = True
                exit_status = 1
                break

            if pid != 0:
                child_exited = True
                exit_status = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
                break

            # Check timeout — parent safety net fires after timeout + 60s
            elapsed = time.monotonic() - start_time
            # if timeout and elapsed > timeout:
            if parent_timeout and elapsed > parent_timeout:
                logger.warning(f"Job {job.id} timed out after {int(elapsed)}s (parent safety net), killing child PID {child_pid}")
                self._kill_child(child_pid)
                # # Pipe close removed
                # os.close(read_fd)
                try:
                    self.backend.mark_job_failed(
                        job.id,
                        error=f"Job {job.id} exceeded timeout of {timeout} seconds (parent safety net at {int(elapsed)}s)",
                        traceback=f"Parent killed child PID {child_pid} after {int(elapsed)}s",
                    )
                except Exception:
                    pass
                return {'success': False, 'error': f'Timeout after {int(elapsed)}s'}

            # Sleep briefly so parent stays responsive to signals
            time.sleep(0.5)
            self._check_heartbeat()

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
        #     if result_data:
        #         return json.loads(result_data.decode())
        # except Exception as e:
        #     logger.warning(f"Failed to read result from child for job {job.id}: {e}")

        # Reconnect DB (may be stale after waiting)
        self.executor._reset_db_connections()
        refreshed = self.backend.get_job_by_id(job.id)
        if refreshed and refreshed.status == 'success':
            return {'success': True, 'output': refreshed.output or ''}
        elif refreshed and refreshed.status == 'failed':
            return {'success': False, 'error': refreshed.error or ''}
        elif exit_status == 0:
            return {'success': True, 'output': ''}
        else:
            # Child crashed — mark failed if not already marked by child
            try:
                self.backend.mark_job_failed(
                    job.id,
                    error=f"Child process exited with status {exit_status}",
                    traceback=f"Child PID {child_pid} exited abnormally",
                )
            except Exception as e:
                logger.error(f"Failed to mark crashed job {job.id}: {e}")
            return {'success': False, 'error': f'Child exited with status {exit_status}'}

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
    to `sqlery.django_sqlery.executor.TaskExecutor` (a Django-coupled class
    with scheduled-task helpers). In standalone mode (no Django installed)
    it falls back to the framework-agnostic `JobExecutor` in this module.

    This indirection lets callers do `from sqlery.core.worker import
    TaskExecutor` without `core/worker.py` importing Django at module load.
    """
    if name == "TaskExecutor":
        try:
            from sqlery.django_sqlery.executor import TaskExecutor as _TE
            return _TE
        except ImportError:
            return JobExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
