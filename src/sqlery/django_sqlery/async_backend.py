"""DjangoAsyncBackend — native-async Django implementation of AsyncDatabaseBackend (ASYN-02).

Django has neither ``transaction.aatomic`` nor ``connection.acursor`` (checked on
Django 5.2 and 6.x), so the multi-statement claim cannot be native async. ``aclaim_job``
therefore runs the shared sync claiming algorithm through ``sync_to_async``; all other
methods use the native async ORM (``aget``/``acreate``/``aupdate``/``adelete``/...).

Concurrency contract: see ``DjangoBackend.claim_job`` (PostgreSQL ``FOR UPDATE SKIP
LOCKED``; SQLite optimistic ``version`` CAS).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import connection
from django.utils import timezone

from sqlery.compat import AsyncDatabaseBackend
from sqlery.core.attempts import OUTCOME_ABANDONED, OUTCOME_FAILED, OUTCOME_SUCCESS
from sqlery.core.utils import reject_unawaited_coroutine

from .models import (
    DaemonLease,
    JobAttempt,
    JobRegistry,
    QueuedJob,
    ScheduledTask,
    Worker,
)

logger = logging.getLogger(__name__)


class DjangoAsyncBackend(AsyncDatabaseBackend):
    """Async backend backed by the Django ORM (native async, no thread offload)."""

    def __init__(self):
        """Initialize async backend with per-instance partition cache."""
        # WR-02: mirror DjangoBackend._partitioned_pg_cache so async callers don't
        # issue a pg_class roundtrip on every invocation.
        self._partitioned_pg_cache: bool | None = None

    def _partitioned_pg(self) -> bool:
        """True iff on PostgreSQL AND sqlery_queued_job is partitioned.

        Parity with DjangoBackend._partitioned_pg so async callers can branch on
        partitioning (e.g. cleanup→reclaim routing). SQLite / non-partitioned PG
        return False (D6 — unchanged path).
        """
        # WR-02: use per-instance cache to avoid a pg_class roundtrip on every call.
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
                    [QueuedJob._meta.db_table],
                )
                row = cur.fetchone()
            self._partitioned_pg_cache = bool(row and row[0])
        except Exception:
            # Fail open: leave cache as None so the next call retries (mirrors WR-01 fix
            # in DjangoBackend — transient DB error at startup must not permanently
            # disable partition routing for the lifetime of the process).
            logger.warning(
                "_partitioned_pg (async): catalog query failed — will retry on next call",
                exc_info=True,
            )
            return False
        return self._partitioned_pg_cache

    # ----- claim path ------------------------------------------------------

    async def anext_due_delay(self, queues: list[str]) -> float | None:
        """Seconds until the earliest queued-but-not-yet-due job on ``queues``."""
        from django.db.models import Min

        now = timezone.now()
        nxt = (
            await QueuedJob.objects.filter(
                queue_name__in=list(queues), status="queued", scheduled_at__gt=now
            ).aaggregate(m=Min("scheduled_at"))
        )["m"]
        return None if nxt is None else (nxt - now).total_seconds()

    async def aattempt_table_exists(self) -> bool:
        from asgiref.sync import sync_to_async

        from .backend import DjangoBackend

        return await sync_to_async(DjangoBackend().attempt_table_exists)()

    async def aclaim_job(self, queues: list[str], worker_id: str):
        """Atomically claim the next queued job for one of ``queues``.

        Runs the same claiming algorithm as the sync path
        (``claim_next_job_with_queue_priority``: TTL expiry, dependency checks, tag limits,
        ``SELECT FOR UPDATE SKIP LOCKED`` on PostgreSQL, version CAS on SQLite) through
        ``sync_to_async``: Django has neither ``acursor`` nor ``transaction.aatomic``, so the
        multi-statement claim cannot be native async.

        Returns the claimed ``QueuedJob`` with status='running', or ``None`` if nothing
        was available.
        """
        from asgiref.sync import sync_to_async

        return await sync_to_async(self._claim_job_sync)(list(queues), worker_id)

    @staticmethod
    def _claim_job_sync(queues: list[str], worker_id: str):
        """Sync claim body for :meth:`aclaim_job`.

        Unlike ``DjangoBackend.claim_job`` this does not require ``worker_id`` to name a
        registered Worker row (async workers use arbitrary ids); an unresolvable id claims
        with ``worker=None``.
        """
        from django.db import transaction

        from sqlery.core.claiming import claim_next_job_with_queue_priority

        from .backend import DjangoBackend

        backend = DjangoBackend()
        try:
            worker_row = backend._resolve_worker(str(worker_id))
        except (ValueError, TypeError):
            worker_row = None
        with transaction.atomic():
            return claim_next_job_with_queue_priority(worker_row, backend, queues=queues)

    # ----- cleanup routing (async mirror of DjangoBackend.cleanup_jobs) -----

    async def acleanup_jobs(self, **kwargs) -> dict:
        """Async cleanup routing — delegates reclaim to sync backend on PG+partitioned.

        The partition reclaim path (reclaim_drained_partitions) requires a raw
        synchronous psycopg cursor which is not available in the async backend.
        On a partitioned PG install, log a warning and return a skip signal;
        callers should invoke the sync backend.cleanup_jobs() from a daemon or
        management command context instead.

        On SQLite / non-partitioned PG, mirrors the sync batched-DELETE path
        result format but is a no-op stub — the daemon always invokes the sync
        backend for cleanup. Override in production if fully-async cleanup is needed.
        """
        if self._partitioned_pg():
            # Partition reclaim is sync-only (psycopg cursor); defer to sync backend.
            logger.warning(
                "acleanup_jobs: Partition reclaim not yet available in async path — "
                "call sync backend.cleanup_jobs() from a daemon or management command."
            )
            return {"skipped": True, "reason": "partition_reclaim_sync_only"}
        # SQLite / non-partitioned PG: return stub — sync backend owns cleanup for now.
        return {"skipped": True, "reason": "use_sync_cleanup_jobs"}

    # ----- terminal-status writes -----------------------------------------

    async def amark_running(self, job_id, worker_id) -> None:
        now = timezone.now()
        # Old: await QueuedJob.objects.filter(pk=job_id).aupdate(...)
        # pk is now a composite (created_at, id); filter by id only.
        # Add created_at to filter for partition pruning (write-path item, async mirror).
        # Old (id-only — does not prune to partition on PG):
        # await QueuedJob.objects.filter(id=job_id).aupdate(status="running", started_at=now)
        job = await QueuedJob.objects.filter(id=job_id).values("created_at").afirst()
        if job:
            await QueuedJob.objects.filter(id=job_id, created_at=job["created_at"]).aupdate(
                status="running", started_at=now
            )

    @staticmethod
    def _finish_sync(job_id, outcome, attempt, values) -> None:
        """Close the attempt and write the terminal job row in one transaction.

        Django has no ``transaction.aatomic``, so the two statements run through
        ``sync_to_async`` (like ``aclaim_job``). A stale attempt raises ``JobFencingError``
        and the transaction rolls back: the job row is untouched. Without a handle every
        open attempt of the job is closed.
        """
        from django.db import transaction

        # Write-path pruning: fetch created_at first so PG prunes to one partition.
        row = QueuedJob.objects.filter(id=job_id).values("created_at").first()
        with transaction.atomic():
            if attempt is not None:
                JobAttempt.close(attempt, outcome)
            else:
                JobAttempt.close_open(
                    job_id, OUTCOME_SUCCESS if outcome == OUTCOME_SUCCESS else OUTCOME_ABANDONED
                )
            if row:
                QueuedJob.objects.filter(id=job_id, created_at=row["created_at"]).update(**values)

    async def amark_success(self, job_id, result, attempt=None) -> None:
        # REGRESSION 2026-08-08: this write path bypasses QueuedJob.mark_success
        # (raw .update()), so it needs its own guard against unawaited coroutines.
        reject_unawaited_coroutine(result)
        from asgiref.sync import sync_to_async

        # Old: native await QueuedJob.objects.filter(id=..., created_at=...).aupdate(...)
        # (no fencing); now fenced by the attempt, which needs a transaction.
        await sync_to_async(self._finish_sync)(
            job_id,
            OUTCOME_SUCCESS,
            attempt,
            {
                "status": "success",
                "finished_at": timezone.now(),
                "output": "" if result is None else str(result),
            },
        )

    async def amark_failed(
        self, job_id, error: str, traceback: str | None = None, attempt=None
    ) -> None:
        from asgiref.sync import sync_to_async

        await sync_to_async(self._finish_sync)(
            job_id,
            OUTCOME_FAILED,
            attempt,
            {
                "status": "failed",
                "finished_at": timezone.now(),
                "error": error or "",
                "traceback": traceback or "",
            },
        )

    async def amark_shutting_down(self, job_id) -> None:
        # Old: await QueuedJob.objects.filter(pk=job_id).aupdate(status="shutting_down")
        # Add created_at to filter for partition pruning (write-path item, async mirror).
        # Old (id-only — does not prune to partition on PG):
        # await QueuedJob.objects.filter(id=job_id).aupdate(status="shutting_down")
        job = await QueuedJob.objects.filter(id=job_id).values("created_at").afirst()
        if job:
            await QueuedJob.objects.filter(
                id=job_id, created_at=job["created_at"]
            ).aupdate(status="shutting_down")

    # ----- read paths ------------------------------------------------------

    async def aget_status(self, job_id) -> str | None:
        try:
            # Old: job = await QueuedJob.objects.only("status").aget(pk=job_id)
            # pk is now a composite (created_at, id); look up by id only.
            job = await QueuedJob.objects.only("status").aget(id=job_id)
        except QueuedJob.DoesNotExist:
            return None
        return job.status

    async def aget_job(self, job_id):
        try:
            # Old: return await QueuedJob.objects.aget(pk=job_id)
            return await QueuedJob.objects.aget(id=job_id)
        except QueuedJob.DoesNotExist:
            return None

    # ----- worker registry -------------------------------------------------

    async def aupdate_heartbeat(self, worker_id) -> None:
        now = timezone.now()  # capture BEFORE await
        await Worker.objects.filter(pk=worker_id).aupdate(last_heartbeat=now)

    async def aregister_worker(self, worker_id, metadata: dict) -> None:
        defaults: dict[str, Any] = {
            "node_id": metadata.get("node_id", ""),
            "pid": int(metadata.get("pid", 0)),
            "status": metadata.get("status", "idle"),
            "queues": metadata.get("queues", []),
        }
        await Worker.objects.aupdate_or_create(pk=worker_id, defaults=defaults)

    async def aunregister_worker(self, worker_id) -> None:
        await Worker.objects.filter(pk=worker_id).adelete()

    # ----- leases ----------------------------------------------------------

    async def aclaim_lease(
        self, queue_name: str, worker_id: str, ttl_seconds: int
    ) -> bool:
        """Claim a queue lease. Returns True on success, False if held by another.

        Single-statement attempts only (no aatomic available in 5.2):
          1. Try ``aupdate`` over expired-or-self leases.
          2. If no rows updated, try ``acreate``; IntegrityError ⇒ live lease.
        """
        from django.db import IntegrityError

        now = timezone.now()  # capture BEFORE await
        expires = now + timezone.timedelta(seconds=ttl_seconds)

        # Take over expired leases OR refresh our own (idempotent reclaim).
        from django.db.models import Q

        updated = await DaemonLease.objects.filter(
            Q(queue_name=queue_name)
            & (Q(expires_at__lt=now) | Q(daemon_id=worker_id))
        ).aupdate(
            daemon_id=worker_id,
            node_id=str(worker_id),
            pid=0,
            acquired_at=now,
            expires_at=expires,
        )
        if updated:
            return True

        try:
            await DaemonLease.objects.acreate(
                queue_name=queue_name,
                daemon_id=worker_id,
                node_id=str(worker_id),
                pid=0,
                acquired_at=now,
                expires_at=expires,
            )
            return True
        except IntegrityError:
            return False

    async def arenew_lease(self, queue_name: str, worker_id: str) -> bool:
        now = timezone.now()  # capture BEFORE await
        # Default to a 30-second renewal; callers re-renew on a tighter cycle.
        expires = now + timezone.timedelta(seconds=30)
        rows = await DaemonLease.objects.filter(
            queue_name=queue_name, daemon_id=worker_id
        ).aupdate(expires_at=expires)
        return rows > 0

    async def arelease_lease(self, queue_name: str, worker_id: str) -> None:
        await DaemonLease.objects.filter(
            queue_name=queue_name, daemon_id=worker_id
        ).adelete()

    # ----- scheduler -------------------------------------------------------

    async def aget_due_scheduled_tasks(self, now) -> list:
        # ``now`` is captured by the caller before any await. Iterate the
        # async queryset via ``async for`` to honor the native-async rule.
        results = []
        qs = (
            ScheduledTask.objects.filter(enabled=True, next_run_at__lte=now)
            .order_by("next_run_at")
        )
        async for task in qs:
            results.append(task)
        return results

    # ----- retry path --------------------------------------------------

    async def arequeue_retry(self, failed_job) -> None:
        """Insert a fresh ``queued`` row carrying the retry chain."""
        from sqlery.core.retry import retry_job_fields

        fields, delay = retry_job_fields(failed_job)
        scheduled_at = timezone.now() + timedelta(seconds=delay)

        await QueuedJob.objects.acreate(status="queued", scheduled_at=scheduled_at, **fields)

        # The failed attempt is superseded by the retry (as in the sync worker).
        row = await QueuedJob.objects.filter(id=failed_job.id, status="failed").values(
            "created_at"
        ).afirst()
        if row:
            await QueuedJob.objects.filter(
                id=failed_job.id, created_at=row["created_at"], status="failed"
            ).aupdate(status="archived")

    # ----- registry --------------------------------------------------------

    async def aregistry_add(self, registry_name: str, job_id) -> None:
        await JobRegistry.objects.acreate(
            job_id=job_id,
            registry_type=registry_name,
            metadata={},
        )

    async def aregistry_remove(self, registry_name: str, job_id) -> None:
        now = timezone.now()  # capture BEFORE await
        await JobRegistry.objects.filter(
            job_id=job_id,
            registry_type=registry_name,
            exited_at__isnull=True,
        ).aupdate(exited_at=now)
