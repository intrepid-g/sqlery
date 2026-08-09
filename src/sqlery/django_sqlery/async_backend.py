"""DjangoAsyncBackend — native-async Django implementation of AsyncDatabaseBackend (ASYN-02).

[ASSUMED — RESEARCH §A2 — transaction.aatomic availability]
This module was implemented against Django 5.2.14. As of that release,
`django.db.transaction.aatomic` is NOT available (`hasattr` check returned
False). Per plan 02-04 the project rule forbids thread-offload helpers, so
we adopt fallback option 1: drop to raw ``await connection.acursor()``
with explicit ``BEGIN``/``COMMIT``/``ROLLBACK`` statements around the
multi-statement claim sequence on PostgreSQL. SQLite uses a single-
statement ``aupdate()`` CAS on the ``version`` column — atomic by virtue
of being one statement — so no explicit transaction wrapper is required.

The project rule (plan 02-04) forbids wrapping sync ORM in a thread-
offload helper from asgiref; this implementation uses native async ORM
methods (``aget``/``acreate``/``aupdate``/``adelete``/``aupdate_or_create``
/``afirst``/``async for``) and raw ``acursor()`` exclusively.

[ASSUMED — version backfill]
The SQLite claim path relies on every QueuedJob row having a non-NULL
``version``. Tests apply a one-line idempotent backfill in
``tests/test_django_async_backend.py``. The model declares
``version = IntegerField(default=0)`` so newly-created rows are safe; only
legacy/migrated rows would require this fixup in production.

Concurrency contract:
- PostgreSQL: ``SELECT ... FOR UPDATE SKIP LOCKED`` via raw acursor.
- SQLite: optimistic ``version``-CAS via filter(pk, version).aupdate().
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import connection
from django.utils import timezone

from sqlery.compat import AsyncDatabaseBackend
from sqlery.core.utils import reject_unawaited_coroutine

from .models import (
    DaemonLease,
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

    async def aclaim_job(self, queues: list[str], worker_id: str):
        """Atomically claim the next queued job for one of ``queues``.

        Postgres branch uses raw ``acursor()`` with ``FOR UPDATE SKIP LOCKED``.
        SQLite branch uses optimistic locking on the ``version`` column.
        Returns the claimed ``QueuedJob`` instance with status='running', or
        ``None`` if nothing was available.
        """
        now = timezone.now()  # capture BEFORE await (RESEARCH §9 pitfall)

        if connection.vendor == "postgresql":
            return await self._aclaim_job_postgres(queues, worker_id, now)
        # SQLite (and any other vendor) → CAS retry loop.
        return await self._aclaim_job_sqlite(queues, worker_id, now)

    async def _aclaim_job_postgres(
        self, queues: list[str], worker_id: str, now
    ) -> QueuedJob | None:
        """Postgres claim path: raw SQL, SELECT FOR UPDATE SKIP LOCKED."""
        sql = (
            "SELECT id FROM sqlery_queued_job "
            "WHERE queue_name = ANY(%s) AND status = 'queued' "
            "  AND (scheduled_at IS NULL OR scheduled_at <= NOW()) "
            "ORDER BY priority DESC, created_at "
            "FOR UPDATE SKIP LOCKED LIMIT 1"
        )
        async with connection.acursor() as cur:
            # Explicit BEGIN/COMMIT (no transaction.aatomic in Django 5.2).
            await cur.execute("BEGIN")
            try:
                await cur.execute(sql, [list(queues)])
                row = await cur.fetchone()
                if row is None:
                    await cur.execute("COMMIT")
                    return None
                job_id = row[0]
                await cur.execute(
                    "UPDATE sqlery_queued_job "
                    "SET status='running', started_at=%s, version=version+1 "
                    "WHERE id=%s",
                    [now, job_id],
                )
                await cur.execute("COMMIT")
            except Exception:
                await cur.execute("ROLLBACK")
                raise
        # Old: return await QueuedJob.objects.aget(pk=job_id)
        # pk is now a composite (created_at, id); look up by id only.
        return await QueuedJob.objects.aget(id=job_id)

    async def _aclaim_job_sqlite(
        self, queues: list[str], worker_id: str, now
    ) -> QueuedJob | None:
        """SQLite claim path: optimistic version-CAS via single aupdate()."""
        # Find a candidate (filtered by queue/status/scheduled_at).
        qs = (
            QueuedJob.objects.filter(
                queue_name__in=list(queues),
                status="queued",
            )
            .filter(scheduled_at__isnull=True)
            .order_by("-priority", "created_at")
        )
        # Also include rows whose scheduled_at <= now.
        # NOTE: Django's `|` on querysets short-circuits via UNION at SQL
        # level; safer to do two filters and combine in Python via afirst().
        job = await qs.afirst()
        if job is None:
            qs2 = (
                QueuedJob.objects.filter(
                    queue_name__in=list(queues),
                    status="queued",
                    scheduled_at__lte=now,
                ).order_by("-priority", "created_at")
            )
            job = await qs2.afirst()
        if job is None:
            return None

        rowcount = await QueuedJob.objects.filter(
            # Old: pk=job.pk, version=job.version
            # pk is now a composite (created_at, id); filter by id for clarity.
            id=job.id, version=job.version
        ).aupdate(
            status="running",
            version=(job.version or 0) + 1,
            started_at=now,
        )
        if rowcount != 1:
            # Lost CAS — another claimer won this row. Return None; the
            # caller's poll loop will retry on the next tick.
            return None
        # Re-fetch to return current state.
        # Old: return await QueuedJob.objects.aget(pk=job.pk)
        return await QueuedJob.objects.aget(id=job.id)

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

    async def amark_success(self, job_id, result) -> None:
        # REGRESSION 2026-08-08: this write path bypasses QueuedJob.mark_success
        # (raw .aupdate()), so it needs its own guard against unawaited coroutines.
        reject_unawaited_coroutine(result)
        now = timezone.now()
        # Old: await QueuedJob.objects.filter(pk=job_id).aupdate(...)
        # Add created_at to filter for partition pruning (write-path item, async mirror).
        # Old (id-only — does not prune to partition on PG):
        # await QueuedJob.objects.filter(id=job_id).aupdate(
        #     status="success", finished_at=now, output=...
        # )
        job = await QueuedJob.objects.filter(id=job_id).values("created_at").afirst()
        if job:
            await QueuedJob.objects.filter(id=job_id, created_at=job["created_at"]).aupdate(
                status="success",
                finished_at=now,
                output="" if result is None else str(result),
            )

    async def amark_failed(
        self, job_id, error: str, traceback: str | None = None
    ) -> None:
        now = timezone.now()
        # Old: await QueuedJob.objects.filter(pk=job_id).aupdate(...)
        # Add created_at to filter for partition pruning (write-path item, async mirror).
        # Old (id-only — does not prune to partition on PG):
        # await QueuedJob.objects.filter(id=job_id).aupdate(
        #     status="failed", finished_at=now, error=error or "", traceback=traceback or ""
        # )
        job = await QueuedJob.objects.filter(id=job_id).values("created_at").afirst()
        if job:
            await QueuedJob.objects.filter(id=job_id, created_at=job["created_at"]).aupdate(
                status="failed",
                finished_at=now,
                error=error or "",
                traceback=traceback or "",
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
        retry_count = (getattr(failed_job, "retry_count", 0) or 0) + 1
        backoff = float(getattr(failed_job, "retry_backoff", 1.0) or 1.0)
        delay = backoff * (2 ** (retry_count - 1))
        scheduled_at = timezone.now() + timedelta(seconds=delay)

        await QueuedJob.objects.acreate(
            task_path=failed_job.task_path,
            kwargs=dict(failed_job.kwargs) if isinstance(failed_job.kwargs, dict) else {},
            queue_name=getattr(failed_job, "queue_name", "default"),
            priority=getattr(failed_job, "priority", 0) or 0,
            status="queued",
            parent_job_id=failed_job.id,
            retry_count=retry_count,
            max_retries=getattr(failed_job, "max_retries", 0) or 0,
            retry_backoff=backoff,
            scheduled_at=scheduled_at,
        )

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
