"""Framework-agnostic worker pool management.

Manages spawning, monitoring, and cleanup of worker processes using the
backend abstraction layer. Works in both Django and standalone modes.
"""

import os
import sys
import socket
import subprocess
import signal
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


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
            from ..compat import get_backend
            backend = get_backend()

        self.backend = backend
        self.max_workers = max_workers
        self.queues = queues
        self.node_id = self._get_node_id()
        self._child_processes: list[subprocess.Popen] = []  # Track spawned workers for reaping

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
        from ..compat import get_config, is_django_mode

        if is_django_mode():
            from django.conf import settings
            log_dir = Path(settings.BASE_DIR) / 'tmp'
        else:
            log_dir = Path(get_config('LOG_DIR', '/tmp/sqlery'))

        log_dir.mkdir(exist_ok=True, parents=True)
        return log_dir

    def spawn_worker(self):
        """Spawn a new worker subprocess.

        Creates a worker process that will process jobs from the configured queues.
        Registers the worker in the database for monitoring.

        Returns:
            subprocess.Popen instance of the spawned worker
        """
        log_dir = self._get_log_dir()
        worker_log = log_dir / f'sqlery_worker_{os.getpid()}.log'

        try:
            worker_log_file = open(worker_log, 'a')
        except Exception as e:
            logger.warning(f"Failed to open log file {worker_log}: {e}")
            worker_log_file = subprocess.DEVNULL

        # Spawn worker subprocess using core worker module
        # The worker will run jobs from the specified queues
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "sqlery.core.worker_runner"],
                stdout=worker_log_file,
                stderr=sys.stderr,       # stderr goes to daemon's stderr (visible in docker logs)
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
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

    def cleanup_dead_workers(self) -> int:
        """Remove dead workers from database.

        Uses heartbeat timestamp as primary indicator of worker health.
        Workers are considered dead if:
        1. Heartbeat is older than 60 seconds (worker not responding)
        2. OR process PID check fails (worker crashed on same node)

        Returns:
            Number of workers cleaned up
        """
        from datetime import datetime, timezone, timedelta

        workers = self.backend.get_worker_heartbeats(active_only=False)
        now = datetime.now(timezone.utc)
        heartbeat_threshold = now - timedelta(seconds=60)

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

            # Skip daemon processes (PID=0)
            if pid == 0:
                continue

            # Skip workers already marked as dead
            if status == 'dead':
                continue

            # Check 1: Heartbeat age (primary indicator - applies to ALL nodes)
            is_dead = False
            reason = None

            if last_heartbeat and last_heartbeat < heartbeat_threshold:
                is_dead = True
                age_seconds = int((now - last_heartbeat).total_seconds())
                reason = f"heartbeat too old ({age_seconds}s > 60s threshold)"

            # Check 2: Process existence (secondary check, ONLY for workers on this node with recent heartbeat)
            elif on_this_node and last_heartbeat and last_heartbeat >= heartbeat_threshold:
                try:
                    os.kill(pid, 0)  # Signal 0 = check existence only
                except OSError:
                    is_dead = True
                    reason = "process not found on this node"

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

            # Send signal to process
            try:
                os.kill(pid, sig)
                stopped += 1
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

    def _kill_worker_process(self, pid: int) -> bool:
        """Kill a worker process by PID.

        Tries SIGTERM first (graceful), then SIGKILL after 5 seconds.

        Args:
            pid: Process ID to kill

        Returns:
            True if process was killed, False if already dead or error
        """
        import time

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
                try:
                    os.kill(pid, 0)
                except OSError:
                    # Process terminated
                    logger.info(f"Worker process {pid} terminated gracefully")
                    return True

            # Process still alive after 5s, send SIGKILL
            logger.warning(f"Worker process {pid} did not terminate, sending SIGKILL")
            os.kill(pid, signal.SIGKILL)

            # Wait briefly for SIGKILL
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
        from datetime import datetime, timezone, timedelta
        from ..compat import get_config

        now = datetime.now(timezone.utc)
        irregularities = {
            'dead_workers_cleaned': 0,
            'stalled_workers_killed': 0,
            'details': [],
        }

        # Get active workers on this node
        workers = self.backend.get_worker_heartbeats(active_only=True)
        node_workers = [w for w in workers if (
            (hasattr(w, 'node_id') and w.node_id == self.node_id) or
            (isinstance(w, dict) and w.get('node_id') == self.node_id)
        ) and (
            (hasattr(w, 'pid') and w.pid > 0) or
            (isinstance(w, dict) and w.get('pid', 0) > 0)
        )]

        # Only check: workers whose heartbeat is stale despite SIGUSR1.
        # The daemon sends SIGUSR1 every cycle (~10s). If a worker can't
        # respond for WORKER_ALIVE_TIMEOUT * 3, it's truly stuck.
        alive_timeout = get_config('WORKER_ALIVE_TIMEOUT', 30)
        stale_threshold = alive_timeout * 3

        for worker in node_workers:
            if hasattr(worker, 'last_heartbeat'):
                last_heartbeat = worker.last_heartbeat
            elif isinstance(worker, dict):
                from dateutil.parser import isoparse
                last_heartbeat = isoparse(worker['last_heartbeat']) if worker.get('last_heartbeat') else None
            else:
                continue

            if not last_heartbeat:
                continue

            heartbeat_age = (now - last_heartbeat).total_seconds()
            if heartbeat_age <= stale_threshold:
                continue

            pid = worker.pid if hasattr(worker, 'pid') else worker.get('pid')
            worker_id = str(worker.id) if hasattr(worker, 'id') else str(worker.get('id'))

            try:
                os.kill(pid, 0)
                process_exists = True
            except OSError:
                process_exists = False

            if not process_exists:
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
                killed = self._kill_worker_process(pid)
                if killed:
                    self.backend.update_worker_heartbeat(
                        worker_id=worker_id, status='dead', current_job_id=None
                    )
                    irregularities['stalled_workers_killed'] += 1
                    irregularities['details'].append({
                        'type': 'stalled_worker', 'worker_id': worker_id,
                        'pid': pid, 'heartbeat_age': heartbeat_age,
                        'reason': f'unresponsive_to_signals_{heartbeat_age:.0f}s',
                    })

        return irregularities
