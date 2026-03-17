"""Public API for manual job enqueueing."""

from datetime import datetime
from .models import QueuedJob
from .settings import get_setting


def enqueue(task_path, queue=None, priority=None, max_retries=None, retry_backoff=None,
             allow_parallel=None, timeout_seconds=None, tags=None, depends_on=None,
             webhook_url=None, webhook_events=None, **kwargs):
    """Enqueue a job for immediate execution.

    Args:
        task_path (str): Python path to callable (e.g., 'myapp.tasks.send_email')
        queue (str, optional): Queue name. Defaults to settings DEFAULT_QUEUE.
        priority (int, optional): Priority. Higher = sooner. Defaults to settings DEFAULT_PRIORITY.
        max_retries (int, optional): Maximum retry attempts. Defaults to 0 (no retries).
        retry_backoff (float, optional): Exponential backoff multiplier in seconds. Defaults to 1.0.
        allow_parallel (bool, optional): Allow parallel execution in same queue. Defaults to False.
        timeout_seconds (int, optional): Maximum execution time in seconds. Defaults to None (no timeout).
        tags (list[str], optional): Tags for concurrency limiting (e.g., ['acme-api']). Defaults to empty list.
        depends_on (list[int], optional): List of job IDs that must complete successfully before this job runs.
        webhook_url (str, optional): URL to POST webhook notification when job completes.
        webhook_events (list[str], optional): Events that trigger webhook: ['success', 'failure'] or subset.
        **kwargs: Keyword arguments to pass to the task function.

    Returns:
        QueuedJob: The created job instance

    Example:
        >>> from sqlery import enqueue
        >>> job = enqueue(
        ...     'myapp.tasks.send_email',
        ...     queue='email',
        ...     priority=10,
        ...     max_retries=3,
        ...     allow_parallel=True,
        ...     timeout_seconds=300,
        ...     tags=['acme-api', 'rate-limited'],
        ...     to_email='user@example.com',
        ...     subject='Welcome'
        ... )
        >>> job.id
        42
        >>> job.status
        'queued'
        >>> job.tags
        ['acme-api', 'rate-limited']

        Example with dependencies:
        >>> job1 = enqueue('myapp.tasks.extract_data')
        >>> job2 = enqueue('myapp.tasks.transform_data', depends_on=[job1.id])
        >>> job3 = enqueue('myapp.tasks.load_data', depends_on=[job2.id])

        Example with webhooks:
        >>> job = enqueue(
        ...     'myapp.tasks.process_payment',
        ...     webhook_url='https://example.com/hooks/payment-complete',
        ...     webhook_events=['success', 'failure'],
        ...     amount=100
        ... )
    """
    if queue is None:
        queue = get_setting("DEFAULT_QUEUE", "default")

    if priority is None:
        priority = get_setting("DEFAULT_PRIORITY", 0)

    if max_retries is None:
        max_retries = get_setting("DEFAULT_MAX_RETRIES", 0)

    if retry_backoff is None:
        retry_backoff = get_setting("DEFAULT_RETRY_BACKOFF", 1.0)

    if allow_parallel is None:
        allow_parallel = False

    if tags is None:
        tags = []

    if depends_on is None:
        depends_on = []

    if webhook_events is None:
        webhook_events = ['success', 'failure']  # Default: notify on both success and failure

    job = QueuedJob.objects.create(
        task_path=task_path,
        kwargs=kwargs,
        queue_name=queue,
        priority=priority,
        scheduled_at=None,  # Run immediately
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout_seconds,
        tags=tags,
        dependencies=depends_on,
        webhook_url=webhook_url,
        webhook_events=webhook_events,
    )

    # Trigger worker if configured
    _trigger_worker_if_needed()

    return job


