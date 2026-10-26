"""Django ORM backend implementation for sqlery.

This backend wraps Django ORM operations to implement the DatabaseBackend interface.
"""

import logging
import os
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from django.db import connection, IntegrityError, models as db_models, transaction
from django.db.models import Q, Count
from django.utils import timezone

from sqlery.core.db_resilience import retry_on_db_error

from sqlery.core import history as history_core
from sqlery.core.attempts import (
    OUTCOME_ABANDONED,
    OUTCOME_SUCCESS,
    AttemptInfo,
)

from ..compat import DatabaseBackend, JobFencingError
from .db_compat import (
    assert_in_atomic_block,
    atomic_claim_job_queryset,
    is_sqlite,
)
# Old: from .models import DaemonLease, QueuedJob, ScheduledTask, JobRegistry, TagLock, Worker
from . import history, ready
from .models import (
    ConcurrentModificationError,
    DaemonLease,
    JobAll,
    JobAttempt,
    JobBlocked,
    JobHistory,
    JobReady,
    QueuedJob,
    ScheduledTask,
    JobRegistry,
    TagLock,
    Worker,
    ScheduledJob,
)
from .settings import get_setting
from sqlery.core.claiming import claim_next_job_with_queue_priority
# Partition maintenance helpers — guarded against psycopg absence (standalone/SQLite installs)
try:
    from sqlery.core import partitioning as _partitioning
except ImportError:
    _partitioning = None  # psycopg not installed; partition reclaim path unavailable

# Phase 18: pg_notify hook — guarded so Django backend works without core.pg_notify
try:
    from sqlery.core.pg_notify import notify_queue_django as _notify_queue_django
except ImportError:
    _notify_queue_django = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CLEANUP_BATCH_SIZE = 500
FINISHED_STATUSES = ("success", "failed", "archived")


# Claimer ids minted internally by the Lambda and HTTP-trigger paths (no Worker row).
_EPHEMERAL_CLAIMER_PREFIXES = ("lambda-", "trigger-")


