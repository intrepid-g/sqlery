"""Django-agnostic scheduled task management."""

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from ..compat import get_backend, get_config, is_django_mode
from ..crontab import next_cron_occurrence

try:
    import psycopg
    from psycopg import sql as pgsql
    _PSYCOPG_AVAILABLE = True
except ImportError:
    psycopg = None
    pgsql = None
    _PSYCOPG_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Advisory-lock constant for the staging promotion DDL lock (D9, Phase 14).
# Derived from the 8-char ASCII tag "SQLEPROM" — fits in signed PostgreSQL int8.
# ---------------------------------------------------------------------------
ADVISORY_LOCK_PROMOTE: int = int.from_bytes(b"SQLEPROM", "big")  # staging promotion DDL lock

# How far ahead of scheduled_at to promote rows: 30 seconds covers two
# default daemon ticks so jobs are never delayed by more than one tick.
_PROMOTION_LOOKAHEAD_SECONDS: int = 30

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

            # IN-02: distinguish staged vs directly-queued jobs in log (no ORM import needed).
            _verb = "Enqueued" if hasattr(job, "status") else "Staged"
            logger.info(
                f"{_verb} job {job.id} for scheduled task '{task.name}' "
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

        # IN-02: "Staged" when create_job routed to sqlery_scheduled_job (no status attr).
        # Old: f"Enqueued job {job.id} ..."  <-- misleading when job is actually staged
        _verb = "Enqueued" if hasattr(job, "status") else "Staged"
        logger.info(
            f"{_verb} job {job.id} for scheduled task '{task.name}' in queue '{task.queue_name}'"
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
            # # Old (CR-01): returned the past candidate, which was persisted into
            # # next_run_at and made the task re-qualify as due every cycle — a
            # # runaway enqueue-every-cycle producer. The CAS dedup does not help
            # # since each cycle the row still equals the observed value.
            # logger.warning(
            #     f"calculate_next_run hit clamp cap ({_MAX_CLAMP_ITERATIONS}) for "
            #     f"'{cron_expression}'; returning last candidate {candidate}"
            # )
            logger.warning(
                f"calculate_next_run hit clamp cap ({_MAX_CLAMP_ITERATIONS}) for "
                f"'{cron_expression}'; recomputing from now to avoid re-fire loop"
            )
            # Recompute strictly from the current time so the persisted next_run_at
            # is in the future and the task does not immediately re-qualify as due.
            candidate = next_cron_occurrence(cron_expression, now)
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


# ---------------------------------------------------------------------------
# Module-level staging promotion function (Phase 14, D9)
# ---------------------------------------------------------------------------


def promote_due_scheduled_jobs(cur) -> int:
    """Atomically move due rows from sqlery_scheduled_job into sqlery_queued_job.

    Single transaction: CTE locks due rows with SELECT FOR UPDATE SKIP LOCKED,
    then DELETE WHERE id IN (locked ids) RETURNING *, then INSERT INTO
    sqlery_queued_job for each row. Wrapped in pg_try_advisory_lock
    (ADVISORY_LOCK_PROMOTE) so concurrent daemons skip rather than race (D9).

    The payload column in sqlery_scheduled_job carries the full job-creation
    spec as {"kwargs": {...}, "job_spec": {...all execution params...}} so the
    promoted row is field-for-field identical to a directly-queued job.

    Payload schema stored in sqlery_scheduled_job.payload:
        {
          "kwargs": dict,          # task function kwargs
          "job_spec": {
            "retry_backoff": float,
            "allow_parallel": bool,
            "timeout_seconds": int | None,
            "retry_count": int,
            "scheduled_task_id": int | None,
            "job_name": str | None,
            "retry_intervals": list | None,
            "meta": dict | None,
            "dependencies": list,
            "on_success_path": str,
            "on_failure_path": str,
            "ttl": int | None,
            "result_ttl": int | None,
            "failure_ttl": int | None,
            "parent_job_id": int | None,
          }
        }

    Args:
        cur: Raw psycopg cursor (must NOT be in autocommit mode — this function
             issues BEGIN/COMMIT/ROLLBACK explicitly to ensure the DELETE and
             all INSERTs are atomic). No Django or SQLAlchemy imports used here.

    Returns:
        Number of rows promoted.
    """
    if not _PSYCOPG_AVAILABLE:
        logger.warning(
            "promote_due_scheduled_jobs: psycopg not available — staging promotion skipped"
        )
        return 0

    # Step 1: Acquire advisory lock; skip tick if another daemon is running this.
    cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_PROMOTE])
    (lock_acquired,) = cur.fetchone()
    if not lock_acquired:
        return 0

    try:
        # Step 2: Explicit transaction so DELETE and all INSERTs are atomic.
        # If any INSERT fails the ROLLBACK returns all rows to sqlery_scheduled_job.
        cur.execute("BEGIN")
        try:
            # Step 2a: CTE locks due rows with SKIP LOCKED (valid PG syntax) then
            # DELETEs the locked ids — avoids the invalid DELETE ... FOR UPDATE form.
            # Old: "DELETE FROM sqlery_scheduled_job"
            #      " WHERE scheduled_at <= now() + make_interval(secs => %s)"
            #      " FOR UPDATE SKIP LOCKED"   <-- invalid PostgreSQL syntax
            #      " RETURNING id, queue_name, task_path, payload,"
            #      "           scheduled_at, priority, max_retries, created_at"
            cur.execute(
                "WITH locked AS ("
                "  SELECT id FROM sqlery_scheduled_job"
                "  WHERE scheduled_at <= now() + make_interval(secs => %s)"
                "  FOR UPDATE SKIP LOCKED"
                ")"
                " DELETE FROM sqlery_scheduled_job"
                " WHERE id IN (SELECT id FROM locked)"
                " RETURNING id, queue_name, task_path, payload,"
                "           scheduled_at, priority, max_retries, created_at",
                [_PROMOTION_LOOKAHEAD_SECONDS],
            )
            rows = cur.fetchall()

            # Step 2b: Nothing to promote this tick.
            if not rows:
                cur.execute("COMMIT")
                return 0

            # Step 2c: INSERT each promoted row into sqlery_queued_job with full
            # field fidelity from payload["job_spec"] (WR-01/WR-02). All non-nullable
            # JSON columns are supplied explicitly rather than relying on DB defaults.
            # payload column in staging stores {"kwargs": {...}, "job_spec": {...}}.
            for row in rows:
                (job_id, queue_name, task_path, raw_payload,
                 scheduled_at, priority, max_retries, created_at) = row

                # Deserialise payload if the cursor returns bytes/str instead of dict.
                if isinstance(raw_payload, (bytes, str)):
                    payload_dict = json.loads(raw_payload)
                else:
                    payload_dict = raw_payload or {}

                # Extract kwargs and job_spec with safe fallbacks for legacy rows
                # that were staged before the full-fidelity payload format was introduced.
                if isinstance(payload_dict, dict) and "kwargs" in payload_dict:
                    kwargs = payload_dict.get("kwargs", {})
                    spec = payload_dict.get("job_spec", {})
                else:
                    # Legacy staging row — payload IS the kwargs dict.
                    kwargs = payload_dict
                    spec = {}

                retry_backoff = spec.get("retry_backoff", 1.0)
                allow_parallel = spec.get("allow_parallel", False)
                timeout_seconds = spec.get("timeout_seconds", None)
                retry_count = spec.get("retry_count", 0)
                scheduled_task_id = spec.get("scheduled_task_id", None)
                job_name = spec.get("job_name", None)
                retry_intervals = spec.get("retry_intervals", None)
                meta = spec.get("meta", None)
                dependencies = spec.get("dependencies", [])
                on_success_path = spec.get("on_success_path", "")
                on_failure_path = spec.get("on_failure_path", "")
                ttl = spec.get("ttl", None)
                result_ttl = spec.get("result_ttl", None)
                failure_ttl = spec.get("failure_ttl", None)
                parent_job_id = spec.get("parent_job_id", None)

                cur.execute(
                    "INSERT INTO sqlery_queued_job"
                    " (id, queue_name, task_path, kwargs, scheduled_at,"
                    "  priority, max_retries, status, version, retry_count,"
                    "  retry_backoff, allow_parallel, timeout_seconds,"
                    "  scheduled_task_id, job_name, retry_intervals, meta,"
                    "  dependencies, on_success_path, on_failure_path,"
                    "  ttl, result_ttl, failure_ttl, parent_job_id,"
                    "  runs, tags, webhook_events, output, error, traceback,"
                    "  termination_reason, created_at)"
                    " VALUES (%s, %s, %s, %s::jsonb, %s,"
                    "  %s, %s, 'queued', 0, %s,"
                    "  %s, %s, %s,"
                    "  %s, %s, %s::jsonb, %s::jsonb,"
                    "  %s::jsonb, %s, %s,"
                    "  %s, %s, %s, %s,"
                    "  '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '', '', '',"
                    "  '', %s)",
                    [
                        job_id, queue_name, task_path,
                        json.dumps(kwargs), scheduled_at,
                        priority, max_retries, retry_count,
                        retry_backoff, allow_parallel, timeout_seconds,
                        scheduled_task_id, job_name,
                        json.dumps(retry_intervals) if retry_intervals is not None else None,
                        json.dumps(meta) if meta is not None else None,
                        json.dumps(dependencies),
                        on_success_path, on_failure_path,
                        ttl, result_ttl, failure_ttl, parent_job_id,
                        created_at,
                    ],
                )

            cur.execute("COMMIT")
            return len(rows)

        except Exception:
            cur.execute("ROLLBACK")
            raise

    finally:
        # Step 3: Always release the advisory lock even if INSERT raised.
        cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_PROMOTE])
