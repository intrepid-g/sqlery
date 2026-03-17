"""Django-agnostic daemon process management."""

import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from pathlib import Path
# from typing import Optional  # Replaced with X | None (Python 3.10+)

logger = logging.getLogger(__name__)


def _should_run_cleanup(last_run: datetime | None, interval_hours: int = 6) -> bool:
    """Return True if enough time has passed since last cleanup run."""
    if last_run is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last_run).total_seconds()
    return elapsed >= interval_hours * 3600


class DaemonManager:
    """Manages daemon process lifecycle for background job processing.

    Works in both Django and standalone modes.
    """

    def __init__(self, pid_dir: Path | None = None):
        """Initialize daemon manager.

        Args:
            pid_dir: Directory for PID and heartbeat files (default: /tmp or BASE_DIR/tmp)
        """
        if pid_dir is None:
            pid_dir = self._get_default_pid_dir()

        self.pid_dir = Path(pid_dir)
        self.pid_dir.mkdir(parents=True, exist_ok=True)

        self.pid_file = self.pid_dir / 'sqlery_daemon.pid'
        self.heartbeat_file = self.pid_dir / 'sqlery_daemon.heartbeat'

    def _get_default_pid_dir(self) -> Path:
        """Get default PID directory based on mode."""
        from ..compat import is_django_mode

        if is_django_mode():
            from django.conf import settings
            return Path(settings.BASE_DIR) / 'tmp'
        else:
            return Path('/tmp/sqlery')

    def read_pid(self) -> int | None:
        """Read PID from file.

        Returns:
            PID as integer, or None if file doesn't exist or is invalid
        """
        if not self.pid_file.exists():
            return None

        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
                return pid
        except (ValueError, OSError) as e:
            logger.warning(f"Failed to read PID file: {e}")
            return None

    def write_pid(self, pid: int):
        """Write PID to file.

        Args:
            pid: Process ID to write
        """
        with open(self.pid_file, 'w') as f:
            f.write(str(pid))

    def remove_pid(self):
        """Remove PID file."""
        try:
            self.pid_file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Failed to remove PID file: {e}")

    def is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running.

        Args:
            pid: Process ID to check

        Returns:
            True if process exists, False otherwise
        """
        try:
            # Send signal 0 - doesn't kill, just checks if process exists
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def is_running(self) -> bool:
        """Check if daemon is currently running.

        Returns:
            True if daemon process is active, False otherwise
        """
        pid = self.read_pid()

        if pid is None:
            return False

        return self.is_process_running(pid)

    def status(self) -> dict:
        """Get detailed daemon status information.

        Returns:
            Dictionary with status information
        """
        pid = self.read_pid()
        running = False
        heartbeat_age = None
        stale = False

        if pid is not None:
            running = self.is_process_running(pid)

        # Check heartbeat
        if self.heartbeat_file.exists():
            try:
                with open(self.heartbeat_file, 'r') as f:
                    heartbeat_ts = int(f.read().strip())
                    heartbeat_age = int(time.time()) - heartbeat_ts

                    # Consider stale if no heartbeat for 5 minutes
                    if heartbeat_age > 300:
                        stale = True
            except (ValueError, OSError) as e:
                logger.warning(f"Failed to read heartbeat file: {e}")

        # Get worker count if running
        worker_count = 0
        if running:
            from ..compat import get_backend
            backend = get_backend()
            workers = backend.get_worker_heartbeats(active_only=True)
            worker_count = len(workers)

        return {
            'running': running,
            'pid': pid,
            'heartbeat_age': heartbeat_age,
            'stale': stale,
            'worker_count': worker_count,
        }

    def spawn_daemon(self, daemon_script_path: Path | None = None):
        """Spawn daemon as subprocess (for use by middleware/frameworks).

        This is the recommended way to start the daemon from Django/FastAPI middleware.
        Uses subprocess instead of fork() for better compatibility.

        Args:
            daemon_script_path: Optional path to daemon runner script
                              Defaults to core/daemon_runner.py

        Returns:
            subprocess.Popen instance, or None if already running
        """
        import subprocess

        # Check if already running
        if self.is_running():
            logger.info("Daemon already running")
            return None

        # Clean up stale files
        self.cleanup_stale()

        # Determine daemon script
        if daemon_script_path is None:
            # Use core daemon runner
            daemon_script_path = Path(__file__).parent / 'daemon_runner.py'

        # Get log file path
        log_file_path = self.pid_dir / 'sqlery_daemon.log'

        # Open log file for daemon output
        try:
            log_file = open(log_file_path, 'a')
        except Exception as e:
            logger.warning(f"Failed to open log file {log_file_path}: {e}")
            log_file = subprocess.DEVNULL

        # Spawn subprocess
        try:
            process = subprocess.Popen(
                [sys.executable, str(daemon_script_path)],
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
                start_new_session=True,
                close_fds=True,
            )

            logger.info(f"Spawned daemon process: PID {process.pid}")
            return process

        except Exception as e:
            logger.error(f"Failed to spawn daemon: {e}")
            if log_file != subprocess.DEVNULL:
                log_file.close()
            raise

    def start(self, detach: bool = True):
        """Start the daemon process.

        Args:
            detach: If True, run as background daemon. If False, run in foreground.
        """
        if self.is_running():
            pid = self.read_pid()
            raise RuntimeError(f"Daemon is already running (PID: {pid})")

        # Clean up stale files
        self.cleanup_stale()

        if detach:
            # Fork and detach
            self._daemonize()
        else:
            # Run in foreground
            self._run_daemon()

    def _daemonize(self):
        """Fork and run daemon in background."""
        # First fork
        try:
            pid = os.fork()
            if pid > 0:
                # Parent process - exit
                sys.exit(0)
        except OSError as e:
            raise RuntimeError(f"Fork failed: {e}")

        # Decouple from parent environment
        os.chdir('/')
        os.setsid()
        os.umask(0)

        # Second fork
        try:
            pid = os.fork()
            if pid > 0:
                # First child - exit
                sys.exit(0)
        except OSError as e:
            raise RuntimeError(f"Second fork failed: {e}")

        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()

        with open('/dev/null', 'r') as devnull_r:
            os.dup2(devnull_r.fileno(), sys.stdin.fileno())

        with open('/dev/null', 'a+') as devnull_w:
            os.dup2(devnull_w.fileno(), sys.stdout.fileno())
            os.dup2(devnull_w.fileno(), sys.stderr.fileno())

        # Write PID file
        self.write_pid(os.getpid())

        # Run daemon
        self._run_daemon()

    def _run_daemon(self, max_workers: int | None = None):
        """Main daemon loop - manages worker pool and scheduler.

        Runs continuously, performing these tasks each cycle:
        1. Update daemon heartbeat in database
        2. Renew / acquire DB-backed queue leases
        3. Run scheduler for owned queues (create jobs from scheduled tasks)
        4. Manage worker pool (ensure desired number of workers are running)
        5. Sleep until next cycle

        Args:
            max_workers: Number of worker subprocess processes to maintain.
                None falls back to config then default of 1.

        Supports graceful shutdown on SIGTERM/SIGINT.
        """
        # import fcntl  # Removed: replaced with DB-backed queue leases

        from ..compat import get_config, get_backend
        from .scheduler import Scheduler
        from .worker_pool import WorkerPoolManager

        # Configure DB resilience (WAL mode, busy_timeout, statement_timeout, etc.)
        from sqlery.core.db_resilience import configure_connection_resilience
        configure_connection_resilience()

        # Get configuration
        check_interval = get_config('DAEMON_CHECK_INTERVAL', 10)
        if max_workers is None:
            max_workers = get_config('MAX_WORKERS_PER_NODE', 1)
        queues = get_config('WORKER_QUEUES', ['default'])

        # Initialize components
        backend = get_backend()
        scheduler = Scheduler(backend=backend)

        # --- DB-backed queue leases (replaces file lock) ---
        # Unique daemon identifier: format daemon_{node_id}_{pid}
        daemon_id = f"daemon_{self.node_id}_{os.getpid()}"
        # A lease lives for 3 missed heartbeats before it's considered dead
        lease_secs = check_interval * 3

        owned_queues = set(backend.claim_queue_leases(
            queues, daemon_id, self.node_id, os.getpid(), lease_secs
        ))
        unowned = set(queues) - owned_queues
        if unowned:
            logger.info(
                f"Queues {sorted(unowned)} held by live daemons — will retry each cycle."
            )
        logger.info(
            f"Daemon starting (workers: {max_workers}, queues: {queues}). "
            f"Scheduler responsibility: {sorted(owned_queues) or 'none yet'}"
        )

        worker_pool = WorkerPoolManager(max_workers, queues, backend)

        # On startup: Clean up stale workers from ALL nodes (handles container restarts)
        # Workers with heartbeats older than the alive timeout are considered dead
        self._cleanup_stale_workers_all_nodes(backend)

        # Track when we last purged dead worker rows
        dead_worker_purge_interval = 120  # Keep dead workers visible for 2 minutes

        # Auto-cleanup config
        auto_cleanup = get_config('AUTO_CLEANUP_JOBS', True)
        last_cleanup_at: datetime | None = None

        # Track shutdown state
        shutdown_requested = False

        # Set up signal handlers
        def signal_handler(signum, frame):
            nonlocal shutdown_requested
            logger.info(f"Received signal {signum}, shutting down...")
            shutdown_requested = True

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        try:
            # Main daemon loop
            while not shutdown_requested:
                # Update daemon heartbeat in database
                self._update_heartbeat_db(backend)

                # --- Lease management (every iteration) ---
                # Renew leases we already hold
                if owned_queues:
                    backend.renew_queue_leases(sorted(owned_queues), daemon_id, lease_secs)

                # Try to claim any queues we don't yet own (previous holder may have died)
                unowned = set(queues) - owned_queues
                if unowned:
                    newly_claimed = set(backend.claim_queue_leases(
                        sorted(unowned), daemon_id, self.node_id, os.getpid(), lease_secs
                    ))
                    if newly_claimed:
                        owned_queues |= newly_claimed
                        logger.info(f"Acquired scheduler leases for: {sorted(newly_claimed)}")

                # Update heartbeats for live worker processes.
                # The daemon is the "always on" parent — it refreshes each
                # worker's heartbeat as long as the worker PID is alive,
                # so a busy worker blocked on a long job stays healthy.
                try:
                    self._heartbeat_workers(backend, worker_pool)
                except Exception as e:
                    logger.error(f"Worker heartbeat error: {e}", exc_info=True)

                # --- Coordinator work — scoped to owned queues only ---
                # Run scheduler (create jobs from scheduled tasks)
                try:
                    jobs = scheduler.run_due_tasks(queue_names=owned_queues)
                    if jobs:
                        logger.info(f"Scheduler created {len(jobs)} jobs")
                except Exception as e:
                    logger.error(f"Scheduler error: {e}", exc_info=True)

                # Validate running jobs — fail any whose worker process is dead
                try:
                    self._fail_zombie_running_jobs(backend, queue_names=owned_queues)
                except Exception as e:
                    logger.error(f"Zombie job cleanup error: {e}", exc_info=True)

                # --- Worker pool — always uses full configured queues ---
                # Workers handle all of `queues`, not just owned_queues.
                # FOR UPDATE SKIP LOCKED handles concurrency at the DB level.
                try:
                    status = worker_pool.ensure_workers()
                    if status['spawned'] > 0:
                        logger.info(f"Spawned {status['spawned']} workers")
                    if status['cleaned_up'] > 0:
                        logger.info(f"Cleaned up {status['cleaned_up']} dead workers")
                except Exception as e:
                    logger.error(f"Worker pool error: {e}", exc_info=True)

                # Detect and fix irregularities (stuck workers, timed out jobs, etc.)
                try:
                    irregularities = worker_pool.detect_and_fix_irregularities()
                    if any(irregularities.values()):
                        logger.warning(f"Detected and fixed irregularities: {irregularities}")
                except Exception as e:
                    logger.error(f"Irregularity detection error: {e}", exc_info=True)

                # Purge dead worker rows after grace period
                try:
                    self._purge_dead_workers(backend, max_age_seconds=dead_worker_purge_interval)
                except Exception as e:
                    logger.error(f"Dead worker purge error: {e}", exc_info=True)

                # Periodic auto-cleanup (every 6 hours)
                if auto_cleanup and _should_run_cleanup(last_cleanup_at, interval_hours=6):
                    try:
                        from .cleanup import CleanupManager
                        CleanupManager().auto_cleanup()
                        last_cleanup_at = datetime.now(timezone.utc)
                        logger.info("Periodic auto-cleanup completed")
                    except Exception as e:
                        logger.error(f"Auto-cleanup error: {e}", exc_info=True)

                # Sleep until next cycle (with responsive shutdown checking)
                elapsed = 0
                while elapsed < check_interval and not shutdown_requested:
                    time.sleep(1)
                    elapsed += 1

        except KeyboardInterrupt:
            logger.info("Daemon interrupted by keyboard")
            shutdown_requested = True
        finally:
            # Cleanup
            logger.info("Daemon shutting down...")

            logger.info("Stopping all workers...")
            try:
                stopped = worker_pool.stop_all_workers()
                logger.info(f"Stopped {stopped} workers")
            except Exception as e:
                logger.error(f"Error stopping workers: {e}")

            # Release queue leases on clean shutdown
            try:
                backend.release_queue_leases(sorted(owned_queues), daemon_id)
            except Exception as e:
                logger.error(f"Error releasing queue leases: {e}")

            # Remove PID file
            self.remove_pid()

            # Remove file-based heartbeat (deprecated but cleanup anyway)
            try:
                self.heartbeat_file.unlink(missing_ok=True)
            except Exception:
                pass

            # # Old: release file lock
            # if hasattr(self, '_lock_file') and self._lock_file:
            #     try:
            #         fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            #         self._lock_file.close()
            #     except Exception:
            #         pass

            logger.info("Daemon stopped")

    # _execute_job_in_child removed — single-worker inline mode eliminated
    # in favor of always using WorkerPoolManager (even with max_workers=1).
    # Workers now run as independent subprocesses via worker_runner.

    @staticmethod
    def _fail_zombie_running_jobs(backend, queue_names=None):
        """Fail running jobs that are no longer being executed.

        Checks every job in 'running' state for the given queues:
        1. worker_pid is set but that PID doesn't exist → worker crashed
        2. No worker assigned at all → job was never properly claimed
        3. Assigned worker is marked dead → worker was killed/crashed
        4. Worker's current_job points elsewhere → worker moved on, job abandoned
        5. Worker heartbeat is stale → worker unresponsive (uses WORKER_ALIVE_TIMEOUT)

        Args:
            backend: Database backend instance
            queue_names: If provided, only check jobs in these queues.
                         None means check all queues.
        """
        import os

        try:
            from ..django_sqlery.models import QueuedJob
        except Exception:
            return

        from ..compat import get_config
        # Heartbeat stale threshold: the daemon sends SIGUSR1 every cycle
        # (~DAEMON_CHECK_INTERVAL seconds). If the worker can't respond for
        # this many seconds, something is wrong.
        alive_timeout = get_config('WORKER_ALIVE_TIMEOUT', 30)
        # Use 3x alive_timeout as the stale threshold to allow for timing
        # jitter between daemon cycles and worker signal handling.
        stale_threshold = alive_timeout * 3

        import socket
        current_node = os.environ.get("NODE_ID", socket.gethostname())

        running_jobs_qs = QueuedJob.objects.filter(status='running')
        if queue_names:
            running_jobs_qs = running_jobs_qs.filter(queue_name__in=queue_names)
        running_jobs = running_jobs_qs.select_related('worker')
        failed_count = 0

        for job in running_jobs:
            reason = None

            # Check 1: worker_pid doesn't exist on this machine
            # # Old: checked os.kill for ALL jobs globally — incorrect on multi-node
            # if job.worker_pid:
            #     try:
            #         os.kill(job.worker_pid, 0)
            #     except OSError:
            #         reason = f"Worker process PID {job.worker_pid} no longer exists"
            if job.worker_pid:
                job_node = job.worker.node_id if job.worker else None
                if job_node == current_node:
                    try:
                        os.kill(job.worker_pid, 0)
                    except OSError:
                        reason = f"Worker PID {job.worker_pid} no longer exists on {current_node}"
                # Remote nodes: rely on heartbeat staleness (Check 5)

            # Check 2: running job has no worker assigned
            if not reason and not job.worker:
                reason = "Running job has no worker assigned"

            # Check 3: assigned worker is marked dead
            if not reason and job.worker and job.worker.status == 'dead':
                reason = f"Assigned worker {job.worker.friendly_name} is dead"

            # Check 4: worker moved on to a different job or is idle (this job is abandoned)
            if not reason and job.worker and job.worker.current_job_id != job.id:
                from django.utils import timezone
                from datetime import timedelta
                if job.worker.current_job_id:
                    reason = (
                        f"Worker {job.worker.friendly_name} moved on to job "
                        f"#{job.worker.current_job_id} — this job was abandoned"
                    )
                # Worker is idle (current_job_id=None) but job still running.
                # Grace period: only flag if running longer than alive_timeout
                # to avoid false positives during the brief claim→heartbeat window.
                elif job.started_at and job.started_at < timezone.now() - timedelta(seconds=alive_timeout):
                    age = int((timezone.now() - job.started_at).total_seconds())
                    reason = (
                        f"Worker {job.worker.friendly_name} is idle but job has been "
                        f"running for {age}s — zombie"
                    )

            # Check 5: assigned worker heartbeat is stale
            if not reason and job.worker:
                from django.utils import timezone
                from datetime import timedelta
                if job.worker.last_heartbeat and job.worker.last_heartbeat < timezone.now() - timedelta(seconds=stale_threshold):
                    age = int((timezone.now() - job.worker.last_heartbeat).total_seconds())
                    reason = (
                        f"Worker {job.worker.friendly_name} heartbeat stale "
                        f"({age}s old, threshold {stale_threshold}s)"
                    )

            if reason:
                try:
                    job.mark_failed(
                        error=reason,
                        termination_reason="zombie_job",
                    )
                    failed_count += 1
                    logger.info(f"Failed zombie running job #{job.id}: {reason}")
                except Exception as e:
                    logger.error(f"Failed to mark zombie job #{job.id}: {e}")

        if failed_count > 0:
            logger.info(f"Cleaned up {failed_count} zombie running jobs")

    def _fail_orphaned_jobs_for_worker(self, backend, inline_worker_id):
        """Fail running jobs assigned to our worker from a previous process life.

        When a container restarts and gets the same PID, the Worker row is
        reused via update_or_create. Any jobs left in 'running' state from
        the previous process are orphaned — they'll never complete.

        Args:
            backend: Database backend instance
            inline_worker_id: Worker ID string (e.g. "worker_host_9"), or None to skip
        """
        if not inline_worker_id:
            return

        try:
            # Resolve worker_id to a Worker model instance
            worker_row = backend._resolve_worker(inline_worker_id)
            if not worker_row:
                return

            from ..django_sqlery.models import QueuedJob
            orphaned = QueuedJob.objects.filter(worker=worker_row, status='running')
            count = 0
            for job in orphaned:
                job.mark_failed(
                    error="Worker restarted — job orphaned from previous process",
                    termination_reason="worker_restarted",
                )
                count += 1

            if count > 0:
                logger.info(f"Startup: Failed {count} orphaned running jobs from previous worker life")

        except Exception as e:
            logger.warning(f"Failed to clean orphaned jobs on startup: {e}")

    def _cleanup_stale_workers_all_nodes(self, backend):
        """Clean up stale workers from ALL nodes (not just current node).

        This is called once on daemon startup to handle cases where containers
        are restarted and get new hostnames, leaving old worker records orphaned.

        Workers are considered stale if:
        - Their heartbeat is older than 1 hour
        - They're marked as idle/busy (not dead)

        Args:
            backend: Database backend instance
        """
        from datetime import datetime, timedelta, timezone

        try:
            # Get all workers (not just from current node)
            # Use active_only=False to get workers with old heartbeats too
            workers = backend.get_worker_heartbeats(active_only=False)

            # Use configured alive timeout (default 30s)
            from ..compat import get_config
            alive_timeout = get_config('WORKER_ALIVE_TIMEOUT', 30)
            threshold = datetime.now(timezone.utc) - timedelta(seconds=alive_timeout)
            cleaned = 0

            for worker in workers:
                # Skip daemon processes (PID=0)
                if hasattr(worker, 'pid') and worker.pid == 0:
                    continue
                elif isinstance(worker, dict) and worker.get('pid') == 0:
                    continue

                # Get last heartbeat
                if hasattr(worker, 'last_heartbeat'):
                    last_heartbeat = worker.last_heartbeat
                    worker_id = str(worker.id)
                elif isinstance(worker, dict):
                    last_heartbeat = worker.get('last_heartbeat')
                    worker_id = str(worker.get('id'))
                else:
                    continue

                # Skip workers already marked dead
                worker_status = getattr(worker, 'status', None) or (worker.get('status') if isinstance(worker, dict) else None)
                if worker_status == 'dead':
                    continue

                # Check if heartbeat is too old
                if last_heartbeat and last_heartbeat < threshold:
                    # Fail any running jobs assigned to this worker
                    try:
                        from ..django_sqlery.models import QueuedJob
                        orphaned_jobs = QueuedJob.objects.filter(
                            worker_id=worker_id,
                            status='running',
                        )
                        for job in orphaned_jobs:
                            job.mark_failed(
                                error="Worker died — no heartbeat",
                                termination_reason="worker_dead",
                            )
                            logger.warning(f"Failed orphaned job {job.id} from dead worker {worker_id}")
                    except Exception as e:
                        logger.error(f"Failed to clean orphaned jobs for worker {worker_id}: {e}")

                    # Mark worker as dead
                    backend.update_worker_heartbeat(
                        worker_id=worker_id,
                        status='dead',
                        current_job_id=None
                    )
                    cleaned += 1

            if cleaned > 0:
                logger.info(f"Cleanup: Marked {cleaned} stale workers as dead (heartbeat > {alive_timeout}s old)")

        except Exception as e:
            logger.warning(f"Failed to cleanup stale workers on startup: {e}")

    def _purge_dead_workers(self, backend, max_age_seconds=120):
        """Delete worker rows that have been dead for longer than max_age_seconds.

        Keeps dead workers visible in the dashboard for a grace period before removing them.

        Args:
            backend: Database backend instance
            max_age_seconds: How long to keep dead worker rows (default: 120s / 2 minutes)
        """
        from datetime import datetime, timedelta, timezone

        try:
            workers = backend.get_worker_heartbeats(active_only=False)
            threshold = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
            purged = 0

            for worker in workers:
                # Only purge workers already marked as dead
                status = getattr(worker, 'status', None) or (worker.get('status') if isinstance(worker, dict) else None)
                if status != 'dead':
                    continue

                if hasattr(worker, 'last_heartbeat'):
                    last_hb = worker.last_heartbeat
                elif isinstance(worker, dict):
                    last_hb = worker.get('last_heartbeat')
                else:
                    continue

                if last_hb and last_hb < threshold:
                    try:
                        worker.delete()
                        purged += 1
                    except Exception:
                        pass

            if purged > 0:
                logger.info(f"Purged {purged} dead worker rows (older than {max_age_seconds}s)")

        except Exception as e:
            logger.warning(f"Failed to purge dead workers: {e}")

    def _heartbeat_workers(self, backend, worker_pool):
        """Request heartbeats from live worker processes on this node.

        Sends SIGUSR1 to each worker — the worker's signal handler responds
        by writing its actual state (busy + current job, or idle) to the DB.
        This proves the Python interpreter is responsive and reports exactly
        what the worker is doing right now.

        If the PID is gone, marks the worker as dead immediately.
        """
        workers = backend.get_worker_heartbeats(active_only=True)

        for worker in workers:
            # Extract node_id and pid
            if hasattr(worker, 'node_id'):
                w_node = worker.node_id
                w_pid = worker.pid
                w_id = worker.id
            elif isinstance(worker, dict):
                w_node = worker.get('node_id')
                w_pid = worker.get('pid', 0)
                w_id = worker.get('id')
            else:
                continue

            # Only manage workers on this node (skip daemon rows and other nodes)
            if w_node != self.node_id or w_pid <= 0:
                continue

            if self.is_process_running(w_pid):
                # PID is alive — send SIGUSR1 so the worker can report its
                # own accurate state (status + current_job) to the DB.
                try:
                    os.kill(w_pid, signal.SIGUSR1)
                except OSError as e:
                    logger.warning(f"Failed to send SIGUSR1 to worker PID {w_pid}: {e}")
                # # Old: daemon wrote status and current_job_id, racing with
                # # worker's own SIGUSR1 handler and overwriting accurate state
                # # with stale data.
                # current_status = getattr(worker, 'status', None) or (worker.get('status') if isinstance(worker, dict) else None)
                # current_job_id = getattr(worker, 'current_job_id', None) or (worker.get('current_job_id') if isinstance(worker, dict) else None)
                # backend.update_worker_heartbeat(
                #     worker_id=str(w_id),
                #     status=current_status or 'busy',
                #     current_job_id=current_job_id,
                # )
                # Bump heartbeat timestamp only — worker owns status/current_job.
                # This keeps the worker alive even if SIGUSR1 can't be delivered
                # (e.g. blocked in C-level call).
                backend.refresh_worker_heartbeat(w_id)
            else:
                # PID gone — mark dead and clear current_job
                backend.update_worker_heartbeat(
                    worker_id=str(w_id),
                    status='dead',
                    current_job_id=None,
                )
                logger.warning(f"Worker {w_id} (PID {w_pid}) is dead, marked accordingly")

    def _update_heartbeat_db(self, backend):
        """Update daemon heartbeat in database using Worker model.

        Replaces file-based heartbeat with database-backed approach.
        This allows monitoring daemon health across multiple nodes.

        Args:
            backend: Database backend instance
        """
        daemon_worker_id = f"daemon_{self.node_id}"

        try:
            backend.update_worker_heartbeat(
                worker_id=daemon_worker_id,
                status='idle',  # Daemon is always "idle" (workers do the work)
                current_job_id=None
            )
        except Exception as e:
            logger.warning(f"Failed to update daemon heartbeat in database: {e}")

            # Fallback to file-based heartbeat if DB fails
            try:
                with open(self.heartbeat_file, 'w') as f:
                    f.write(str(int(time.time())))
            except Exception as e2:
                logger.error(f"Failed to write file heartbeat: {e2}")

    @property
    def node_id(self) -> str:
        """Get node identifier for this daemon.

        Returns:
            Hostname of the current machine
        """
        import socket
        return socket.gethostname()

    def stop(self, force: bool = False) -> bool:
        """Stop the daemon process.

        Args:
            force: If True, use SIGKILL instead of SIGTERM

        Returns:
            True if daemon was stopped, False if not running
        """
        pid = self.read_pid()

        if pid is None:
            logger.info("No PID file found - daemon not running")
            return False

        if not self.is_process_running(pid):
            logger.info(f"Daemon with PID {pid} not running (stale PID file)")
            self.cleanup_stale()
            return False

        try:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)

            signal_name = "SIGKILL" if force else "SIGTERM"
            logger.info(f"Sent {signal_name} to daemon process {pid}")

            # Wait for process to exit (max 10 seconds)
            for _ in range(10):
                time.sleep(1)
                if not self.is_process_running(pid):
                    logger.info(f"Daemon process {pid} stopped")
                    self.cleanup_stale()
                    return True

            # If still running after SIGTERM, force kill
            if not force and self.is_process_running(pid):
                logger.warning(f"Daemon didn't stop gracefully, force killing...")
                return self.stop(force=True)

            return True

        except OSError as e:
            logger.error(f"Failed to stop daemon: {e}")
            return False

    def restart(self):
        """Restart the daemon process."""
        if self.is_running():
            logger.info("Stopping existing daemon...")
            self.stop()

            # Wait a moment for cleanup
            time.sleep(1)

        logger.info("Starting daemon...")
        self.start(detach=True)

    def cleanup_stale(self) -> bool:
        """Remove PID and heartbeat files if process is not running.

        Returns:
            True if cleaned up, False if daemon is still running
        """
        pid = self.read_pid()

        if pid is None:
            return True  # No PID file, nothing to clean

        if self.is_process_running(pid):
            return False  # Daemon still running, don't remove

        # Process not running, remove stale files
        try:
            self.pid_file.unlink(missing_ok=True)
            self.heartbeat_file.unlink(missing_ok=True)
            logger.info(f"Cleaned up stale PID file for process {pid}")
            return True
        except Exception as e:
            logger.error(f"Failed to cleanup stale files: {e}")
            return False
