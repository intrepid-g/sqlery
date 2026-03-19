"""Framework-agnostic scheduled task processing.

Promoted from django_sqlery/executor.py. The scheduled task -> job conversion
logic is pure algorithm parameterized by backend calls.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlery.core.utils import calculate_next_run

logger = logging.getLogger(__name__)


class _RQCompatJob:
    """Thin wrapper that makes a job look like an RQ Job for callbacks.

    RQ callbacks receive a job where job.id is the string job_id passed at
    enqueue time. In sqlery, that string lives in job.job_name while job.id
    is an auto-incrementing integer PK. This wrapper proxies all attribute
    access to the real job but overrides .id to return job_name (falling
    back to str(pk) when no job_name was set).
    """

    def __init__(self, job):
        object.__setattr__(self, '_job', job)

    @property
    def id(self):
        job = object.__getattribute__(self, '_job')
        return job.job_name if job.job_name else str(job.pk)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_job'), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_job'), name, value)


def run_due_tasks(backend) -> list:
    """Find due scheduled tasks and enqueue jobs for them.

    Uses backend.claim_due_scheduled_task() for atomic claiming,
    preventing duplicate enqueueing across multiple scheduler instances.

    Args:
        backend: DatabaseBackend instance

    Returns:
        List of created job instances
    """
    due_tasks = backend.get_due_scheduled_tasks()
    logger.info(f"Found {len(due_tasks)} due scheduled tasks")

    jobs = []
    for task in due_tasks:
        # Atomically claim the task
        claimed_task = backend.claim_due_scheduled_task(task.id)
        if claimed_task is None:
            continue

        job = enqueue_for_scheduled_task(claimed_task, backend)
        if job:
            jobs.append(job)

    return jobs


def enqueue_for_scheduled_task(task, backend):
    """Enqueue a job for a scheduled task.

    Handles repeat limits, dedup (skip if already queued/running),
    and next-run calculation.

    Args:
        task: ScheduledTask instance
        backend: DatabaseBackend instance

    Returns:
        Job instance if created, None if skipped
    """
    # Check repeat limit for interval tasks
    schedule_type = getattr(task, 'schedule_type', 'cron')
    repeat = getattr(task, 'repeat', None)

    if schedule_type == "interval" and repeat is not None:
        # Count total jobs enqueued for this task
        if backend.has_pending_job_for_scheduled_task(task.id):
            # Rough check — detailed repeat counting needs task-level query
            pass

    # Skip if already has queued/running job
    if backend.has_pending_job_for_scheduled_task(task.id):
        logger.info(
            f"Scheduled task '{task.name}' already has queued/running job, skipping"
        )
        return None

    # Get task kwargs
    task_kwargs = {}
    if hasattr(task, 'get_kwargs_dict'):
        task_kwargs = task.get_kwargs_dict()

    # Create job via backend
    job = backend.create_job(
        task_path=task.task_path,
        kwargs=task_kwargs,
        queue_name=task.queue_name,
        priority=task.priority,
        scheduled_at=None,  # Run immediately
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
        scheduled_task_id=task.id,
    )

    # Update next run time based on schedule type
    if schedule_type == "cron":
        # from datetime import datetime, timezone  # moved to top-level
        next_run = calculate_next_run(
            task.cron_expression,
            base_time=datetime.now(timezone.utc),
        )
        backend.update_scheduled_task_next_run(task.id, next_run)
    elif schedule_type == "interval":
        # from datetime import datetime, timezone  # moved to top-level
        interval_seconds = task.get_interval_seconds() if hasattr(task, 'get_interval_seconds') else 60
        next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
        backend.update_scheduled_task_next_run(task.id, next_run)
    elif schedule_type == "once":
        backend.update_scheduled_task(task.id, enabled=False, next_run_at=None)

    logger.info(
        f"Enqueued job for scheduled task '{task.name}' in queue '{task.queue_name}'"
    )
    return job
