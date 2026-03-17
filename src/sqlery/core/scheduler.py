"""Django-agnostic scheduled task management."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Scheduler:
    """Manages scheduled tasks and enqueues jobs when tasks are due.

    Works in both Django and standalone modes via backend abstraction.
    """

    def __init__(self, backend=None):
        """Initialize scheduler with optional backend.

        Args:
            backend: DatabaseBackend instance (auto-detected if not provided)
        """
        if backend is None:
            from ..compat import get_backend
            backend = get_backend()
        self.backend = backend

    def run_due_tasks(self, queue_names: list[str] | None = None):
        """Find due scheduled tasks and enqueue jobs for them.

        Uses atomic claiming to prevent duplicate enqueueing across multiple
        scheduler instances.

        Args:
            queue_names: If provided, only process tasks in these queues.
                         None means process all queues (backwards-compatible).

        Returns:
            list: Job instances created
        """
        jobs = []
        now = datetime.now(timezone.utc)

        # Get all due tasks - we'll claim them atomically one by one
        due_tasks = self.backend.get_due_scheduled_tasks()

        if queue_names is not None:
            due_tasks = [t for t in due_tasks if t.queue_name in queue_names]

        logger.info(f"Found {len(due_tasks)} due scheduled tasks")

        # Process each task atomically to prevent duplicate enqueueing
        for task in due_tasks:
            try:
                # Atomically claim and enqueue the task
                job = self._enqueue_for_scheduled_task(task)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.error(f"Error processing scheduled task '{task.name}': {e}")
                continue

        return jobs

    def _enqueue_for_scheduled_task(self, task):
        """Enqueue a job for a scheduled task.

        Checks if task already has a queued/running job to avoid duplicates.

        Args:
            task: ScheduledTask instance

        Returns:
            Job instance if created, None if already queued
        """
        # Check if already queued
        has_pending = self.backend.has_pending_job_for_scheduled_task(task.id)

        if has_pending:
            logger.info(
                f"Scheduled task '{task.name}' already has queued/running job, skipping"
            )
            return None

        # Create queued job
        # kwargs={},
        kwargs = task.get_kwargs_dict() if hasattr(task, 'get_kwargs_dict') else {}
        job = self.backend.create_job(
            task_path=task.task_path,
            kwargs=kwargs,
            queue_name=task.queue_name,
            priority=task.priority,
            scheduled_at=None,  # Run immediately
            max_retries=getattr(task, 'max_retries', 0),
            retry_backoff=getattr(task, 'retry_backoff', 1.0),
            allow_parallel=getattr(task, 'allow_parallel', False),
            timeout_seconds=getattr(task, 'timeout_seconds', None),
            scheduled_task_id=task.id,
        )

        # Update next run time based on schedule_type
        # # Old: always used cron — broke interval and once schedule types
        # next_run = self.calculate_next_run(task.cron_expression)
        # self.backend.update_scheduled_task_next_run(task.id, next_run)
        schedule_type = getattr(task, 'schedule_type', 'cron')
        if schedule_type == 'cron' and task.cron_expression:
            next_run = self.calculate_next_run(task.cron_expression)
            self.backend.update_scheduled_task_next_run(task.id, next_run)
        elif schedule_type == 'interval':
            from datetime import timedelta
            interval = getattr(task, 'get_interval_seconds', lambda: 0)()
            if interval > 0:
                next_run = datetime.now(timezone.utc) + timedelta(seconds=interval)
                self.backend.update_scheduled_task_next_run(task.id, next_run)
        elif schedule_type == 'once':
            self.backend.update_scheduled_task(task.id, enabled=False, next_run_at=None)
        else:
            # Fallback: try cron if expression is available
            if task.cron_expression:
                next_run = self.calculate_next_run(task.cron_expression)
                self.backend.update_scheduled_task_next_run(task.id, next_run)

        logger.info(
            f"Enqueued job {job.id} for scheduled task '{task.name}' in queue '{task.queue_name}'"
        )

        return job

    def calculate_next_run(self, cron_expression: str, base_time: datetime | None = None) -> datetime:
        """Calculate next run time from cron expression.

        Args:
            cron_expression: Cron expression (e.g., '0 * * * *')
            base_time: Base time to calculate from (default: now UTC)

        Returns:
            Next run datetime (UTC)
        """
        from ..crontab import next_cron_occurrence

        if base_time is None:
            base_time = datetime.now(timezone.utc)

        # Ensure timezone-aware
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)

        return next_cron_occurrence(cron_expression, base_time)

    def register_scheduled_task(
        self,
        name: str,
        task_path: str,
        cron_expression: str,
        queue_name: str = 'default',
        priority: int = 0,
        enabled: bool = True,
    ):
        """Register a new scheduled task.

        Args:
            name: Human-readable task name
            task_path: Python path to task function
            cron_expression: Cron expression for scheduling
            queue_name: Queue to enqueue jobs in
            priority: Job priority
            enabled: Whether task is enabled

        Returns:
            ScheduledTask instance
        """
        # Calculate initial next_run_at
        next_run = self.calculate_next_run(cron_expression)

        # Create scheduled task
        task = self.backend.create_scheduled_task(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
            next_run_at=next_run,
        )

        logger.info(
            f"Registered scheduled task '{name}' ({cron_expression}) -> {task_path}"
        )

        return task

    def update_scheduled_task(
        self,
        task_id: int,
        name: str | None = None,
        cron_expression: str | None = None,
        enabled: bool | None = None,
        **kwargs
    ):
        """Update an existing scheduled task.

        Args:
            task_id: Task ID to update
            name: New task name
            cron_expression: New cron expression
            enabled: New enabled status
            **kwargs: Additional fields to update

        Returns:
            Updated ScheduledTask instance
        """
        updates = {}

        if name is not None:
            updates['name'] = name

        if cron_expression is not None:
            updates['cron_expression'] = cron_expression
            # Recalculate next_run_at
            updates['next_run_at'] = self.calculate_next_run(cron_expression)

        if enabled is not None:
            updates['enabled'] = enabled

        updates.update(kwargs)

        task = self.backend.update_scheduled_task(task_id, **updates)

        logger.info(f"Updated scheduled task {task_id}")

        return task

    def delete_scheduled_task(self, task_id: int):
        """Delete a scheduled task.

        Args:
            task_id: Task ID to delete

        Returns:
            True if deleted, False if not found
        """
        deleted = self.backend.delete_scheduled_task(task_id)

        if deleted:
            logger.info(f"Deleted scheduled task {task_id}")
        else:
            logger.warning(f"Scheduled task {task_id} not found")

        return deleted

    def get_scheduled_tasks(self, enabled_only: bool = False):
        """Get all scheduled tasks.

        Args:
            enabled_only: Only return enabled tasks

        Returns:
            List of ScheduledTask instances
        """
        return self.backend.get_scheduled_tasks(enabled_only=enabled_only)

    def get_scheduled_task(self, task_id: int):
        """Get a specific scheduled task.

        Args:
            task_id: Task ID

        Returns:
            ScheduledTask instance or None
        """
        return self.backend.get_scheduled_task(task_id)