class DjangoBackend(DatabaseBackend):
    """Django ORM implementation of DatabaseBackend.

    Uses Django models and QuerySets for all database operations.
    """

    def __init__(self):
        """Initialize Django backend."""
        # Import models here to avoid circular imports
        # from .models import QueuedJob, ScheduledTask, JobRegistry, Worker  # moved to top-level

        self.QueuedJob = QueuedJob
        self.ScheduledTask = ScheduledTask
        self.JobRegistry = JobRegistry
        self.Worker = Worker
        self.ScheduledJob = ScheduledJob
        self._partitioned_pg_cache: bool | None = None

    def _partitioned_pg(self) -> bool:
        """True iff running on PostgreSQL AND sqlery_queued_job is partitioned.

        Used by the cleanup→reclaim routing (Phase 13 seam), the far-future
        staging gate (Phase 14), and the write-path pruning logic. SQLite and a
        non-partitioned PG install both return False — they keep the Phase-12
        batched DELETE path and the in-queue scheduling path unchanged (D6).
        Cached per-process: the table's partition status does not change at
        runtime (only via the stop-the-world cutover migration).
        """
        if self._partitioned_pg_cache is not None:
            return self._partitioned_pg_cache
        if connection.vendor != "postgresql":
            self._partitioned_pg_cache = False
            return False
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT relkind = 'p' FROM pg_class "
                    "WHERE relname = %s AND relnamespace = 'public'::regnamespace",
                    [self.QueuedJob._meta.db_table],
                )
                row = cur.fetchone()
            self._partitioned_pg_cache = bool(row and row[0])
        except Exception:
            # Old: self._partitioned_pg_cache = False
            # WR-01: do NOT permanently cache False on a transient DB error — a
            # connection-pool warmup failure at startup would disable all partition
            # routing for the lifetime of the process with no retry. Leave the cache
            # unset (None) so the next call retries; only write the cache on a
            # successful catalog query above. Return False for this call to fail safe
            # (never route to PG-only paths against a potentially non-partitioned table).
            logger.warning(
                "_partitioned_pg: catalog query failed — will retry on next call",
                exc_info=True,
            )
            return False
        return self._partitioned_pg_cache

    def get_raw_cursor(self):
        """Return a raw DB-API cursor for the daemon's PG-only maintenance loop.

        promote_due_scheduled_jobs / reclaim_drained_partitions / ensure_future_
        partitions need a live psycopg cursor (not the Django ORM). Returns the
        Django connection's cursor on PostgreSQL; returns None on SQLite (and any
        non-partitioned install) so the daemon skips PG-only maintenance cleanly.
        The caller owns the cursor lifecycle (commit/rollback handled inside the
        maintenance functions).
        """
        if not self._partitioned_pg():
            return None
        return connection.cursor()

    @contextmanager
    def raw_cursor(self):
        """Context manager yielding a raw psycopg cursor without closing Django's connection.

        BUG FIX: the daemon's generic ``_nullable_cursor_cm`` fallback (used when a
        backend has no ``raw_cursor()``) closes ``cur.connection`` on exit — correct
        for ``SQLAlchemyBackend.get_raw_cursor()`` (a freshly checked-out pooled
        connection per call) but WRONG here: ``DjangoBackend.get_raw_cursor()``
        returns a cursor on Django's persistent thread-local ``connection`` object.
        Closing the underlying DBAPI connection directly desyncs it from Django's
        own bookkeeping (``connection.connection`` still looks "open"), so every
        subsequent ORM query on that thread/process raises
        ``OperationalError: the connection is closed`` — this was cascading through
        the entire postgres test session after any daemon partition-maintenance
        tick ran. Only the cursor is closed here; Django owns the connection
        lifecycle itself (``close_old_connections`` / request-boundary teardown).
        """
        if not self._partitioned_pg():
            yield None
            return
        cur = connection.cursor()
        try:
            yield cur
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def create_job(
        self,
        task_path: str,
        kwargs: dict,
        queue_name: str,
        priority: int,
        scheduled_at: datetime | None,
        max_retries: int,
        retry_backoff: float,
        allow_parallel: bool,
        timeout_seconds: int | None,
        retry_count: int | None = None,
        scheduled_task_id: int | None = None,
        job_name: str | None = None,
        retry_intervals: list | None = None,
        meta: dict | None = None,
        dependencies: list | None = None,
        on_success_path: str = "",
        on_failure_path: str = "",
        ttl: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        parent_job_id: int | None = None,
        tags: list | None = None,
        webhook_url: str | None = None,
        webhook_events: list | None = None,
        webhook_max_retries: int | None = None,
    ):
        """Create a new job in the database."""
        # Webhook settings are passed on only when given, so unset ones keep the model defaults.
        webhook = {
            key: value
            for key, value in (
                ("webhook_url", webhook_url),
                ("webhook_events", webhook_events),
                ("webhook_max_retries", webhook_max_retries),
            )
            if value is not None
        }
        # If a named job is requested, the new job always wins.  Stop any running
        # conflict (SIGTERM + mark failed) before deleting all records with that name.
        if job_name:
            replaced = []
            for conflicting in self.QueuedJob.objects.filter(job_name=job_name):
                replaced.append((conflicting.id, conflicting.parent_job_id))
                if conflicting.status == "running":
                    conflicting.force_stop()
            with transaction.atomic():
                self.QueuedJob.objects.filter(job_name=job_name).delete()
                ready.remove([job_id for job_id, _ in replaced])
            # Jobs that waited on a replaced job learn that it is gone (they fail, as before).
            for job_id, parent_id in replaced:
                ready.release_dependents_quietly(job_id, parent_id)

        # Threshold routing (D1, Phase 14): jobs scheduled further out than the
        # configured threshold go into sqlery_scheduled_job instead of
        # sqlery_queued_job so they cannot pin otherwise-drained partitions.
        threshold_days = get_setting("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS", 1)
        staging_threshold = timedelta(days=threshold_days)
        now_utc = timezone.now()
        # Staging only protects partitions, which exist only on partitioned PG.
        # On SQLite / non-partitioned PG, far-future jobs stay in sqlery_queued_job
        # (D6 — SQLite path unchanged) since the PG-only promotion loop can't drain
        # a staging table there. (Phase 16 carry-forward: gate routing on partitioning.)
        # Old: if scheduled_at is not None and scheduled_at > now_utc + staging_threshold:
        if (
            self._partitioned_pg()
            and scheduled_at is not None
            and scheduled_at > now_utc + staging_threshold
        ):
            # Store full job-creation spec in payload for lossless promotion (WR-01/WR-02).
            # payload schema: {"kwargs": <task kwargs>, "job_spec": {<all execution params>}}
            # Promotion reads job_spec to reconstruct every queued_job column identically.
            # Old: payload=kwargs   <-- silently dropped 12 fields (retry_backoff, timeout, etc.)
            full_payload = {
                "kwargs": kwargs,
                "job_spec": {
                    "retry_backoff": retry_backoff,
                    "allow_parallel": allow_parallel,
                    "timeout_seconds": timeout_seconds,
                    "retry_count": retry_count if retry_count is not None else 0,
                    "scheduled_task_id": scheduled_task_id,
                    "job_name": job_name,
                    "retry_intervals": retry_intervals,
                    "meta": meta,
                    "dependencies": dependencies or [],
                    "on_success_path": on_success_path,
                    "on_failure_path": on_failure_path,
                    "ttl": ttl,
                    "result_ttl": result_ttl,
                    "failure_ttl": failure_ttl,
                    "parent_job_id": parent_job_id,
                    "tags": list(tags or []),
                    **webhook,
                },
            }
            return self.ScheduledJob.objects.create(
                queue_name=queue_name,
                task_path=task_path,
                payload=full_payload,
                scheduled_at=scheduled_at,
                priority=priority,
                max_retries=max_retries,
            )

        # Below threshold or immediate — insert into main queue.
        job = self.QueuedJob.objects.create(
            task_path=task_path,
            kwargs=kwargs,
            queue_name=queue_name,
            priority=priority,
            scheduled_at=scheduled_at,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            allow_parallel=allow_parallel,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count if retry_count is not None else 0,
            scheduled_task_id=scheduled_task_id,
            job_name=job_name,
            retry_intervals=retry_intervals,
            meta=meta,
            dependencies=dependencies or [],
            on_success_path=on_success_path,
            on_failure_path=on_failure_path,
            ttl=ttl,
            result_ttl=result_ttl,
            failure_ttl=failure_ttl,
            parent_job_id=parent_job_id,
            tags=list(tags or []),
            status="queued",
            **webhook,
        )
        # Phase 18 (D1): emit pg_notify after commit when flag is on + PG.
        # No-op when SQLERY_PG_NOTIFY=False (default) or on SQLite.
        # notify_queue_django handles the vendor check and on_commit scheduling
        # internally; any NOTIFY failure is caught + logged, never crashes enqueue.
        # Old: return job
        if (
            _notify_queue_django is not None
            and get_setting("SQLERY_PG_NOTIFY", False)
            and connection.vendor == "postgresql"
        ):
            _notify_queue_django(queue_name)
        return job

    # No @retry_on_db_error: called inside the worker's idle transaction, where a retry
    # cannot recover an aborted statement; the caller is best-effort already.
    def next_due_delay(self, queues: list[str]) -> float | None:
        now = timezone.now()
        # The earliest not-yet-due entry of the ready table (no scan of the job table).
        nxt = JobReady.objects.filter(queue_name__in=queues, scheduled_at__gt=now).aggregate(
            m=db_models.Min("scheduled_at")
        )["m"]
        return None if nxt is None else (nxt - now).total_seconds()

    def next_scheduled_run_at(self, queue_names: list[str]) -> datetime | None:
        return self.ScheduledTask.objects.filter(
            enabled=True, next_run_at__isnull=False, queue_name__in=queue_names
        ).aggregate(m=db_models.Min("next_run_at"))["m"]

    def idle_transaction(self):
        return transaction.atomic()

    def claim_job(self, queues: list[str], worker_id: str):
        """Atomically claim next available job using the unified claiming path.

        Delegates to claim_next_job_with_queue_priority which enforces tag
        concurrency, rate limits, dependency checks, TTL expiry, and
        optimistic locking.
        """
        # # Old claim_job body — bypassed tag concurrency, rate limits,
        # # dependency checks, TTL expiry, and optimistic locking.
        # from django.db import connection
        #
        # with transaction.atomic():
        #     query = atomic_claim_job_queryset(
        #         self.QueuedJob.objects.filter(
        #             queue_name__in=queues,
        #             status="queued",
        #         ).filter(
        #             Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())
        #         )
        #     ).order_by('-priority', 'created_at')
        #
        #     job = query.first()
        #
        #     if job:
        #         worker_row = self._resolve_worker(worker_id)
        #
        #         if worker_row:
        #             abandoned = self.QueuedJob.objects.filter(
        #                 worker=worker_row, status='running'
        #             ).exclude(id=job.id)
        #             for orphan in abandoned:
        #                 orphan.status = 'failed'
        #                 orphan.finished_at = timezone.now()
        #                 orphan.error = 'Worker claimed a new job — this job was abandoned'
        #                 orphan.save(update_fields=['status', 'finished_at', 'error'])
        #                 logger.info(f"Failed abandoned job #{orphan.id} (worker claimed #{job.id})")
        #
        #         job.status = "running"
        #         job.started_at = timezone.now()
        #         job.worker_pid = os.getpid()
        #         update_fields = ['status', 'started_at', 'worker_pid']
        #         if worker_row:
        #             job.worker = worker_row
        #             update_fields.append('worker')
        #         job.save(update_fields=update_fields)
        #
        #         if worker_row:
        #             worker_row.status = 'busy'
        #             worker_row.current_job = job
        #             worker_row.last_heartbeat = timezone.now()
        #             worker_row.save(update_fields=['status', 'current_job', 'last_heartbeat'])
        #
        #     return job

        # REGRESSION 2026-05-18: select_for_update used outside transaction
        # Root cause: claim logic moved to framework-agnostic module without Django transaction wrapper
        # Fix: restore transaction.atomic() that existed in the old claim_job body
        # #110: resolve the worker inside the claim transaction so an idle claim attempt
        # is one commit, not two.
        with transaction.atomic():
            worker_row = self._resolve_worker(worker_id)
            if not worker_row:
                # I wish I had the time to: add a retry loop with exponential backoff
                # before auto-registering, to handle transient visibility delays
                worker_row = self._auto_register_worker(worker_id)
                if not worker_row and not worker_id.startswith(_EPHEMERAL_CLAIMER_PREFIXES):
                    return None
                # Ephemeral claimers (Lambda "lambda-<uuid>", HTTP trigger "trigger-<uuid>") never
                # have a Worker row, and the claim itself does not need one (worker=None is handled
                # by the shared claiming path). Before, they matched neither the UUID nor the
                # "worker_<node>_<pid>" format and silently claimed nothing in Django mode. Any
                # other unregistered worker id is still refused.
            return claim_next_job_with_queue_priority(worker_row, self, queues=queues)

    def request_job_revoke(self, job_id: int) -> bool:
        """Flag a running job for termination (see DatabaseBackend.request_job_revoke)."""
        return (
            self.QueuedJob.objects.filter(id=job_id, status="running").update(
                termination_reason="revoke_requested"
            )
            > 0
        )

    def is_job_revoke_requested(self, job_id: int) -> bool:
        return self.QueuedJob.objects.filter(
            id=job_id, status="running", termination_reason="revoke_requested"
        ).exists()

    def get_jobs_pending_retry(self, older_than_seconds: float = 60, limit: int = 100) -> list:
        """Failed jobs whose retry was never created (see DatabaseBackend)."""
        from sqlery.core.retry import RETRY_PENDING

        cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
        return list(
            self.QueuedJob.objects.filter(
                status="failed",
                termination_reason__startswith=RETRY_PENDING,
                finished_at__isnull=False,
                finished_at__lte=cutoff,
            ).order_by("finished_at")[:limit]
        )

    def settle_retry_pending(self, job_id: int) -> None:
        """No retry is owed any more: the failed job becomes final (moves to history as
        ``failed`` with the marker cleared). A job already in history is left alone."""
        row = (
            self.QueuedJob.objects.filter(id=job_id, status="failed")
            .values("created_at", "termination_reason")
            .first()
        )
        if row is not None:
            with transaction.atomic():
                history.move(
                    job_id,
                    row["created_at"],
                    "failed",
                    {
                        "termination_reason": history_core.settled_termination_reason(
                            row["termination_reason"]
                        )
                    },
                    require_status="failed",
                )

    def get_latest_retry(self, job_id: int):
        """Newest retry attempt (child via parent_job_id) of ``job_id`` (live or finished),
        or None."""
        return history.latest_retry(job_id)

    @retry_on_db_error()
    def claim_job_by_id(self, job_id: int, worker_id: str):
        """Atomically claim one specific queued job (Lambda / HTTP-trigger paths).

        Locks go ready entry first, then job row (the order every claim uses). An entry that
        another claimer holds means the job is being claimed: return None instead of waiting.
        """
        from django.db.models import F

        with transaction.atomic():
            if not is_sqlite():
                entries = JobReady.objects.filter(slot__in=ready.SLOTS, job_id=job_id)
                held = list(entries.select_for_update(skip_locked=True))
                if not held and entries.exists():
                    return None  # the entry exists but is locked: someone else is claiming it
            current = self.QueuedJob.objects.filter(id=job_id, status="queued").first()
            if current is None:
                return None
            ready.remove([job_id])
            updated = self.QueuedJob.objects.filter(
                id=job_id, created_at=current.created_at, status="queued"
            ).update(
                status="running",
                started_at=timezone.now(),
                # worker_pid lives on the attempt now (#138).
                # #CLEANUP: stop reading QueuedJob.worker_pid/child_pid, then drop the columns.
                version=F("version") + 1,
            )
            if updated != 1:
                transaction.set_rollback(True)  # keeps the entries: not claimed by us
                return None
            # Ephemeral claimers (lambda-/trigger-) have no Worker row: worker id stays NULL.
            job = self.QueuedJob.objects.filter(id=job_id, created_at=current.created_at).first()
            JobAttempt.open_new(job, self._attempt_worker_id(worker_id), os.getpid())
        return job

    def _attempt_worker_id(self, worker_id):
        """Id of the registered Worker row ``worker_id`` names, else None (ephemeral claimers)."""
        worker_id = str(worker_id)
        if worker_id.startswith(_EPHEMERAL_CLAIMER_PREFIXES):
            return None
        try:
            row = self._resolve_worker(worker_id)
        except (ValueError, TypeError):
            return None
        return row.id if row is not None else None

    @retry_on_db_error()
    def release_claimed_job(
        self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs
    ):
        """Release a job after processing and update worker state."""
        with transaction.atomic():
            JobAttempt.close_open(
                job.id, OUTCOME_SUCCESS if status == "success" else OUTCOME_ABANDONED
            )
            job.status = status
            job.finished_at = timezone.now()
            if job.started_at:
                job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
            # job.worker = None  # Keep worker FK for historical tracking
            update_fields = ["status", "finished_at", "duration_seconds"]
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
                    update_fields.append(key)
            job.save(update_fields=update_fields)

            # Update worker back to idle
            worker_row = self._resolve_worker(worker_id)
            if worker_row:
                worker_row.status = "idle"
                # Old: worker_row.current_job = None
                worker_row.current_job_id = None
                worker_row.jobs_processed = jobs_processed
                worker_row.last_heartbeat = timezone.now()
                # Old: update_fields=["status", "current_job", "jobs_processed", "last_heartbeat"]
                worker_row.save(
                    update_fields=["status", "current_job_id", "jobs_processed", "last_heartbeat"]
                )

        if status in ("success", "failed"):
            ready.release_dependents_quietly(job.id, job.parent_job_id)
        return job

    def _resolve_worker(self, worker_id: str):
        """Resolve a worker_id string to a Worker model instance."""
        # import uuid  # moved to top-level

        # Try UUID first
        try:
            worker_uuid = uuid.UUID(worker_id)
            return self.Worker.objects.filter(id=worker_uuid).first()
        except ValueError:
            pass

        # Parse "worker_<node>_<pid>" format
        parts = worker_id.split("_")
        if parts[0] == "worker" and len(parts) >= 3:
            pid = int(parts[-1])
            node_id = "_".join(parts[1:-1])
            return self.Worker.objects.filter(node_id=node_id, pid=pid).first()

        return None

    def _auto_register_worker(self, worker_id: str):
        """Auto-register a worker on-demand if not found in database.

        This prevents a race condition where a worker's heartbeat hasn't
        propagated yet when it tries to claim its first job.

        Args:
            worker_id: Worker identifier string (e.g., "worker_node_12345")

        Returns:
            Worker instance if created, None if worker_id format is invalid
        """
        # I wish I had the time to: validate worker_id against a whitelist
        # or require a shared secret to prevent unauthorized worker registration

        parts = worker_id.split("_")
        if parts[0] != "worker" or len(parts) < 3:
            return None

        pid = int(parts[-1])
        node_id = "_".join(parts[1:-1])

        worker, created = self.Worker.objects.get_or_create(
            node_id=node_id,
            pid=pid,
            defaults={
                "status": "idle",
                "last_heartbeat": timezone.now(),
            },
        )

        if created:
            logger.info(f"Auto-registered worker {worker_id} on-demand")

        return worker

    def is_worker_paused(self, worker_id: str) -> bool:
        """Check if worker is currently paused."""
        worker = self._resolve_worker(worker_id)
        if not worker or not worker.paused_until:
            return False
        if worker.paused_until > timezone.now():
            return True
        # Pause expired, clear it
        worker.paused_until = None
        worker.save(update_fields=["paused_until"])
        return False

    def get_queue_stats(self, queue_name: str | None = None) -> dict:
        """Get queue statistics (counts by status), live and finished jobs together."""
        query = JobAll.objects.all()

        if queue_name:
            query = query.filter(queue_name=queue_name)

        stats = query.order_by().values("status").annotate(count=Count("id"))

        result = {
            "queued": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
        }

        for stat in stats:
            result[stat["status"]] = stat["count"]

        if queue_name:
            result["queue_name"] = queue_name

        return result

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued or staged job, spanning QueuedJob and ScheduledJob tables."""
        # Old (single-table — staged jobs were uncancellable via this API):
        # updated = self.QueuedJob.objects.filter(id=job_id, status="queued").update(
        #     status="failed", error="Cancelled by user"
        # )
        # return updated > 0
        # Item 7: Add created_at to filter so PG prunes to one partition (write-path pruning).
        # SELECT created_at first (cancel_job receives only job_id, not the full job object).
        # Old (id-only filter — does not prune to partition on PG):
        # updated = self.QueuedJob.objects.filter(id=job_id, status="queued").update(
        #     status="failed", error="Cancelled by user"
        # )
        row = self.QueuedJob.objects.filter(id=job_id, status="queued").values("created_at").first()
        if row:
            with transaction.atomic():
                # Entries first, then the job row: the lock order of a claim. A claimer that
                # holds the entry finishes first; the cancel then finds the job running.
                ready.remove([job_id])
                # The cancelled job is final: it moves to history as failed.
                updated = int(
                    history.move(
                        job_id,
                        row["created_at"],
                        "failed",
                        {"error": "Cancelled by user"},
                        require_status="queued",
                    )
                )
                if updated == 0:
                    transaction.set_rollback(True)  # not cancelled: keep the entries
        else:
            updated = 0
        if updated > 0:
            # Jobs that waited on it fail now (they would at their next claim).
            ready.release_dependents_quietly(job_id)
            return True
        deleted, _ = self.ScheduledJob.objects.filter(id=job_id).delete()
        return deleted > 0

    def retry_failed_jobs(self, queue_name: str | None = None, max_jobs: int | None = None) -> int:
        """Retry failed jobs: each failed job that has not been retried gets a fresh queued
        copy (``retry_count`` 0, ``parent_job_id`` = the failed job). The failed job itself
        is in history and stays as it was (history is insert-only); having a retry child, it
        is not retried a second time."""
        retried = JobAll.objects.filter(parent_job_id=db_models.OuterRef("id"))
        query = (
            JobHistory.objects.filter(status="failed")
            .filter(~db_models.Exists(retried))
            .order_by("finished_at", "id")
        )
        if queue_name:
            query = query.filter(queue_name=queue_name)
        if max_jobs:
            query = query[:max_jobs]
        failed = list(query)
        with transaction.atomic():
            for old in failed:
                task_id = old.scheduled_task_id
                if task_id is not None and not self.ScheduledTask.objects.filter(pk=task_id).exists():
                    task_id = None  # the task is gone
                self.QueuedJob.objects.create(
                    task_path=old.task_path,
                    kwargs=old.kwargs,
                    queue_name=old.queue_name,
                    priority=old.priority,
                    max_retries=old.max_retries,
                    retry_backoff=old.retry_backoff,
                    allow_parallel=old.allow_parallel,
                    timeout_seconds=old.timeout_seconds,
                    retry_count=0,
                    scheduled_task_id=task_id,
                    job_name=old.job_name,
                    retry_intervals=old.retry_intervals,
                    meta=old.meta,
                    dependencies=old.dependencies,
                    tags=old.tags,
                    webhook_url=old.webhook_url,
                    webhook_events=old.webhook_events,
                    webhook_max_retries=old.webhook_max_retries,
                    on_success_path=old.on_success_path,
                    on_failure_path=old.on_failure_path,
                    ttl=old.ttl,
                    result_ttl=old.result_ttl,
                    failure_ttl=old.failure_ttl,
                    parent_job_id=old.id,
                    status="queued",
                )
        return len(failed)

    def get_due_scheduled_tasks(self):
        """Get scheduled tasks that are due to run."""
        return list(
            self.ScheduledTask.objects.filter(
                enabled=True, next_run_at__lte=timezone.now()
            ).order_by("next_run_at")
        )

    def create_scheduled_task(
        self,
        name: str,
        task_path: str,
        cron_expression: str,
        queue_name: str,
        priority: int,
        enabled: bool = True,
        next_run_at: datetime | None = None,
    ):
        """Create a new scheduled task."""
        extra = {"next_run_at": next_run_at} if next_run_at is not None else {}
        task = self.ScheduledTask.objects.create(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
            **extra,
        )
        return task

    def get_worker_heartbeats(self, active_only: bool = True):
        """Get worker heartbeats."""
        query = self.Worker.objects.all()

        if active_only:
            # Old: hardcoded 60s — a fourth, disagreeing definition of "alive".
            threshold = timezone.now() - timedelta(
                seconds=get_setting("WORKER_ALIVE_TIMEOUT", 30)
            )
            query = query.filter(last_heartbeat__gte=threshold).exclude(status="dead")

        return list(query.order_by("-last_heartbeat"))

    @retry_on_db_error()
    def update_worker_heartbeat(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
        jobs_processed: int | None = None,
        total_busy_seconds: float | None = None,
    ):
        """Update or create worker heartbeat."""
        # import socket  # moved to top-level
        # import uuid  # moved to top-level

        # Check if worker_id is a UUID (from cleanup_dead_workers)
        try:
            worker_uuid = uuid.UUID(worker_id)
            # Update by UUID - DO NOT update heartbeat when marking as dead
            update_fields = {
                "status": status,
                "current_job_id": current_job_id,
            }
            # Only update heartbeat for active workers, not dead ones
            if status != "dead":
                update_fields["last_heartbeat"] = timezone.now()
            # Update jobs_processed if provided
            if jobs_processed is not None:
                update_fields["jobs_processed"] = jobs_processed
            if total_busy_seconds is not None:
                update_fields["total_busy_seconds"] = total_busy_seconds

            self.Worker.objects.filter(id=worker_uuid).update(**update_fields)
            return
        except ValueError:
            # Not a UUID, parse as worker_id format
            pass

        # Extract node_id and pid from worker_id parameter
        # Format: "worker_<node>_<pid>" or "daemon_<node>"
        parts = worker_id.split("_")

        if parts[0] == "daemon":
            # Daemon heartbeat - use PID 0 as special marker for daemon processes
            node_id = "_".join(parts[1:])  # Join remaining parts for node_id
            pid = 0
        elif parts[0] == "worker":
            # Worker heartbeat - extract node and pid from worker_id
            # Format: worker_<node>_<pid>
            pid = int(parts[-1])  # Last part is PID
            node_id = "_".join(parts[1:-1])  # Middle parts are node_id
        else:
            # Fallback for unknown format - use current process
            node_id = socket.gethostname()
            pid = os.getpid()

        defaults = {
            "status": status,
            "current_job_id": current_job_id,
            "last_heartbeat": timezone.now(),
        }
        # Update jobs_processed if provided
        if jobs_processed is not None:
            defaults["jobs_processed"] = jobs_processed
        if total_busy_seconds is not None:
            defaults["total_busy_seconds"] = total_busy_seconds

        # Try lightweight UPDATE first (no row lock contention).
        # Only fall back to update_or_create for initial registration.
        rows = self.Worker.objects.filter(node_id=node_id, pid=pid).update(**defaults)
        if rows == 0:
            self.Worker.objects.update_or_create(node_id=node_id, pid=pid, defaults=defaults)

    def delete_worker_registration(self, worker_id: str) -> int:
        """Delete any Worker row for this worker_id (stale record from a crashed run).

        Called once at worker startup so the subsequent update_or_create creates
        a fresh row with a reset started_at instead of reusing the old one.
        """
        worker_row = self._resolve_worker(worker_id)
        if worker_row:
            worker_row.delete()
            return 1
        return 0

    def refresh_worker_heartbeat(self, worker_id):
        """Update ONLY last_heartbeat for a worker. Does not touch status or current_job.

        Used by the daemon to keep workers alive without interfering with
        the worker's own state management (status, current_job).
        """
        # import uuid  # moved to top-level

        try:
            worker_uuid = uuid.UUID(str(worker_id))
            self.Worker.objects.filter(id=worker_uuid).update(last_heartbeat=timezone.now())
        except (ValueError, TypeError):
            logger.warning(f"refresh_worker_heartbeat: invalid worker_id {worker_id}")

    def cleanup_jobs(
        self,
        status: str | None = None,
        max_age_days: int | None = None,
        max_count: int | None = None,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up finished jobs (history) based on retention policy.

        Only history is ever pruned; queued and running jobs are never touched. The age of a
        finished job is the time since it finished.

        On a partitioned PostgreSQL install (history ``PARTITION BY RANGE (finished_at)``)
        whole partitions past ``SQLERY_PARTITION_RETENTION`` are dropped through
        ``sqlery.core.partitioning`` (the attempt table is partitioned too and drops with
        them; see ``sqlery.core.history``). Advisory lock (pg_try_advisory_lock) is acquired
        inside reclaim_drained_partitions; if not acquired the call returns 0 without error.
        Elsewhere history is deleted in keyset batches of ``CLEANUP_BATCH_SIZE`` rows, and the
        finished attempts of the deleted jobs go with each batch.
        """
        # --- Partition reclaim path (PostgreSQL + partitioned table) ---
        if self._partitioned_pg() and _partitioning is not None:
            if dry_run:
                # dry_run is not meaningful for partition-drop path; return estimate
                query = JobHistory.objects.all()
                if status:
                    query = query.filter(status=status)
                if queue_name:
                    query = query.filter(queue_name=queue_name)
                return {"deleted": 0, "count": query.count()}

            retention_str = get_setting("SQLERY_PARTITION_RETENTION", "30 days")
            archive_hook = get_setting("SQLERY_PARTITION_ARCHIVE_HOOK", None)
            cur = self.get_raw_cursor()
            try:
                # Partition reclaim destroys all finished jobs in drained partitions (beyond
                # SQLERY_PARTITION_RETENTION) by default. Set SQLERY_PARTITION_ARCHIVE_HOOK
                # to archive instead. The archive hook receives (cur, partition_name) and
                # must not execute arbitrary SQL via string interpolation.
                dropped_by_table = history_core.reclaim_partitions(
                    cur, retention_str, archive_hook, _partitioning
                )
                attempts_partitioned = _partitioning.is_partitioned(cur, JobAttempt._meta.db_table)
            finally:
                if cur is not None:
                    cur.close()
            if not attempts_partitioned:
                self._prune_attempts_without_history()
            return {
                "deleted": 0,
                "reclaimed_via_partition_drop": True,
                "dropped_partitions": sum(dropped_by_table.values()),
                "dropped_by_table": dropped_by_table,
                "note": (
                    "Partition reclaim: jobs beyond retention destroyed by default. "
                    "Set SQLERY_PARTITION_ARCHIVE_HOOK to archive instead."
                ),
            }

        # --- SQLite or non-partitioned PG: batched DELETE of history ---
        query = JobHistory.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        if max_age_days:
            cutoff = timezone.now() - timedelta(days=max_age_days)
            query = query.filter(finished_at__lt=cutoff)

        if dry_run:
            count = query.count()
            return {"deleted": 0, "count": count}

        # Keyset-batched loop: at most CLEANUP_BATCH_SIZE rows per DELETE. The DELETE
        # re-applies the SAME retention filter (`query`) restricted to the selected ids, so
        # the deleted set is always a subset of the selected set — guaranteeing forward progress
        # (no infinite re-selection).
        total_deleted = 0
        while True:
            ids = list(
                query.order_by("finished_at", "id").values_list("id", flat=True)[:CLEANUP_BATCH_SIZE]
            )
            if not ids:
                break
            deleted_count = self._delete_history_batch(query.filter(id__in=ids), ids)
            if not deleted_count:
                # No selected row was deletable (all gone mid-loop) — stop to avoid
                # re-selecting the same ids indefinitely.
                break
            total_deleted += deleted_count
            time.sleep(0.1)

        return {"deleted": total_deleted, "count": total_deleted}

    @staticmethod
    def _delete_history_batch(queryset, ids: list) -> int:
        """Delete the history rows of ``queryset`` (``ids``) and their finished attempts;
        returns the rows deleted. Retention is the only thing that removes history, and an
        attempt outlives neither its job nor the history row of it."""
        with transaction.atomic():
            # _raw_delete: history rows have no relations to cascade over.
            deleted = queryset._raw_delete(using=queryset.db)
            JobAttempt.objects.filter(job_id__in=ids, finished_at__isnull=False)._raw_delete(
                using=JobAttempt.objects.db
            )
        return deleted

    def _prune_attempts_without_history(self, limit: int = 5000) -> int:
        """Delete finished attempts whose job is in neither the live table nor history (one
        bounded batch per call). Used where attempts cannot be dropped with a partition."""
        has_job = JobAll.objects.filter(id=db_models.OuterRef("job_id"))
        ids = list(
            JobAttempt.objects.filter(finished_at__isnull=False)
            .filter(~db_models.Exists(has_job))
            .values_list("id", flat=True)[:limit]
        )
        if not ids:
            return 0
        return JobAttempt.objects.filter(id__in=ids)._raw_delete(using=JobAttempt.objects.db)

    def cleanup_jobs_by_count(
        self,
        status: str | None = None,
        keep_count: int = 1000,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Keep only the most recent N finished jobs (by finish time); delete older history."""
        query = JobHistory.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        total = query.count()
        kept = min(total, keep_count)
        if dry_run:
            return {"deleted": 0, "count": max(0, total - keep_count), "kept": kept}

        # Rows past the newest ``keep_count``: each pass takes the next batch of them (what
        # was deleted is gone from the offset window, so the offset stays put).
        ordered = query.order_by("-finished_at", "-id")
        deleted = 0
        while True:
            ids = list(
                ordered.values_list("id", flat=True)[keep_count : keep_count + CLEANUP_BATCH_SIZE]
            )
            if not ids:
                break
            batch = self._delete_history_batch(JobHistory.objects.filter(id__in=ids), ids)
            if not batch:
                break
            deleted += batch
        return {"deleted": deleted, "count": deleted, "kept": kept}

    def get_database_stats(self) -> dict:
        """Get database statistics."""
        job_counts = JobAll.objects.order_by().values("status").annotate(count=Count("id"))
        registry_counts = self.JobRegistry.objects.values("registry_type").annotate(
            count=Count("id")
        )

        stats = {
            "total_jobs": JobAll.objects.count(),
            "job_counts": {item["status"]: item["count"] for item in job_counts},
            "total_registries": self.JobRegistry.objects.count(),
            "registry_counts": {item["registry_type"]: item["count"] for item in registry_counts},
            "total_scheduled_tasks": self.ScheduledTask.objects.count(),
            "enabled_scheduled_tasks": self.ScheduledTask.objects.filter(enabled=True).count(),
            "total_workers": self.Worker.objects.count(),
        }

        return stats

    @retry_on_db_error()
    def vacuum_database(self) -> dict:
        """Run database vacuum/optimize (PostgreSQL VACUUM).

        On a partitioned PG install, VACUUM ANALYZE on the parent table
        (sqlery_queued_job) is skipped — partition DROP leaves nothing to
        vacuum on the parent and individual partitions are vacuumed by
        autovacuum per-child (D5/R3). Other tables are always vacuumed.
        SQLite path is unchanged.
        """
        # from django.db import connection  # moved to top-level

        with connection.cursor() as cursor:
            try:
                if is_sqlite():
                    # SQLite: single VACUUM for entire database (no per-table or ANALYZE)
                    cursor.execute("VACUUM")
                else:
                    # PostgreSQL: per-table VACUUM ANALYZE
                    # Old (unconditional): cursor.execute("VACUUM ANALYZE sqlery_queued_job")
                    # Partition DROP leaves nothing to vacuum on parent table; skip when partitioned.
                    if not self._partitioned_pg():
                        cursor.execute("VACUUM ANALYZE sqlery_queued_job")
                        cursor.execute("VACUUM ANALYZE sqlery_job_history")
                    # else: partition DROP leaves nothing to vacuum on parent; skip (D5/R3)
                    #       (the history and the attempt table are partitioned the same way)
                    cursor.execute("VACUUM ANALYZE sqlery_scheduled_task")
                    cursor.execute("VACUUM ANALYZE sqlery_registry")
                    cursor.execute("VACUUM ANALYZE sqlery_worker")
                return {"success": True, "message": "Database vacuumed successfully"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def add_job_to_registry(
        self,
        job_id: int,
        registry_type: str,
        metadata: dict | None = None,
    ):
        """Add job to a registry for lifecycle tracking."""
        self.JobRegistry.objects.create(
            job_id=job_id,
            registry_type=registry_type,
            metadata=metadata or {},
        )

    def remove_job_from_registry(self, job_id: int, registry_type: str):
        """Remove job from a registry."""
        self.JobRegistry.objects.filter(
            job_id=job_id,
            registry_type=registry_type,
            exited_at__isnull=True,
        ).update(exited_at=timezone.now())

    def get_registry_jobs(
        self,
        registry_type: str,
        queue_name: str | None = None,
        limit: int | None = None,
    ) -> list:
        """Get jobs in a specific registry."""
        # Old: .select_related("job") and filter(job__queue_name=queue_name)
        # JobRegistry.job FK demoted to job_id (D4, Phase 15); select_related and FK traversal removed.
        query = self.JobRegistry.objects.filter(
            registry_type=registry_type,
            exited_at__isnull=True,
        )

        if queue_name:
            # Old: query = query.filter(job__queue_name=queue_name)
            # queue_name traversal via FK not possible after demotion — fetch job_ids then filter
            job_ids = list(query.values_list("job_id", flat=True))
            job_ids = list(
                JobAll.objects.filter(id__in=job_ids, queue_name=queue_name).values_list("id", flat=True)
            )
            query = query.filter(job_id__in=job_ids)

        if limit:
            query = query[:limit]

        # Old: return [entry.job for entry in query]
        job_ids = list(query.values_list("job_id", flat=True))
        # live or finished: the registries mostly list finished jobs
        return list(JobAll.objects.filter(id__in=job_ids))

    def cleanup_registry(
        self,
        registry_type: str | None = None,
        max_age_days: int | None = None,
    ) -> dict:
        """Clean up old registry entries."""
        query = self.JobRegistry.objects.all()

        if registry_type:
            query = query.filter(registry_type=registry_type)

        if max_age_days:
            cutoff = timezone.now() - timedelta(days=max_age_days)
            query = query.filter(entered_at__lt=cutoff)

        count = query.count()
        query.delete()

        return {"deleted": count}

    def get_job_by_id(self, job_id: int):
        """Get job by ID, spanning both sqlery_queued_job and sqlery_scheduled_job."""
        # Old (single-table — staged jobs were invisible to status APIs):
        # try:
        #     return self.QueuedJob.objects.get(id=job_id)
        # except self.QueuedJob.DoesNotExist:
        #     return None
        # Item 10: Verified — this is a full-row SELECT by id (not an UPDATE).
        # PG index scan by id is acceptable; partition pruning on SELECT is less
        # critical than on UPDATE. Full row returned including created_at — no
        # .only() that would drop the field. No change needed; checklist item 10 verified.
        try:
            return self.QueuedJob.objects.get(id=job_id)
        except self.QueuedJob.DoesNotExist:
            pass
        # A finished job is in history (read-only).
        finished = JobHistory.objects.filter(id=job_id).first()
        if finished is not None:
            return finished
        try:
            return self.ScheduledJob.objects.get(id=job_id)
        except self.ScheduledJob.DoesNotExist:
            return None

    @staticmethod
    def _already_finished(job, expected_version, attempt):
        """A completion for a job that is already in history.

        A fenced completion (it holds a version or an attempt handle) is stale: another
        completion, or recovery, got there first. An unfenced caller just gets the job back.
        """
        if expected_version is not None or attempt is not None:
            raise JobFencingError(
                f"Job {job.id} already finished ({job.status}); completion rejected"
            )
        return job

    def mark_job_success(
        self,
        job_id: int,
        output: str = "",
        expected_version: int | None = None,
        attempt=None,
    ):
        """Mark job as successful (the job moves to history, see ``QueuedJob.mark_success``).

        Staged ScheduledJob rows (not yet promoted) do not have mark_success;
        the guard prevents AttributeError if an operator calls this for a staged id.

        expected_version fences the write against a stale lease (see
        DatabaseBackend.mark_job_success): without it, the fresh
        get_job_by_id() below always reads the row's *current* version,
        which defeats mark_success()'s own optimistic-locking CAS — a
        superseded worker would silently "win" the CAS against a version
        it never actually held.
        """
        job = self.get_job_by_id(job_id)
        if isinstance(job, JobHistory):
            return self._already_finished(job, expected_version, attempt)
        # Old: if job: job.mark_success(...)  <-- AttributeError for ScheduledJob (IN-01)
        if job and hasattr(job, "mark_success"):
            if expected_version is not None:
                job.version = expected_version
            try:
                job.mark_success(output=output, attempt=attempt)
            except ConcurrentModificationError as e:
                raise JobFencingError(str(e)) from e
        return job

    def mark_job_failed(
        self,
        job_id: int,
        error: str,
        traceback: str = "",
        expected_version: int | None = None,
        termination_reason: str = "",
        attempt=None,
    ):
        """Mark job as failed.

        Staged ScheduledJob rows do not have mark_failed; guard prevents
        AttributeError if called for a staged job id (IN-01).

        See mark_job_success for expected_version fencing semantics.
        """
        job = self.get_job_by_id(job_id)
        if isinstance(job, JobHistory):
            return self._already_finished(job, expected_version, attempt)
        # Old: if job: job.mark_failed(...)  <-- AttributeError for ScheduledJob (IN-01)
        if job and hasattr(job, "mark_failed"):
            if expected_version is not None:
                job.version = expected_version
            try:
                job.mark_failed(
                    error=error,
                    traceback=traceback,
                    termination_reason=termination_reason,
                    attempt=attempt,
                )
            except ConcurrentModificationError as e:
                raise JobFencingError(str(e)) from e
        return job

    def mark_job_archived(self, job_id: int):
        """Mark a failed job as archived (a retry has been created for it): the job moves to
        history as ``archived`` and its retry marker is cleared. A job that is already in
        history is final and stays as it is."""
        # Item 8: created_at in the filter lets PG prune to one partition (write-path pruning).
        row = (
            self.QueuedJob.objects.filter(id=job_id, status="failed")
            .values("created_at", "termination_reason")
            .first()
        )
        if row:
            with transaction.atomic():
                history.move(
                    job_id,
                    row["created_at"],
                    "archived",
                    {
                        "termination_reason": history_core.settled_termination_reason(
                            row["termination_reason"]
                        )
                    },
                    require_status="failed",
                )

    def cascade_ancestor_status(self, job_id: int, status: str):
        """Walk the parent_job_id chain, set the live ancestors to the given status.

        Ancestors that are already in history are final (history is insert-only) and are only
        walked through. A live ancestor that is not already ``success`` / ``archived`` becomes
        ``status`` (a terminal status moves it into history).
        """
        current_id = (
            JobAll.objects.filter(id=job_id).values_list("parent_job_id", flat=True).first()
        )
        seen: set = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            job_row = (
                JobAll.objects.filter(id=current_id).values("created_at", "parent_job_id").first()
            )
            if not job_row:
                break
            live = self.QueuedJob.objects.filter(
                id=current_id, created_at=job_row["created_at"]
            ).exclude(status__in=("success", "archived"))
            if history_core.is_terminal(status):
                with transaction.atomic():
                    history.move(
                        current_id,
                        job_row["created_at"],
                        status,
                        {},
                        require_status=("queued", "running", "failed", "shutting_down"),
                    )
            else:
                live.update(status=status)
            current_id = job_row["parent_job_id"]

    def has_pending_job_for_scheduled_task(self, task_id: int) -> bool:
        """Check if scheduled task has pending jobs."""
        return self.QueuedJob.objects.filter(
            scheduled_task_id=task_id,
            status__in=["queued", "running"],
        ).exists()

    def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
        """Update scheduled task's next run time."""
        self.ScheduledTask.objects.filter(id=task_id).update(next_run_at=next_run_at)

    @retry_on_db_error()
    def advance_scheduled_task_if_due(
        self,
        task_id: int,
        observed_next_run_at: datetime,
        new_next_run_at: datetime,
        job_kwargs: dict,
    ) -> Any:
        """Atomically advance next_run_at on a CAS and enqueue in the same txn.

        Inside a single ``transaction.atomic()`` block, a queryset ``.update()``
        filtered on ``next_run_at=observed_next_run_at`` advances the row to
        ``new_next_run_at`` ONLY when it still matches (rowcount-CAS — the
        ScheduledTask has no version column, so the observed due time is the
        idempotency token). On a winning CAS (rowcount == 1) the queued job is
        created via ``self.create_job`` in the ambient transaction so the advance
        and the enqueue commit together (CRON-01); only the caller whose advance
        wins enqueues, so two briefly-overlapping leaders cannot double-fire
        (CRON-04). When the CAS is lost (rowcount != 1) no job is created.

        The rowcount-CAS gives exactly-once on both SQLite and Postgres, so a
        ``select_for_update`` is not required.

        Args:
            task_id: Scheduled task ID.
            observed_next_run_at: The ``next_run_at`` observed when the task was due.
            new_next_run_at: The value to advance to when the CAS wins.
            job_kwargs: Keyword arguments forwarded to ``create_job``.

        Returns:
            The created QueuedJob when this caller won the CAS, otherwise ``None``.
        """
        with transaction.atomic():
            # # Old (WR-05): filtered only on id + next_run_at, so a task disabled
            # # mid-cycle could still win the CAS and fire. Re-check enabled=True.
            # advanced = self.ScheduledTask.objects.filter(
            #     id=task_id, next_run_at=observed_next_run_at
            # ).update(next_run_at=new_next_run_at)
            advanced = self.ScheduledTask.objects.filter(
                id=task_id, next_run_at=observed_next_run_at, enabled=True
            ).update(next_run_at=new_next_run_at)
            if advanced != 1:
                # Another leader already advanced this tick — do not enqueue.
                return None
            return self.create_job(**job_kwargs)

    def update_scheduled_task(self, task_id: int, **updates) -> Any:
        """Update scheduled task fields."""
        self.ScheduledTask.objects.filter(id=task_id).update(**updates)
        return self.get_scheduled_task(task_id)

    def delete_scheduled_task(self, task_id: int) -> bool:
        """Delete scheduled task."""
        deleted, _ = self.ScheduledTask.objects.filter(id=task_id).delete()
        return deleted > 0

    def get_scheduled_tasks(self, enabled_only: bool = False) -> list:
        """Get all scheduled tasks."""
        query = self.ScheduledTask.objects.all()

        if enabled_only:
            query = query.filter(enabled=True)

        return list(query.order_by("name"))

    def get_scheduled_task(self, task_id: int):
        """Get scheduled task by ID."""
        try:
            return self.ScheduledTask.objects.get(id=task_id)
        except self.ScheduledTask.DoesNotExist:
            return None

    def get_running_jobs(self, queue_name: str | None = None) -> list:
        """Get currently running jobs."""
        query = self.QueuedJob.objects.filter(status="running")

        if queue_name:
            query = query.filter(queue_name=queue_name)

        return list(query)

    def get_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        """Build RunningJobLiveness records for the zombie sweep.

        Preserves the original daemon query: select_related('worker') over all
        ``status='running'`` jobs, optionally filtered to ``queue_names``.
        """
        from sqlery.core.liveness import RunningJobLiveness

        query = self.QueuedJob.objects.filter(status="running")
        if queue_names:
            query = query.filter(queue_name__in=queue_names)
        query = query.select_related("worker")

        jobs = list(query)
        attempts = self.get_open_attempts([j.id for j in jobs])
        records = []
        for job in jobs:
            worker = job.worker
            attempt = attempts.get(job.id)
            records.append(
                RunningJobLiveness(
                    job_id=job.id,
                    started_at=job.started_at,
                    # The open attempt; the row column only for jobs claimed before attempts.
                    worker_pid=attempt.worker_pid if attempt else job.worker_pid,
                    worker_node_id=worker.node_id if worker else None,
                    worker_status=worker.status if worker else None,
                    worker_current_job_id=worker.current_job_id if worker else None,
                    worker_last_heartbeat=worker.last_heartbeat if worker else None,
                    worker_friendly_name=worker.friendly_name if worker else None,
                    has_worker=worker is not None,
                )
            )
        return records

    def fail_zombie_job(self, job_id: int, reason: str) -> bool:
        """Mark a running job failed with termination_reason='zombie_job'."""
        job = self.QueuedJob.objects.filter(id=job_id).first()
        if job is None:
            return False
        job.mark_failed(error=reason, termination_reason="zombie_job")
        return True

    def has_running_jobs_in_queue(self, queue_name: str, exclude_job_id: int | None = None) -> bool:
        """Check if queue has running jobs."""
        query = self.QueuedJob.objects.filter(
            queue_name=queue_name,
            status="running",
        )

        if exclude_job_id:
            query = query.exclude(id=exclude_job_id)

        return query.exists()

    def update_job_child_pid(self, job_id: int, child_pid: int, created_at=None, attempt=None):
        """Store the forked child PID on the job's open attempt.

        Args:
            job_id: QueuedJob primary key.
            child_pid: PID of the forked child process.
            created_at: Unused (the attempt table has no partition key); kept for callers.
            attempt: Handle of the attempt the caller holds; when given only that exact
                attempt is updated, so a superseded worker cannot stamp a newer attempt.
        """
        # Old: wrote QueuedJob.child_pid (a job-row update per job). The PID now lives on the
        # attempt; #CLEANUP: drop QueuedJob.child_pid once no reader falls back to it.
        query = JobAttempt.objects.filter(finished_at__isnull=True)
        if attempt is not None:
            query = query.filter(id=attempt.id, token=attempt.token)
        else:
            query = query.filter(job_id=job_id)
        query.update(child_pid=child_pid)

    def attempt_table_exists(self) -> bool:
        return JobAttempt._meta.db_table in connection.introspection.table_names()

    def get_open_attempts(self, job_ids: list) -> dict:
        if not job_ids:
            return {}
        rows = JobAttempt.objects.filter(job_id__in=list(job_ids), finished_at__isnull=True)
        # Newest wins if several are open (cannot happen through claim; defensive).
        return {
            r.job_id: AttemptInfo(
                r.id, r.job_id, r.worker_id, r.worker_pid, r.child_pid, r.claimed_at
            )
            for r in rows.order_by("id")
        }

    def abandon_attempts(self, job_id: int) -> int:
        return JobAttempt.close_open(job_id, OUTCOME_ABANDONED)

    def _count_with_tag(self, queryset, tag: str) -> int:
        """Count rows carrying ``tag``; SQL ``contains`` where supported, Python otherwise
        (SQLite has no JSON ``contains`` lookup)."""
        from django.db import connection

        if connection.features.supports_json_field_contains:
            return queryset.filter(tags__contains=[tag]).count()
        return sum(1 for tags in queryset.values_list("tags", flat=True) if tags and tag in tags)

    def count_running_with_tag(self, tag: str) -> int:
        """Count currently running jobs with the given tag."""
        return self._count_with_tag(self.QueuedJob.objects.filter(status="running"), tag)

    def count_started_with_tag_since(self, tag: str, threshold) -> int:
        """Count jobs with the given tag that started since threshold."""
        return self._count_with_tag(
            # finished jobs live in history: read the union view
            JobAll.objects.filter(
                status__in=["running", "success", "failed"],
                started_at__gte=threshold,
                started_at__isnull=False,
            ),
            tag,
        )

    def get_expired_ttl_jobs(self) -> list:
        """Get queued jobs whose TTL has expired."""
        # from datetime import timedelta  # moved to top-level

        now = timezone.now()
        expired = []
        for job in self.QueuedJob.objects.filter(status="queued", ttl__isnull=False):
            if job.created_at + timedelta(seconds=job.ttl) < now:
                expired.append(job)
        return expired

    def acquire_tag_locks(self, tags: list[str]) -> bool:
        """Acquire exclusive locks on tag coordination rows."""
        # from .models import TagLock  # moved to top-level
        # from .db_compat import is_sqlite  # moved to top-level

        # Ensure TagLock rows exist
        existing = set(TagLock.objects.filter(tag__in=tags).values_list("tag", flat=True))
        new_tags = [tag for tag in tags if tag not in existing]
        if new_tags:
            TagLock.objects.bulk_create(
                [TagLock(tag=tag) for tag in new_tags],
                ignore_conflicts=True,
            )

        # Acquire exclusive locks (Postgres: SELECT FOR UPDATE SKIP LOCKED, SQLite: no-op).
        # Never wait on another worker's transaction: a row we cannot lock means the tag
        # is busy, and the caller treats the candidate as tag-limited.
        tag_queryset = TagLock.objects.filter(tag__in=tags)
        if is_sqlite():
            list(tag_queryset)
            return True
        assert_in_atomic_block("acquire_tag_locks")
        locked = {row.tag for row in tag_queryset.select_for_update(skip_locked=True)}
        return locked >= set(tags)

    def get_claimable_jobs(
        self,
        queues: list[str],
        priority_weights: dict[str, int] | None = None,
        limit: int = 1,
        exclude_ids: list | None = None,
    ) -> list:
        """Next claimable jobs in claim order, read through the ready queue (#139).

        Reads ready entries in order (``FOR UPDATE SKIP LOCKED`` on PostgreSQL, so the
        returned jobs are locked for the claim) and loads the job row under each by primary
        key. Entries whose job is no longer queued are dropped on the way.
        """
        # from django.db import models as db_models  # moved to top-level

        queryset = JobReady.objects.filter(queue_name__in=queues).filter(
            db_models.Q(scheduled_at__isnull=True) | db_models.Q(scheduled_at__lte=timezone.now())
        )

        if exclude_ids:
            queryset = queryset.exclude(job_id__in=exclude_ids)

        queryset = atomic_claim_job_queryset(queryset)

        # Build CASE expression for queue priority ordering
        order = ["-priority", "created_at", "job_id"]
        if priority_weights:
            case_whens = [
                db_models.When(queue_name=queue, then=db_models.Value(weight))
                for queue, weight in priority_weights.items()
                if queue in queues
            ]
            if case_whens:
                queue_priority_expr = db_models.Case(
                    *case_whens,
                    default=db_models.Value(0),
                    output_field=db_models.IntegerField(),
                )
                queryset = queryset.annotate(queue_priority=queue_priority_expr)
                order = ["-queue_priority", *order]
        entries = list(queryset.order_by(*order)[:limit])
        if not entries:
            return []

        pairs = db_models.Q()
        for entry in entries:
            pairs |= db_models.Q(id=entry.job_id, created_at=entry.created_at)
        rows = {j.id: j for j in self.QueuedJob.objects.filter(pairs)}
        missing = [e.job_id for e in entries if e.job_id not in rows]
        if missing:
            # Not found by (created_at, id): the row's created_at differs from the entry's
            # (edited by hand); fall back to the id alone before calling the entry stale.
            rows.update({j.id: j for j in self.QueuedJob.objects.filter(id__in=missing)})
        jobs, stale = [], []
        for entry in entries:
            job = rows.get(entry.job_id)
            if job is None or job.status != "queued":
                stale.append(entry.job_id)  # claimed elsewhere, cancelled or deleted
                continue
            job._ready_slot = entry.slot
            jobs.append(job)
        if stale:
            ready.remove(stale)
        return jobs

    def atomic_claim_job(self, job, worker) -> bool:
        """Atomically claim a specific job for a worker.

        Deleting the job's ready entry is the claim (it is locked on PostgreSQL, and its
        rowcount decides the race on SQLite); the job row is then set running and the attempt
        inserted. All of it is one unit inside the caller's transaction: if the attempt
        cannot be recorded the claim is rolled back (savepoint) and the job stays ready.
        """
        with transaction.atomic():
            slot = getattr(job, "_ready_slot", None)
            entries = JobReady.objects.filter(
                slot__in=ready.SLOTS if slot is None else [slot], job_id=job.id
            )
            deleted, _ = entries.delete()
            if deleted < 1:
                return False  # claimed by someone else, or no longer ready
            started_at = timezone.now()
            updated = self.QueuedJob.objects.filter(
                id=job.id, created_at=job.created_at, status="queued"
            ).update(
                status="running", worker=worker, started_at=started_at, version=db_models.F("version") + 1
            )
            if updated != 1:
                # The job changed under the entry we own: whoever changed it owns it now;
                # if it is somehow still queued, put the entry back.
                if self.QueuedJob.objects.filter(
                    id=job.id, created_at=job.created_at, status="queued"
                ).exists():
                    ready.add([job])
                return False
            # The version check of the old claim is the entry delete now; mirror the write.
            expected_version = job.version
            job.status = "running"
            job.worker = worker
            job.started_at = started_at
            job.version = expected_version + 1
            JobAttempt.open_new(job, getattr(worker, "id", None), os.getpid())
            return True

    # ----- ready queue (#139) ----------------------------------------------

    def ready_tables_exist(self) -> bool:
        tables = set(connection.introspection.table_names())
        return JobReady._meta.db_table in tables and JobBlocked._meta.db_table in tables

    def block_job(self, job) -> bool:
        return ready.block(job)

    def release_dependents(self, job_id: int, parent_job_id: int | None = None) -> int:
        return ready.release_dependents(job_id, parent_job_id)

    def maintain_ready_queue(
        self, queue_names: list[str] | None = None, limit: int = 200, after_job_id: int = 0
    ) -> dict:
        settled = history.settle_finished(limit)
        result = ready.maintain(queue_names, limit, after_job_id)
        result["settled"] = settled
        return result

    def history_table_exists(self) -> bool:
        return JobHistory._meta.db_table in set(connection.introspection.table_names())

    def claim_due_scheduled_task(self, task_id: int):
        """Atomically claim a scheduled task for processing."""
        # REGRESSION 2026-08-08: select_for_update used outside transaction
        # Root cause: same class of bug as 2026-05-18/2026-06-14 -- this method
        # was promoted to the framework-agnostic core.scheduler_tasks module
        # without an enclosing transaction.atomic(), so it worked on SQLite
        # but raised TransactionManagementError on PostgreSQL.
        try:
            with transaction.atomic():
                return atomic_claim_job_queryset(
                    self.ScheduledTask.objects.filter(
                        id=task_id,
                        enabled=True,
                        next_run_at__lte=timezone.now(),
                    )
                ).get()
        except self.ScheduledTask.DoesNotExist:
            return None

    def release_job(self, job_id: int):
        """Release a claimed job back to queued status (its open attempt is abandoned)."""
        with transaction.atomic():
            JobAttempt.close_open(job_id, OUTCOME_ABANDONED)
            self.QueuedJob.objects.filter(id=job_id).update(
                status="queued",
                started_at=None,
                worker_pid=None,
            )

    def get_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Get jobs with optional filtering and pagination.

        Returns live (sqlery_queued_job) and finished (sqlery_job_history) jobs together.
        Staged jobs (sqlery_scheduled_job) are in a separate table; use get_staged_jobs() for them.
        """
        # Staged jobs (sqlery_scheduled_job) are in a separate table; use get_staged_jobs() for them.
        query = JobAll.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        # Order by priority (desc) and created_at (asc)
        query = query.order_by("-priority", "created_at", "id")

        # Apply pagination
        return list(query[offset : offset + limit])

    def get_staged_jobs(
        self,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Return staged (pre-promotion) jobs from sqlery_scheduled_job.

        Args:
            queue_name: Optional queue filter.
            limit: Maximum number of results to return.
            offset: Pagination offset.

        Returns:
            list of ScheduledJob instances ordered by scheduled_at ascending.
        """
        query = self.ScheduledJob.objects.all()
        if queue_name:
            query = query.filter(queue_name=queue_name)
        query = query.order_by("scheduled_at")
        return list(query[offset : offset + limit])

    def count_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
    ) -> int:
        """Count jobs with optional filtering."""
        query = JobAll.objects.all()

        if status:
            query = query.filter(status=status)

        if queue_name:
            query = query.filter(queue_name=queue_name)

        return query.count()

    def claim_queue_leases(
        self,
        queues: list[str],
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
    ) -> list[str]:
        """Claim scheduler leases for the given queues.

        Returns the subset of queues successfully claimed. Expired leases are
        taken over atomically; live leases held by other daemons are skipped.
        """
        # from .models import DaemonLease  # moved to top-level
        # from django.db import IntegrityError  # moved to top-level

        claimed = []
        for queue_name in queues:
            if self._claim_one_lease(queue_name, daemon_id, node_id, pid, lease_secs):
                claimed.append(queue_name)
        return claimed

    @transaction.atomic
    def _claim_one_lease(
        self,
        queue_name: str,
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
    ) -> bool:
        """Atomically claim a single queue lease. Returns True if claimed."""
        # from .models import DaemonLease  # moved to top-level
        # from django.db import IntegrityError  # moved to top-level
        # from datetime import timedelta  # moved to top-level

        now = timezone.now()
        expires = now + timedelta(seconds=lease_secs)

        # Take over any expired lease
        updated = DaemonLease.objects.filter(
            queue_name=queue_name,
            expires_at__lt=now,
        ).update(
            daemon_id=daemon_id,
            node_id=node_id,
            pid=pid,
            acquired_at=now,
            expires_at=expires,
        )
        if updated:
            return True

        # Fresh insert (no existing row)
        try:
            DaemonLease.objects.create(
                queue_name=queue_name,
                daemon_id=daemon_id,
                node_id=node_id,
                pid=pid,
                acquired_at=now,
                expires_at=expires,
            )
            return True
        except IntegrityError:
            return False  # Live lease held by another daemon

    def renew_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
        lease_secs: int,
    ) -> None:
        """Extend expires_at for all owned leases by lease_secs from now."""
        # from .models import DaemonLease  # moved to top-level
        # from datetime import timedelta  # moved to top-level

        DaemonLease.objects.filter(
            queue_name__in=owned_queues,
            daemon_id=daemon_id,
        ).update(expires_at=timezone.now() + timedelta(seconds=lease_secs))

    def release_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
    ) -> None:
        """Delete lease rows for all owned queues on clean shutdown."""
        # from .models import DaemonLease  # moved to top-level

        DaemonLease.objects.filter(
            queue_name__in=owned_queues,
            daemon_id=daemon_id,
        ).delete()
