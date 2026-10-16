"""Worker pool management for spawning and monitoring workers."""

import os
import signal
import sys
import subprocess
from pathlib import Path

from django.conf import settings as django_settings

from sqlery.core.log_config import is_debug_mode
from sqlery.django_sqlery.models import Worker
from sqlery.django_sqlery.worker_registry import (
    cleanup_dead_workers,
    count_active_workers,
    should_spawn_worker,
)
# from sqlery.django_sqlery.worker_claiming import get_node_id  # Promoted to core
from sqlery.core.claiming import get_node_id
from sqlery.django_sqlery.settings import get_setting


def spawn_worker():
    """Spawn a new worker subprocess.

    Returns:
        subprocess.Popen instance of the spawned worker
    """
    # Debug mode: redirect to raw log file (grows forever).
    # Normal mode: subprocess configures its own RotatingFileHandler.
    if is_debug_mode():
        # from django.conf import settings  # moved to top-level
        log_dir = Path(django_settings.BASE_DIR) / 'tmp'
        log_dir.mkdir(exist_ok=True)
        worker_log = log_dir / f'sqlery_worker_{os.getpid()}.log'
        worker_log_file = open(worker_log, 'a')
    else:
        worker_log_file = subprocess.DEVNULL

    # # Old: always redirect to raw log file (grows forever)
    # from django.conf import settings
    # log_dir = Path(settings.BASE_DIR) / 'tmp'
    # log_dir.mkdir(exist_ok=True)
    # worker_log = log_dir / f'sqlery_worker_{os.getpid()}.log'
    # worker_log_file = open(worker_log, 'a')

    # Run worker as module to preserve package structure for relative imports
    # Use -m to run as module: python -m sqlery.worker_process
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "sqlery.worker_process"],
            stdout=worker_log_file,
            stderr=worker_log_file,
            stdin=subprocess.DEVNULL,
            env=os.environ,
            start_new_session=True,
        )
    finally:
        # Popen dups the fd for the child; close the parent's handle to avoid leaking it.
        if worker_log_file is not subprocess.DEVNULL:
            worker_log_file.close()

    return process


def ensure_worker_pool():
    """Ensure the worker pool is at the desired size.

    - Cleans up dead workers
    - Spawns new workers if below MAX_WORKERS_PER_NODE
    - Called periodically by the daemon

    Returns:
        dict: Status information about workers spawned/cleaned
    """
    node_id = get_node_id()
    max_workers = get_setting("MAX_WORKERS_PER_NODE", 1)

    # Clean up dead workers first
    dead_count = cleanup_dead_workers(node_id)

    # Count current active workers
    current_count = count_active_workers(node_id)

    # Spawn workers if needed
    spawned = 0
    needed = max_workers - current_count

    for i in range(needed):
        spawn_worker()
        spawned += 1

    return {
        "node_id": node_id,
        "max_workers": max_workers,
        "current_workers": current_count + spawned,
        "spawned": spawned,
        "cleaned_up": dead_count,
    }


def stop_all_workers(node_id=None, force=False):
    """Stop all workers on a node.

    Args:
        node_id: Optional node ID (default: current node)
        force: If True, use SIGKILL instead of SIGTERM

    Returns:
        int: Number of workers stopped
    """
    # import signal  # moved to top-level
    # from .models import Worker  # moved to top-level

    if node_id is None:
        node_id = get_node_id()

    workers = Worker.objects.filter(
        node_id=node_id,
        status__in=["idle", "busy"]
    )

    stopped = 0
    sig = signal.SIGKILL if force else signal.SIGTERM

    for worker in workers:
        try:
            os.kill(worker.pid, sig)
            stopped += 1
        except ProcessLookupError:
            # Process already dead
            pass
        except PermissionError:
            # Can't kill process (different user?)
            pass

        # Mark as dead in database
        worker.status = "dead"
        worker.save()

    return stopped


def get_worker_pool_status(node_id=None):
    """Get status of worker pool.

    Args:
        node_id: Optional node ID (default: current node)

    Returns:
        dict: Worker pool status information
    """
    # from .models import Worker  # moved to top-level

    if node_id is None:
        node_id = get_node_id()

    max_workers = get_setting("MAX_WORKERS_PER_NODE", 1)

    workers = Worker.objects.filter(node_id=node_id)

    active = workers.filter(status__in=["idle", "busy"])
    idle = workers.filter(status="idle")
    busy = workers.filter(status="busy")
    dead = workers.filter(status="dead")

    return {
        "node_id": node_id,
        "max_workers": max_workers,
        "active_count": active.count(),
        "idle_count": idle.count(),
        "busy_count": busy.count(),
        "dead_count": dead.count(),
        "workers": list(
            active.values(
                "id",
                "pid",
                "status",
                "current_job_id",
                "jobs_processed",
                "started_at",
                "last_heartbeat",
            )
        ),
    }
