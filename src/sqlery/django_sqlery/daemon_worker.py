"""Background daemon worker for continuous job processing.

This daemon runs continuously in the background, checking for and processing
jobs at regular intervals without requiring HTTP requests.
"""

import os
import sys
import time
import signal
import logging
from pathlib import Path

# Ensure correct import path - avoid shadowing django package with local django/ directory
# When this script is run directly, __file__ is /src/sqlery/daemon_worker.py
# We need to ensure that /src/sqlery is NOT in sys.path[0] to avoid import conflicts
script_dir = str(Path(__file__).parent.resolve())
if sys.path and sys.path[0] == script_dir:
    # Remove script directory from sys.path to avoid local 'django' directory shadowing real django
    sys.path.pop(0)

# Add current working directory to sys.path so Django project can be imported
# This allows importing mysite (or any Django project) from the daemon
cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

# Setup Django before importing models
# Get settings module from environment (set by parent process)
# If not set, this will fail with a clear error message
if 'DJANGO_SETTINGS_MODULE' not in os.environ:
    raise RuntimeError(
        "DJANGO_SETTINGS_MODULE not set. The daemon worker must be spawned "
        "from a Django process that has DJANGO_SETTINGS_MODULE configured."
    )

import django
django.setup()

from sqlery.django_sqlery.executor import TaskExecutor
from sqlery.django_sqlery.settings import get_setting
from sqlery.django_sqlery.worker_registry import cleanup_dead_workers
from sqlery.worker_pool import ensure_worker_pool, stop_all_workers, get_worker_pool_status

logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name} signal, initiating graceful shutdown...")
    shutdown_requested = True


def get_pid_file_path() -> Path:
    """Get path to PID file."""
    from django.conf import settings
    pid_dir = Path(settings.BASE_DIR) / 'tmp'
    pid_dir.mkdir(exist_ok=True)
    return pid_dir / 'sqlery_daemon.pid'


def write_pid_file():
    """Write current process PID to file."""
    pid_file = get_pid_file_path()
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    logger.info(f"Daemon started with PID {os.getpid()}")


def remove_pid_file():
    """Remove PID file on shutdown."""
    pid_file = get_pid_file_path()
    try:
        pid_file.unlink(missing_ok=True)
        logger.info("PID file removed")
    except Exception as e:
        logger.error(f"Failed to remove PID file: {e}")


def write_heartbeat():
    """Write heartbeat timestamp for monitoring."""
    from django.conf import settings
    heartbeat_file = Path(settings.BASE_DIR) / 'tmp' / 'sqlery_daemon.heartbeat'
    try:
        with open(heartbeat_file, 'w') as f:
            f.write(str(int(time.time())))
    except Exception as e:
        logger.error(f"Failed to write heartbeat: {e}")


def run_daemon():
    """Main daemon loop - manages worker pool and runs scheduler.

    Always uses worker pool (even with max_workers=1). Workers run as
    independent subprocesses, keeping the daemon loop free for scheduling,
    cleanup, and health monitoring.
    """
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Write PID file
    write_pid_file()

    # Get configuration
    check_interval = get_setting('DAEMON_CHECK_INTERVAL', 10)
    max_workers = get_setting('MAX_WORKERS_PER_NODE', 1)

    logger.info(f"Daemon started (workers: {max_workers}, interval: {check_interval}s)")

    executor = TaskExecutor()

    try:
        # Initial worker pool setup
        pool_status = ensure_worker_pool()
        logger.info(f"Worker pool initialized: {pool_status['spawned']} workers spawned")

        while not shutdown_requested:
            try:
                # Run scheduler (create jobs from scheduled tasks)
                jobs_created = executor.run_due_tasks()
                if jobs_created:
                    logger.info(f"Scheduler created {len(jobs_created)} jobs")

                # Clean up dead workers (stale heartbeats from previous runs)
                dead_count = cleanup_dead_workers()
                if dead_count > 0:
                    logger.info(f"Cleaned up {dead_count} dead workers")

                # Ensure worker pool is healthy
                pool_status = ensure_worker_pool()
                if pool_status['spawned'] > 0:
                    logger.info(f"Spawned {pool_status['spawned']} replacement workers")

                # Write heartbeat
                write_heartbeat()

                # Sleep until next check
                # Use short sleep intervals to allow responsive shutdown
                elapsed = 0
                while elapsed < check_interval and not shutdown_requested:
                    time.sleep(1)
                    elapsed += 1

            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt")
                break
            except Exception as e:
                logger.error(f"Error in daemon loop: {e}", exc_info=True)
                time.sleep(5)  # Brief pause before retrying

    finally:
        # Cleanup
        logger.info("Daemon shutting down...")

        logger.info("Stopping all workers...")
        stopped = stop_all_workers()
        logger.info(f"Stopped {stopped} workers")

        remove_pid_file()
        logger.info("Daemon stopped")


if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    run_daemon()
