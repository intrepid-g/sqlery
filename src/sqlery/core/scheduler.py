"""Django-agnostic scheduled task management."""

import logging
import random
import time
from datetime import datetime, timedelta, timezone

from ..compat import get_backend, get_config, is_django_mode
from ..crontab import next_cron_occurrence

logger = logging.getLogger(__name__)

# Upper bound on the future-clamp loop in calculate_next_run. Guards against a
# misbehaving cron expression spinning forever; 2,000,000 covers ~3.8 years of
# every-minute ticks of downtime before the cap is reached.
_MAX_CLAMP_ITERATIONS = 2_000_000


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
            # from ..compat import get_backend  # moved to top-level
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

        For cron tasks, the next_run_at advance and the enqueue are a single
        atomic compare-and-swap (backend.advance_scheduled_task_if_due). The CAS
        winner is the only caller that enqueues, so double-fire is impossible even
        under brief two-leader overlap (CRON-01, CRON-04). The next occurrence is
        computed from the scheduled time (task.next_run_at), correcting drift
        (CRON-02), and an optional bounded jitter delay is applied before enqueue
        (CRON-03). interval/once branches keep their prior check-then-act behavior.

        Args:
            task: ScheduledTask instance

        Returns:
            Job instance if created, None if already fired / lost CAS
        """
        # Capture the scheduled due time BEFORE any advance — this is the CAS token.
        observed_due = task.next_run_at

        # Build the same job_kwargs the old create_job call used.
        kwargs = task.get_kwargs_dict() if hasattr(task, "get_kwargs_dict") else {}
        job_kwargs = {
            "task_path": task.task_path,
            "kwargs": kwargs,
            "queue_name": task.queue_name,
            "priority": task.priority,
            "scheduled_at": None,  # Run immediately
            "max_retries": getattr(task, "max_retries", 0),
            "retry_backoff": getattr(task, "retry_backoff", 1.0),
            "allow_parallel": getattr(task, "allow_parallel", False),
            "timeout_seconds": getattr(task, "timeout_seconds", None),
            "scheduled_task_id": task.id,
        }

        schedule_type = getattr(task, "schedule_type", "cron")
        is_cron = (schedule_type == "cron" and task.cron_expression) or (
            schedule_type not in ("cron", "interval", "once") and task.cron_expression
        )

        if is_cron:
            # # Old (check-then-act — replaced by the atomic CAS below; race-prone
            # # under two-leader overlap, see Phase 9 WR-01/WR-02):
            # has_pending = self.backend.has_pending_job_for_scheduled_task(task.id)
            # if has_pending:
            #     logger.info(
            #         f"Scheduled task '{task.name}' already has queued/running job, skipping"
            #     )
            #     return None
            # job = self.backend.create_job(**job_kwargs)
            # next_run = self.calculate_next_run(task.cron_expression)
            # self.backend.update_scheduled_task_next_run(task.id, next_run)

            # Drift-corrected next occurrence from the scheduled time (CRON-02).
            new_next_run = self.calculate_next_run(task.cron_expression, base_time=task.next_run_at)

            # Optional bounded jitter (CRON-03): config-only, never request-derived,
            # never fed into next_run_at. Applied before the atomic advance so a
            # crash during the sleep simply re-evaluates the tick next cycle.
            jitter = self._get_jitter_seconds()
            if jitter and jitter > 0:
                time.sleep(random.uniform(0, jitter))

            # Atomic advance+enqueue: only the CAS winner gets a job back (CRON-01/04).
            job = self.backend.advance_scheduled_task_if_due(
                task.id, observed_due, new_next_run, job_kwargs
            )
            if job is None:
                logger.info(
                    f"Scheduled task '{task.name}' already fired / lost advance CAS, skipping"
                )
                return None

            logger.info(
                f"Enqueued job {job.id} for scheduled task '{task.name}' "
                f"in queue '{task.queue_name}'"
            )
            return job

        # Non-cron paths keep their prior check-then-act behavior (no atomic
        # advance primitive this phase). Preserve the pending-job dedup gate.
        has_pending = self.backend.has_pending_job_for_scheduled_task(task.id)
        if has_pending:
            logger.info(
                f"Scheduled task '{task.name}' already has queued/running job, skipping"
            )
            return None

        job = self.backend.create_job(**job_kwargs)

        if schedule_type == "interval":
            # from datetime import timedelta  # moved to top-level
            interval = getattr(task, "get_interval_seconds", lambda: 0)()
            if interval > 0:
                next_run = datetime.now(timezone.utc) + timedelta(seconds=interval)
                self.backend.update_scheduled_task_next_run(task.id, next_run)
        elif schedule_type == "once":
            self.backend.update_scheduled_task(task.id, enabled=False, next_run_at=None)

        logger.info(
            f"Enqueued job {job.id} for scheduled task '{task.name}' in queue '{task.queue_name}'"
        )

        return job

    def _get_jitter_seconds(self) -> float:
        """Resolve the scheduler jitter delay (seconds) for the active mode.

        Uses a mode-aware key so operator overrides take effect in both modes
        (Django stores SCHEDULER_JITTER_SECONDS upper-snake; standalone stores
        scheduler_jitter_seconds lowercase). Defaults to 0 (jitter off).
        """
        jitter_key = "SCHEDULER_JITTER_SECONDS" if is_django_mode() else "scheduler_jitter_seconds"
        value = get_config(jitter_key, 0)
        try:
            return float(value) if value else 0.0
        except (TypeError, ValueError):
            return 0.0

    def calculate_next_run(self, cron_expression: str, base_time: datetime | None = None) -> datetime:
        """Calculate next run time from cron expression.

        Args:
            cron_expression: Cron expression (e.g., '0 * * * *')
            base_time: Base time to calculate from (default: now UTC)

        Returns:
            Next run datetime (UTC)
        """
        # from ..crontab import next_cron_occurrence  # moved to top-level

        if base_time is None:
            base_time = datetime.now(timezone.utc)

        # Ensure timezone-aware
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)

        # # Old: returned the first occurrence after base_time, even when base_time
        # # was far in the past — replaying every missed tick one-by-one (CRON-02).
        # return next_cron_occurrence(cron_expression, base_time)
        candidate = next_cron_occurrence(cron_expression, base_time)
        # Future-clamp: when base_time is stale (long downtime), advance to the
        # next FUTURE occurrence instead of replaying missed ticks. Bounded loop
        # so a misbehaving cron cannot spin forever.
        now = datetime.now(timezone.utc)
        iterations = 0
        while candidate <= now and iterations < _MAX_CLAMP_ITERATIONS:
            candidate = next_cron_occurrence(cron_expression, candidate)
            iterations += 1
        if iterations >= _MAX_CLAMP_ITERATIONS and candidate <= now:
            logger.warning(
                f"calculate_next_run hit clamp cap ({_MAX_CLAMP_ITERATIONS}) for "
                f"'{cron_expression}'; returning last candidate {candidate}"
            )
        return candidate

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