def enqueue_at(task_path, run_at, queue=None, priority=None, max_retries=None, retry_backoff=None,
               allow_parallel=None, timeout_seconds=None, tags=None, depends_on=None,
               webhook_url=None, webhook_events=None, **kwargs):
    """Enqueue a job to run at a specific datetime.

    Args:
        task_path (str): Python path to callable (e.g., 'myapp.tasks.send_email')
        run_at (datetime): When to run the job (timezone-aware recommended)
        queue (str, optional): Queue name. Defaults to settings DEFAULT_QUEUE.
        priority (int, optional): Priority. Higher = sooner. Defaults to settings DEFAULT_PRIORITY.
        max_retries (int, optional): Maximum retry attempts. Defaults to 0 (no retries).
        retry_backoff (float, optional): Exponential backoff multiplier in seconds. Defaults to 1.0.
        allow_parallel (bool, optional): Allow parallel execution in same queue. Defaults to False.
        timeout_seconds (int, optional): Maximum execution time in seconds. Defaults to None (no timeout).
        tags (list[str], optional): Tags for concurrency limiting (e.g., ['acme-api']). Defaults to empty list.
        depends_on (list[int], optional): List of job IDs that must complete successfully before this job runs.
        webhook_url (str, optional): URL to POST webhook notification when job completes.
        webhook_events (list[str], optional): Events that trigger webhook: ['success', 'failure'] or subset.
        **kwargs: Keyword arguments to pass to the task function.

    Returns:
        QueuedJob: The created job instance

    Example:
        >>> from sqlery import enqueue_at
        >>> from datetime import datetime, timezone, timedelta
        >>> run_time = datetime.now(timezone.utc) + timedelta(hours=1)
        >>> job = enqueue_at(
        ...     'myapp.tasks.send_email',
        ...     run_time,
        ...     queue='email',
        ...     max_retries=3,
        ...     allow_parallel=True,
        ...     timeout_seconds=300,
        ...     tags=['scheduled-sync'],
        ...     to_email='user@example.com'
        ... )
        >>> job.scheduled_at
        datetime.datetime(...)
    """
    if queue is None:
        queue = get_setting("DEFAULT_QUEUE", "default")

    if priority is None:
        priority = get_setting("DEFAULT_PRIORITY", 0)

    if max_retries is None:
        max_retries = get_setting("DEFAULT_MAX_RETRIES", 0)

    if retry_backoff is None:
        retry_backoff = get_setting("DEFAULT_RETRY_BACKOFF", 1.0)

    if allow_parallel is None:
        allow_parallel = False

    if tags is None:
        tags = []

    if depends_on is None:
        depends_on = []

    if webhook_events is None:
        webhook_events = ['success', 'failure']  # Default: notify on both success and failure

    # Ensure timezone-aware
    if run_at.tzinfo is None:
        from django.utils import timezone
        run_at = run_at.replace(tzinfo=timezone.utc)

    job = QueuedJob.objects.create(
        task_path=task_path,
        kwargs=kwargs,
        queue_name=queue,
        priority=priority,
        scheduled_at=run_at,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        allow_parallel=allow_parallel,
        timeout_seconds=timeout_seconds,
        tags=tags,
        dependencies=depends_on,
        webhook_url=webhook_url,
        webhook_events=webhook_events,
    )

    # EventBridge mode: Schedule delayed event
    trigger_mode = get_setting("TRIGGER_MODE", "middleware")
    if trigger_mode == "eventbridge":
        _schedule_eventbridge_delayed_job(job.id, run_at)

    # Note: Worker will skip this until scheduled_at is reached
    return job


def _trigger_worker_if_needed():
    """Trigger worker to process queue (if enabled)."""
    from .settings import get_setting

    trigger_mode = get_setting("TRIGGER_MODE", "middleware")

    # EventBridge mode: Directly invoke Lambda worker
    if trigger_mode == "eventbridge":
        _trigger_eventbridge_worker()
        return

    # Traditional deployment with middleware, workers are already running
    # In other serverless modes, this is a no-op (external scheduler triggers workers)
    auto_trigger = get_setting("AUTO_TRIGGER_WORKER", False)
    if not auto_trigger:
        return

    # Placeholder for future implementation
    # Could use django-tasks, etc.
    pass


def _trigger_eventbridge_worker():
    """Invoke Lambda worker via EventBridge for immediate job processing."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        from .eventbridge_trigger import invoke_lambda_worker

        result = invoke_lambda_worker()
        logger.info(f"Triggered Lambda worker: {result}")

    except Exception as e:
        logger.error(f"Failed to trigger Lambda worker: {e}")


def _schedule_eventbridge_delayed_job(job_id, run_at):
    """Schedule a delayed job execution via EventBridge."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        from .eventbridge_trigger import schedule_eventbridge_event

        result = schedule_eventbridge_event(job_id, run_at)
        logger.info(f"Scheduled EventBridge delayed job {job_id}: {result}")

    except Exception as e:
        logger.error(f"Failed to schedule EventBridge delayed job {job_id}: {e}")
