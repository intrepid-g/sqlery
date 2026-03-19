"""Worker registration and lifecycle management."""

import logging
import os
import signal

from django.utils import timezone

from .models import QueuedJob, Worker
from .settings import get_setting
from .worker_claiming import get_node_id


def register_worker(queues=None):
    """Register a new worker in the database.

    Args:
        queues: List of queue names this worker handles (default: from settings)

    Returns:
        Worker instance
    """
    if queues is None:
        queues = get_setting("WORKER_QUEUES", ["default"])

    worker = Worker.objects.create(
        node_id=get_node_id(),
        pid=os.getpid(),
        status="idle",
        queues=queues,
    )

    return worker


def unregister_worker(worker):
    """Unregister a worker from the database.

    Args:
        worker: Worker instance to unregister
    """
    # If worker has a current job, mark it as failed
    if worker.current_job:
        job = worker.current_job
        job.status = "failed"
        job.error = "Worker terminated unexpectedly"
        job.finished_at = timezone.now()
        if job.started_at:
            job.duration_seconds = (
                job.finished_at - job.started_at
            ).total_seconds()
        job.save()

    # Delete worker record
    worker.delete()


def update_heartbeat(worker):
    """Update worker heartbeat timestamp.

    Args:
        worker: Worker instance
    """
    # Simply save the worker - last_heartbeat has auto_now=True
    worker.save(update_fields=["last_heartbeat"])


def cleanup_dead_workers(node_id=None, timeout_seconds=None):
    """Clean up workers that haven't sent heartbeat within timeout.

    Args:
        node_id: Optional node ID to limit cleanup to specific node
        timeout_seconds: Timeout in seconds (default: from settings)

    Returns:
        Number of workers cleaned up
    """
    if timeout_seconds is None:
        timeout_seconds = get_setting("WORKER_ALIVE_TIMEOUT", 30)

    threshold = timezone.now() - timezone.timedelta(seconds=timeout_seconds)

    # Find dead workers
    query = Worker.objects.filter(
        status__in=["idle", "busy"],
        last_heartbeat__lt=threshold
    )

    if node_id:
        query = query.filter(node_id=node_id)

    dead_workers = list(query)

    # Mark their current jobs as failed
    for worker in dead_workers:
        if worker.current_job:
            job = worker.current_job
            job.status = "failed"
            job.error = "Worker died/timeout - no heartbeat"
            job.finished_at = timezone.now()
            if job.started_at:
                job.duration_seconds = (
                    job.finished_at - job.started_at
                ).total_seconds()
            job.save()

    # Mark workers as dead (or delete them)
    query.update(status="dead", current_job=None)

    # Optionally: Delete dead workers entirely
    # query.delete()

    # Clean up ghost "running" jobs — jobs stuck in running status with no
    # active worker pointing at them (e.g. from a previous crash / force-stop).
    # from .models import QueuedJob  # moved to top-level
    active_worker_job_ids = set(
        Worker.objects.filter(status__in=["idle", "busy"])
        .exclude(current_job__isnull=True)
        .values_list("current_job_id", flat=True)
    )
    ghost_running = QueuedJob.objects.filter(status="running").exclude(
        id__in=active_worker_job_ids
    )
    ghost_count = ghost_running.count()
    if ghost_count:
        # import logging as _logging  # moved to top-level
        logging.getLogger(__name__).warning(
            f"Marking {ghost_count} ghost running job(s) as failed (no active worker)"
        )
        ghost_running.update(
            status="failed",
            error="Ghost job: was stuck in running state with no active worker",
            finished_at=timezone.now(),
        )

    return len(dead_workers)


def kill_worker(worker_id, force=False):
    """Kill a specific worker process.

    Args:
        worker_id: UUID of worker to kill
        force: If True, use SIGKILL instead of SIGTERM

    Returns:
        True if worker was killed, False if not found or already dead
    """
    try:
        worker = Worker.objects.get(id=worker_id)
    except Worker.DoesNotExist:
        return False

    if worker.status == "dead":
        return False

    # Check if process exists
    try:
        if force:
            os.kill(worker.pid, signal.SIGKILL)
        else:
            os.kill(worker.pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        # Process doesn't exist - mark as dead
        worker.status = "dead"
        worker.save()
        return False


def get_active_workers(node_id=None):
    """Get all active workers, optionally filtered by node.

    Args:
        node_id: Optional node ID to filter by

    Returns:
        QuerySet of active Worker instances
    """
    query = Worker.objects.filter(status__in=["idle", "busy"])

    if node_id:
        query = query.filter(node_id=node_id)

    return query


def count_active_workers(node_id=None):
    """Count active workers on a node.

    Args:
        node_id: Optional node ID (default: current node)

    Returns:
        int: Number of active workers
    """
    if node_id is None:
        node_id = get_node_id()

    return get_active_workers(node_id).count()


def should_spawn_worker(node_id=None):
    """Check if we should spawn a new worker.

    Args:
        node_id: Optional node ID (default: current node)

    Returns:
        bool: True if we're below MAX_WORKERS_PER_NODE
    """
    if node_id is None:
        node_id = get_node_id()

    max_workers = get_setting("MAX_WORKERS_PER_NODE", 1)
    current_count = count_active_workers(node_id)

    return current_count < max_workers
