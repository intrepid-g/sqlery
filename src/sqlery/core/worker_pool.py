"""Framework-agnostic worker pool management.

Manages spawning, monitoring, and cleanup of worker processes using the
backend abstraction layer. Works in both Django and standalone modes.
"""

import json
import os
import sys
import socket
import subprocess
import signal
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlery.contract import dead_worker_reaper, zombie_sweep
from sqlery.contract.config import ALIVE_TIMEOUT_DEFAULT

from ..compat import get_backend, get_config, is_django_mode
from . import liveness
from .launcher import START_FORK, WorkerLauncher, load_start_method
from .log_config import is_debug_mode

try:
    from django.conf import settings as django_settings
except ImportError:
    django_settings = None

try:
    from dateutil.parser import isoparse
except ImportError:
    isoparse = None

logger = logging.getLogger(__name__)

WORKER_QUEUES_ENV = "SQLERY_WORKER_QUEUES"


class WorkerPoolManager:
    """Manages a pool of worker subprocesses.

    Uses backend abstraction for all database operations, making it
    framework-agnostic (works with Django, SQLAlchemy, etc.).
    """

    def __init__(self, max_workers: int, queues: list[str], backend=None):
        """Initialize worker pool manager.

        Args:
            max_workers: Maximum number of workers to spawn
            queues: List of queue names workers should process
            backend: Database backend (auto-detected if None)
        """
        if backend is None:
            # from ..compat import get_backend  # moved to top-level
            backend = get_backend()

        self.backend = backend
        self.max_workers = max_workers
        self.queues = queues
        self.node_id = self._get_node_id()
        self._child_processes: list = []  # Popen / LaunchedWorker handles, for reaping
        self._launcher: WorkerLauncher | None = None  # started on the first forked worker
        # When the daemon last regained the database (clause DWR-4); None outside a daemon.
        self.watched_since: datetime | None = None

    def close(self) -> None:
        """Stop the worker launcher, if one was started (workers are stopped separately)."""
        if self._launcher is not None:
            self._launcher.close()
            self._launcher = None

    def _get_node_id(self) -> str:
        """Get unique node identifier.

        Returns:
            Hostname of the current machine
        """
        return socket.gethostname()

    def _get_log_dir(self) -> Path:
        """Get directory for worker log files.

        Returns:
            Path to log directory
        """
        # from ..compat import get_config, is_django_mode  # moved to top-level

        if is_django_mode():
            # from django.conf import settings  # moved to top-level (try/except as django_settings)
            log_dir = Path(django_settings.BASE_DIR) / 'tmp'
        else:
            log_dir = Path(get_config('LOG_DIR', '/tmp/sqlery'))

        log_dir.mkdir(exist_ok=True, parents=True)
        return log_dir

    def spawn_worker(self):
        """Spawn a new worker subprocess.

        Creates a worker process that will process jobs from the configured queues.
        Registers the worker in the database for monitoring.

        WORKER_START_METHOD="fork" (default, #163) has the preloaded launcher fork it;
        "spawn", or a launcher failure, starts a fresh interpreter.

        Returns:
            subprocess.Popen (spawn) or LaunchedWorker (fork) handle of the worker
        """
        if load_start_method() == START_FORK:
            try:
                if self._launcher is None:
                    stdout_path = (
                        str(self._get_log_dir() / f'sqlery_worker_{os.getpid()}.log')
                        if is_debug_mode() else None
                    )
                    self._launcher = WorkerLauncher(stdout_path=stdout_path)
                worker = self._launcher.spawn(self.queues)
                logger.info(f"Forked worker process: PID {worker.pid}")
                self._child_processes.append(worker)
                return worker
            except Exception as e:
                logger.error(f"Forking a worker failed ({e}); starting a fresh interpreter")

        # Debug mode: redirect stdout to raw log file (grows forever).
        # Normal mode: subprocess configures its own RotatingFileHandler; parent
        # sends stdout to DEVNULL since the child manages its own logging.
        if is_debug_mode():
            log_dir = self._get_log_dir()
            worker_log = log_dir / f'sqlery_worker_{os.getpid()}.log'
            try:
                worker_log_file = open(worker_log, 'a')
            except Exception as e:
                logger.warning(f"Failed to open log file {worker_log}: {e}")
                worker_log_file = subprocess.DEVNULL
        else:
            worker_log_file = subprocess.DEVNULL

        # # Old: always redirect to raw log file (grows forever)
        # log_dir = self._get_log_dir()
        # worker_log = log_dir / f'sqlery_worker_{os.getpid()}.log'
        # try:
        #     worker_log_file = open(worker_log, 'a')
        # except Exception as e:
        #     logger.warning(f"Failed to open log file {worker_log}: {e}")
        #     worker_log_file = subprocess.DEVNULL

        # Spawn worker subprocess using core worker module
        # The worker will run jobs from the specified queues
        # The fresh interpreter re-reads settings, so hand it this pool's queues explicitly
        # (e.g. ``rqworker reports`` overrides WORKER_QUEUES only in the daemon process).
        worker_env = os.environ.copy()
        worker_env[WORKER_QUEUES_ENV] = json.dumps(list(self.queues))

        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "sqlery.core.worker_runner"],
                stdout=worker_log_file,
                stderr=sys.stderr,       # stderr goes to daemon's stderr (visible in docker logs)
                stdin=subprocess.DEVNULL,
                env=worker_env,
                start_new_session=True,
                close_fds=True,   # was False — prevents FD leaks into worker processes
            )

            logger.info(f"Spawned worker process: PID {process.pid}")

            # Close the log file handle in the parent — the child inherited
            # the FD via Popen. Leaving it open leaks one FD per spawn.
            if worker_log_file and worker_log_file != subprocess.DEVNULL:
                try:
                    worker_log_file.close()
                except Exception:
                    pass

            # Worker registers itself in the DB on startup (WorkerProcess.__init__),
            # so we don't pre-register here to avoid duplicate records.
            self._child_processes.append(process)

            return process

        except Exception as e:
            logger.error(f"Failed to spawn worker: {e}")
            if worker_log_file != subprocess.DEVNULL:
                worker_log_file.close()
            raise

    def reap_children(self):
        """Reap terminated child processes to prevent zombies.

        Calls poll() on each tracked Popen object. Removes finished processes
        from the tracking list.
        """
        still_alive = []
        for proc in self._child_processes:
            ret = proc.poll()  # Non-blocking check; reaps zombie if terminated
            if ret is None:
                still_alive.append(proc)
            else:
                logger.debug(f"Reaped worker process PID {proc.pid} (exit code {ret})")
        self._child_processes = still_alive

    def started_pids(self) -> set[int]:
        """PIDs of the workers this pool started that are still running.

        Only these may be killed for silence: the PID of any other worker row may belong to
        an unrelated process by now (#48).
        """
        return {proc.pid for proc in self._child_processes if proc.poll() is None}

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        """Edge: does the PID exist? Delegates to clause DWR-3 (EPERM counts as alive, #40)."""
        # Old: except OSError: return False  (EPERM, a live process of another user, read as gone)
        return liveness.pid_exists(pid)

    def cleanup_dead_workers(self) -> int:
        """Remove dead workers from database.

        Uses heartbeat timestamp as primary indicator of worker health.
        Workers are considered dead if:
        1. Heartbeat is older than WORKER_ALIVE_TIMEOUT (worker not responding)
        2. OR process PID check fails (worker crashed on same node)

        Returns:
            Number of workers cleaned up
        """
        # from datetime import datetime, timezone, timedelta  # moved to top-level

        workers = self.backend.get_worker_heartbeats(active_only=False)
        now = datetime.now(timezone.utc)
        alive_timeout = get_config('WORKER_ALIVE_TIMEOUT', ALIVE_TIMEOUT_DEFAULT)

        started = self.started_pids()
        cleaned = 0
        for worker in workers:
            # Get worker node_id
            if hasattr(worker, 'node_id'):
                node_id = worker.node_id
            elif isinstance(worker, dict):
                node_id = worker.get('node_id')
            else:
                continue

            # Check if worker is on this node
            on_this_node = node_id == self.node_id

            # Get worker details
            if hasattr(worker, 'pid'):
                pid = worker.pid
                status = worker.status
                last_heartbeat = worker.last_heartbeat
                worker_id = str(worker.id)
            elif isinstance(worker, dict):
                pid = worker.get('pid')
                status = worker.get('status')
                last_heartbeat = worker.get('last_heartbeat')
                worker_id = str(worker.get('id'))
            else:
                continue

            # Old: inline heartbeat-age and PID checks (clauses DWR-1 and reap_verdict)
            pid_exists = self._pid_exists(pid) if on_this_node else None
            if isinstance(last_heartbeat, str):
                last_heartbeat = isoparse(last_heartbeat)
            reason = dead_worker_reaper.reap_verdict(
                status=status, pid=pid, last_heartbeat=last_heartbeat, now=now,
                pid_exists=pid_exists, alive_timeout=alive_timeout,
                watched_since=self.watched_since,
                started_by_pool=on_this_node and pid in started,
            )
            is_dead = reason is not None

            # Mark as dead if either check failed
            if is_dead:
                try:
                    self.backend.update_worker_heartbeat(
                        worker_id=worker_id,
                        status='dead',
                        current_job_id=None
                    )
                    cleaned += 1
                    logger.info(f"Marked worker {worker_id} (PID {pid}) as dead: {reason}")
                except Exception as e:
                    # Lock contention — skip this cycle, retry next time
                    logger.debug(f"Could not mark worker {worker_id} as dead (will retry): {e}")

        return cleaned

    def count_active_workers(self) -> int:
        """Count active workers on this node.

        Returns:
            Number of workers that are currently active (not dead)
        """
        workers = self.backend.get_worker_heartbeats(active_only=True)

        count = 0
        for worker in workers:
            # Only count workers on this node
            if hasattr(worker, 'node_id'):
                node_id = worker.node_id
            elif isinstance(worker, dict):
                node_id = worker.get('node_id')
            else:
                continue

            if node_id == self.node_id:
                # Exclude daemon processes (PID=0)
                if hasattr(worker, 'pid'):
                    pid = worker.pid
                elif isinstance(worker, dict):
                    pid = worker.get('pid')
                else:
                    continue

                if pid == 0:
                    # This is a daemon heartbeat, not a worker
                    continue

                # Check status
                if hasattr(worker, 'status'):
                    status = worker.status
                elif isinstance(worker, dict):
                    status = worker.get('status')
                else:
                    continue

                if status in ['idle', 'busy']:
                    count += 1

        return count

    def ensure_workers(self) -> dict:
        """Ensure worker pool is at desired size.

        Performs cleanup of dead workers and spawns new workers if needed
        to maintain the configured max_workers count.

        Returns:
            Dict with status information:
            - node_id: Node identifier
            - max_workers: Maximum workers configured
            - current_workers: Current count after cleanup/spawning
            - spawned: Number of new workers spawned
            - cleaned_up: Number of dead workers cleaned
        """
        # Reap any terminated child processes (prevents OS zombies)
        self.reap_children()

        # Cleanup dead workers first
        cleaned = self.cleanup_dead_workers()

        # Count current active workers (DB-based)
        current = self.count_active_workers()

        # Also count live child processes — don't spawn if we already have enough
        # OS processes, even if their DB heartbeats are stale (e.g., stuck on I/O)
        live_children = len(self._child_processes)
        effective_current = max(current, live_children)

        # Spawn workers if needed
        spawned = 0
        needed = self.max_workers - effective_current

        if needed > 0:
            logger.info(f"Need to spawn {needed} workers (current: {current}, max: {self.max_workers})")

            for i in range(needed):
                try:
                    self.spawn_worker()
                    spawned += 1
                except Exception as e:
                    logger.error(f"Failed to spawn worker {i+1}/{needed}: {e}")
                    break

        result = {
            'node_id': self.node_id,
            'max_workers': self.max_workers,
            'current_workers': effective_current + spawned,
            'spawned': spawned,
            'cleaned_up': cleaned,
        }
        return result

    def stop_all_workers(self, force: bool = False) -> int:
        """Stop all workers on this node.

        Sends signals to worker processes to shut them down gracefully
        (SIGTERM) or forcefully (SIGKILL).

        Args:
            force: If True, use SIGKILL instead of SIGTERM

        Returns:
            Number of workers stopped
        """
        workers = self.backend.get_worker_heartbeats(active_only=True)

        stopped = 0
        sig = signal.SIGKILL if force else signal.SIGTERM
        signaled_pids: set[int] = set()

        for worker in workers:
            # Only stop workers on this node
            if hasattr(worker, 'node_id'):
                node_id = worker.node_id
            elif isinstance(worker, dict):
                node_id = worker.get('node_id')
            else:
                continue

            if node_id != self.node_id:
                continue

            # Get worker details
            if hasattr(worker, 'pid'):
                pid = worker.pid
                worker_id = str(worker.id)
            elif isinstance(worker, dict):
                pid = worker.get('pid')
                worker_id = str(worker.get('id'))
            else:
                continue

            # PID 0 is the daemon's own heartbeat row, and os.kill(0, sig) would signal the
            # caller's whole process group (the daemon, its shell, a test runner) (#163).
            if not pid or pid <= 0 or pid == os.getpid():
                continue

            # Send signal to process
            try:
                os.kill(pid, sig)
                stopped += 1
                signaled_pids.add(pid)
                logger.info(f"Sent {signal.Signals(sig).name} to worker {worker_id} (PID {pid})")
            except ProcessLookupError:
                # Process already dead
                logger.debug(f"Worker {worker_id} (PID {pid}) already dead")
            except PermissionError:
                # Can't kill process (different user?)
                logger.warning(f"Permission denied killing worker {worker_id} (PID {pid})")
            except Exception as e:
                logger.error(f"Error stopping worker {worker_id} (PID {pid}): {e}")

            # Mark as dead in database
            try:
                self.backend.update_worker_heartbeat(
                    worker_id=worker_id,
                    status='dead',
                    current_job_id=None
                )
            except Exception as e:
                logger.error(f"Failed to update worker {worker_id} status: {e}")

        # Fallback: also signal locally-tracked worker subprocesses that
        # never made it into the DB heartbeat sweep above. spawn_worker()
        # forks the child before the child's WorkerProcess.__init__ has a
        # chance to INSERT its heartbeat row; a one-shot cycle
        # (`_run_daemon(once=True)`) can reach this `finally` block before
        # that INSERT lands, so the DB-driven loop above never sees (and
        # never signals) the newly spawned PID. Since the child is spawned
        # with `start_new_session=True` (detached process group), an
        # unsignaled worker keeps polling forever as an orphan — this is
        # what leaked and hung CI (killed as an orphan process partway
        # through the run). Signaling every tracked Popen handle directly
        # closes that race regardless of heartbeat timing.
        for proc in self._child_processes:
            if proc.pid in signaled_pids:
                continue
            if proc.poll() is not None:
                continue  # already exited
            try:
                proc.send_signal(sig)
                stopped += 1
                logger.info(
                    f"Sent {signal.Signals(sig).name} to untracked worker (PID {proc.pid})"
                )
            except ProcessLookupError:
                logger.debug(f"Untracked worker (PID {proc.pid}) already dead")
            except Exception as e:
                logger.error(f"Error stopping untracked worker (PID {proc.pid}): {e}")

        return stopped

    def get_status(self) -> dict:
        """Get current worker pool status.

        Returns:
            Dict with detailed worker pool status:
            - node_id: Node identifier
            - max_workers: Maximum workers configured
            - active_count: Number of active workers
            - idle_count: Number of idle workers
            - busy_count: Number of busy workers
            - dead_count: Number of dead workers
            - workers: List of worker details
        """
        workers = self.backend.get_worker_heartbeats(active_only=False)

        # Filter workers for this node and categorize
        node_workers = []
        idle_count = 0
        busy_count = 0
        dead_count = 0

        for worker in workers:
            # Check node_id
            if hasattr(worker, 'node_id'):
                node_id = worker.node_id
            elif isinstance(worker, dict):
                node_id = worker.get('node_id')
            else:
                continue

            if node_id != self.node_id:
                continue

            # Get status
            if hasattr(worker, 'status'):
                status = worker.status
            elif isinstance(worker, dict):
                status = worker.get('status')
            else:
                status = 'unknown'

            # Count by status
            if status == 'idle':
                idle_count += 1
            elif status == 'busy':
                busy_count += 1
            elif status == 'dead':
                dead_count += 1

            # Convert to dict if needed
            if hasattr(worker, '__dict__'):
                worker_dict = {
                    'id': str(worker.id),
                    'pid': worker.pid,
                    'status': worker.status,
                    'current_job_id': worker.current_job_id if hasattr(worker, 'current_job_id') else None,
                    'jobs_processed': worker.jobs_processed if hasattr(worker, 'jobs_processed') else 0,
                    'started_at': worker.started_at.isoformat() if hasattr(worker, 'started_at') and worker.started_at else None,
                    'last_heartbeat': worker.last_heartbeat.isoformat() if hasattr(worker, 'last_heartbeat') else None,
                }
            else:
                worker_dict = dict(worker)

            node_workers.append(worker_dict)

        active_count = idle_count + busy_count

        return {
            'node_id': self.node_id,
            'max_workers': self.max_workers,
            'active_count': active_count,
            'idle_count': idle_count,
            'busy_count': busy_count,
            'dead_count': dead_count,
            'workers': node_workers,
        }

    def _process_gone(self, pid: int) -> bool:
        """True once ``pid`` has exited, reaping it when this pool started it.

        A worker started by this pool stays a zombie (``kill(pid, 0)`` succeeds) until its
        ``Popen`` is polled, so polling is the only way to see it gone.
        """
        for proc in self._child_processes:
            if proc.pid == pid:
                return proc.poll() is not None
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False

    def _kill_worker_process(self, pid: int) -> bool:
        """Kill a worker process by PID.

        Tries SIGTERM first (graceful), then SIGKILL after 5 seconds.

        Args:
            pid: Process ID to kill

        Returns:
            True if process was killed, False if already dead or error
        """
        # import time  # moved to top-level

        if not pid or pid <= 0 or pid == os.getpid():
            return False  # never a process group or the caller itself (#163)

        try:
            # Check if process exists
            os.kill(pid, 0)  # Signal 0 checks existence without killing
        except OSError:
            # Process doesn't exist
            return False

        try:
            # Send SIGTERM (graceful shutdown)
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to worker process {pid}")

            # Wait up to 5 seconds for graceful shutdown
            for _ in range(10):
                time.sleep(0.5)
                # Old: os.kill(pid, 0) — a worker this pool started stays a zombie until its
                # Popen is polled, so the probe saw it alive and every kill "failed" (#48).
                if self._process_gone(pid):
                    logger.info(f"Worker process {pid} terminated gracefully")
                    return True

            # Process still alive after 5s, send SIGKILL
            logger.warning(f"Worker process {pid} did not terminate, sending SIGKILL")
            os.kill(pid, signal.SIGKILL)

            # Wait briefly for SIGKILL
            time.sleep(0.5)
            if self._process_gone(pid):
                logger.info(f"Worker process {pid} killed with SIGKILL")
                return True

            logger.error(f"Failed to kill worker process {pid}")
            return False

        except Exception as e:
            logger.error(f"Error killing worker process {pid}: {e}")
            return False

    def _kill_job_child(self, worker_id: str, job_id) -> int | None:
        """SIGKILL the job child of a hung worker, with its process group (#48).

        The child is read from the job's open attempt, and only when that attempt belongs to
        ``worker_id``. A forked job child leads its own process group (``os.setpgrp``), so the
        group takes the job's own subprocesses with it. Returns the child PID, or None.
        """
        if not job_id:
            return None
        try:
            attempt = self.backend.get_open_attempts([job_id]).get(job_id)
        except Exception as e:
            logger.warning(f"Safety net: could not read the attempt of job {job_id}: {e}")
            return None
        if attempt is None or str(attempt.worker_id) != worker_id:
            return None
        child = attempt.child_pid
        if not child or child <= 0 or child == os.getpid() or child == os.getpgrp():
            return None  # never a process group of ours
        try:
            os.killpg(child, signal.SIGKILL)
        except ProcessLookupError:
            try:
                os.kill(child, signal.SIGKILL)  # not a group leader: the child alone
            except ProcessLookupError:
                return None  # already gone
        except PermissionError:
            logger.warning(f"Safety net: not allowed to kill job child {child}")
            return None
        logger.warning(f"Safety net: killed job child {child} of hung worker {worker_id}")
        return child

    def detect_and_fix_irregularities(self) -> dict:
        """Last-resort safety net for worker/job state inconsistencies.

        This should almost never fire. The primary mechanisms are:
        - Daemon sends SIGUSR1 → worker heartbeats with current job
        - _heartbeat_workers marks dead PIDs as dead
        - _fail_zombie_running_jobs catches orphaned running jobs
        - claim_job fails abandoned jobs when worker moves on
        - mark_success/mark_failed release worker.current_job
        - Worker enforces timeout via signal.alarm()

        This method only catches edge cases that slip through all of the
        above — e.g., a worker process that exists but is completely
        unresponsive to signals for an extended period.
        """
        # from datetime import datetime, timezone, timedelta  # moved to top-level
        # from ..compat import get_config  # moved to top-level

        now = datetime.now(timezone.utc)
        irregularities = {
            'dead_workers_cleaned': 0,
            'stalled_workers_killed': 0,
            'details': [],
        }

        # Every worker row on this node, the dead and the silent included: a hung worker is
        # silent, so the reaper marks it dead after WORKER_ALIVE_TIMEOUT while its process
        # (and job child) keep running. It is the one this check is for.
        # Old: get_worker_heartbeats(active_only=True) — that excludes every row silent for
        # WORKER_ALIVE_TIMEOUT, so no row could ever reach the 3x stale threshold (#48).
        workers = self.backend.get_worker_heartbeats(active_only=False)
        started = self.started_pids()

        # Workers whose heartbeat is stale despite SIGUSR1. The daemon sends SIGUSR1 every
        # cycle (~10s) and no longer writes the beat itself (#48): a worker that cannot
        # answer for WORKER_ALIVE_TIMEOUT * 3 is truly stuck.
        alive_timeout = get_config('WORKER_ALIVE_TIMEOUT', ALIVE_TIMEOUT_DEFAULT)
        # Old: stale_threshold = alive_timeout * 3  (clause ZS-0)
        stale_threshold = zombie_sweep.stale_threshold(alive_timeout)

        for worker in workers:
            row = worker if isinstance(worker, dict) else None
            node_id = row.get('node_id') if row is not None else getattr(worker, 'node_id', None)
            pid = (row.get('pid') if row is not None else getattr(worker, 'pid', None)) or 0
            worker_id = str(row.get('id') if row is not None else getattr(worker, 'id', None))
            status = row.get('status') if row is not None else getattr(worker, 'status', None)
            last_heartbeat = (
                row.get('last_heartbeat') if row is not None
                else getattr(worker, 'last_heartbeat', None)
            )
            current_job_id = (
                row.get('current_job_id') if row is not None
                else getattr(worker, 'current_job_id', None)
            )
            if node_id != self.node_id or pid <= 0 or pid == os.getpid():
                continue  # another node's worker, or a daemon row
            if worker_id.startswith('daemon_') or not last_heartbeat:
                continue
            if isinstance(last_heartbeat, str):
                last_heartbeat = isoparse(last_heartbeat)

            # Old: heartbeat_age = (now - last_heartbeat)  (an outage made every beat stale)
            heartbeat_age = (
                now - dead_worker_reaper.silence_since(last_heartbeat, self.watched_since)
            ).total_seconds()
            # Old: if heartbeat_age <= stale_threshold: continue; then an inline os.kill probe
            action = dead_worker_reaper.safety_net_action(
                heartbeat_age, stale_threshold, self._pid_exists(pid), pid in started
            )
            if action == "keep":
                continue

            if action == "mark_dead":
                if status == 'dead':
                    continue  # already recorded; nothing to fix
                self.backend.update_worker_heartbeat(
                    worker_id=worker_id, status='dead', current_job_id=None
                )
                irregularities['dead_workers_cleaned'] += 1
                irregularities['details'].append({
                    'type': 'dead_worker', 'worker_id': worker_id,
                    'pid': pid, 'reason': 'process_not_found',
                })
                logger.warning(f"Safety net: worker {worker_id} (PID {pid}) — process gone, marked dead")
            else:
                # Process alive but unresponsive to SIGUSR1 for too long
                logger.warning(
                    f"Safety net: worker {worker_id} (PID {pid}) heartbeat stale "
                    f"({heartbeat_age:.0f}s > {stale_threshold}s) but process alive — killing"
                )
                # The job child runs in its own process group, so killing the worker alone
                # left it running while the zombie sweep retried its job (#48).
                child_killed = self._kill_job_child(worker_id, current_job_id)
                killed = self._kill_worker_process(pid)
                if killed:
                    self.backend.update_worker_heartbeat(
                        worker_id=worker_id, status='dead', current_job_id=None
                    )
                    irregularities['stalled_workers_killed'] += 1
                    irregularities['details'].append({
                        'type': 'stalled_worker', 'worker_id': worker_id,
                        'pid': pid, 'heartbeat_age': heartbeat_age,
                        'job_child_killed': child_killed,
                        'reason': f'unresponsive_to_signals_{heartbeat_age:.0f}s',
                    })

        return irregularities
