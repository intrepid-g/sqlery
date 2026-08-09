"""SQLAlchemyAsyncBackend — async SQLAlchemy implementation of AsyncDatabaseBackend (ASYN-03).

Companion to :class:`sqlery.django_sqlery.async_backend.DjangoAsyncBackend` for
the standalone/SQLModel integration mode. Uses SQLAlchemy 2.x ``AsyncSession``
exclusively. ``SQLModel.exec()`` is sync-only and is therefore NEVER called
from this module (see ASYN-03 plan, pitfall §9).

Concurrency contract:
- PostgreSQL (psycopg3-async): ``SELECT ... FOR UPDATE SKIP LOCKED`` via
  ``stmt.with_for_update(skip_locked=True)`` on the async session.
- SQLite (aiosqlite): optimistic ``version``-CAS via a single ``UPDATE ...
  WHERE id = :id AND version = :v`` statement. ``with_for_update`` is
  silently ignored by SQLite, so we rely on the CAS retry/no-op path.

Lease handling: standalone mode has no DaemonLease SQLModel today (Django mode
has one). To satisfy the AsyncDatabaseBackend ABC we declare a lightweight
``Lease`` SQLModel here. ``SQLModel.metadata`` picks it up via import and
``metadata.create_all`` (used by tests and ``init_database``) materialises the
table. Alembic migration will follow in a later plan.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlmodel import Field, SQLModel

from sqlery.compat import AsyncDatabaseBackend
from sqlery.core.models import JobRegistry, QueuedJob, ScheduledTask, Worker
from sqlery.core.utils import reject_unawaited_coroutine

from .database import get_async_session_factory

# Partition maintenance helpers — guarded against psycopg absence (SQLite installs).
# The async backend delegates _partitioned_pg() to a sync catalog query (acceptable:
# it is a cache-on-first-call check, not per-request).
try:
    from .database import get_engine as _get_sync_engine
    _sync_engine_available = True
except ImportError:
    _sync_engine_available = False

logger = logging.getLogger(__name__)


class Lease(SQLModel, table=True):
    """Queue lease row for daemon/worker coordination (standalone async path).

    Mirrors ``django_sqlery.models.DaemonLease`` semantically. A row is the
    sole owner of a queue while ``expires_at > now()``. Expired rows can be
    taken over by any daemon.
    """

    __tablename__ = "sqlery_lease"

    queue_name: str = Field(primary_key=True, max_length=100)
    daemon_id: str = Field(max_length=255)
    node_id: str = Field(default="", max_length=255)
    pid: int = Field(default=0)
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SQLAlchemyAsyncBackend(AsyncDatabaseBackend):
    """Async backend backed by SQLAlchemy 2.x AsyncSession (standalone mode)."""

    def __init__(self):
        """Initialize async backend."""
        self._partitioned_pg_cache: bool | None = None

    def _partitioned_pg(self) -> bool:
        """True iff running on PostgreSQL AND sqlery_queued_job is partitioned.

        Delegates to a sync catalog query via the sync engine (acceptable: this is
        a cache-on-first-call check, not per-request). Mirrors SQLAlchemyBackend._partitioned_pg.

        WR-01: On a transient DB error the cache is NOT written (left None) so the next
        call retries the catalog query.
        """
        if self._partitioned_pg_cache is not None:
            return self._partitioned_pg_cache
        if not _sync_engine_available:
            self._partitioned_pg_cache = False
            return False
        try:
            engine = _get_sync_engine()
        except RuntimeError:
            # Engine not initialized yet — fail safe, don't cache.
            logger.warning(
                "_partitioned_pg (async): sync engine not initialized — will retry on next call"
            )
            return False
        if engine.dialect.name != "postgresql":
            self._partitioned_pg_cache = False
            return False
        try:
            with engine.connect() as conn:
                # Old: %s + [list] — SQLAlchemy text() needs :named binds (Bug-SA-01).
                result = conn.execute(
                    text(
                        "SELECT relkind = 'p' FROM pg_class "
                        "WHERE relname = :name AND relnamespace = 'public'::regnamespace"
                    ),
                    {"name": QueuedJob.__tablename__},
                )
                row = result.fetchone()
            self._partitioned_pg_cache = bool(row and row[0])
        except Exception:
            # WR-01: do NOT permanently cache False on a transient DB error.
            logger.warning(
                "_partitioned_pg (async): catalog query failed — will retry on next call",
                exc_info=True,
            )
            return False
        return self._partitioned_pg_cache

    # ----- claim path ------------------------------------------------------

    async def aclaim_job(self, queues: list[str], worker_id: str):
        """Atomically claim the next queued job for one of ``queues``.

        Postgres branch uses ``with_for_update(skip_locked=True)``.
        SQLite branch falls back to optimistic ``version``-CAS.
        Returns the claimed ``QueuedJob`` (status='running') or ``None``.
        """
        factory = get_async_session_factory()
        now = _utcnow()  # captured BEFORE await
        async with factory() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            stmt = (
                select(QueuedJob)
                .where(QueuedJob.queue_name.in_(list(queues)))
                .where(QueuedJob.status == "queued")
                .order_by(QueuedJob.priority.desc(), QueuedJob.created_at)
                .limit(1)
            )
            if dialect == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
            result = await session.execute(stmt)
            job = result.scalars().first()
            if job is None:
                await session.commit()
                return None

            if dialect == "postgresql":
                # Row is locked by SELECT FOR UPDATE; update inline.
                job.status = "running"
                job.started_at = now
                job.version = (job.version or 0) + 1
                session.add(job)
                await session.commit()
                await session.refresh(job)
                return job

            # SQLite (and others): optimistic CAS on version.
            current_version = job.version or 0
            cas_stmt = (
                update(QueuedJob)
                .where(QueuedJob.id == job.id)
                .where(QueuedJob.version == current_version)
                .values(
                    status="running",
                    started_at=now,
                    version=current_version + 1,
                )
            )
            res = await session.execute(cas_stmt)
            await session.commit()
            if res.rowcount != 1:
                return None
            # Re-fetch to return current state.
            refreshed = await session.execute(
                select(QueuedJob).where(QueuedJob.id == job.id)
            )
            return refreshed.scalars().first()

    # ----- terminal-status writes -----------------------------------------

    async def amark_running(self, job_id, worker_id) -> None:
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            # Write-path pruning: fetch created_at first so PG prunes to one partition.
            # Guard: only add created_at filter when partitioned and value not None (falls
            # back to id-only on SQLite — D6).
            created_at = None
            if self._partitioned_pg():
                res = await session.execute(
                    select(QueuedJob.created_at).where(QueuedJob.id == job_id)
                )
                row = res.first()
                created_at = row[0] if row else None
            # Old (id-only filter — does not prune to partition on PG):
            # update(QueuedJob).where(QueuedJob.id == job_id).values(status="running", ...)
            stmt = update(QueuedJob).where(QueuedJob.id == job_id)
            if created_at is not None:
                stmt = stmt.where(QueuedJob.created_at == created_at)
            stmt = stmt.values(status="running", started_at=now)
            await session.execute(stmt)
            await session.commit()

    async def amark_success(self, job_id, result) -> None:
        # REGRESSION 2026-08-08: this write path bypasses QueuedJob.mark_success
        # (raw update() statement), so it needs its own guard against unawaited
        # coroutines.
        reject_unawaited_coroutine(result)
        now = _utcnow()
        output = "" if result is None else str(result)
        factory = get_async_session_factory()
        async with factory() as session:
            # Write-path pruning: fetch created_at first so PG prunes to one partition.
            created_at = None
            if self._partitioned_pg():
                res = await session.execute(
                    select(QueuedJob.created_at).where(QueuedJob.id == job_id)
                )
                row = res.first()
                created_at = row[0] if row else None
            # Old (id-only filter):
            # update(QueuedJob).where(QueuedJob.id == job_id).values(status="success", ...)
            stmt = update(QueuedJob).where(QueuedJob.id == job_id)
            if created_at is not None:
                stmt = stmt.where(QueuedJob.created_at == created_at)
            stmt = stmt.values(status="success", finished_at=now, output=output)
            await session.execute(stmt)
            await session.commit()

    async def amark_failed(
        self, job_id, error: str, traceback: str | None = None
    ) -> None:
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            # Write-path pruning: fetch created_at first so PG prunes to one partition.
            created_at = None
            if self._partitioned_pg():
                res = await session.execute(
                    select(QueuedJob.created_at).where(QueuedJob.id == job_id)
                )
                row = res.first()
                created_at = row[0] if row else None
            # Old (id-only filter):
            # update(QueuedJob).where(QueuedJob.id == job_id).values(status="failed", ...)
            stmt = update(QueuedJob).where(QueuedJob.id == job_id)
            if created_at is not None:
                stmt = stmt.where(QueuedJob.created_at == created_at)
            stmt = stmt.values(
                status="failed",
                finished_at=now,
                error=error or "",
                traceback=traceback or "",
            )
            await session.execute(stmt)
            await session.commit()

    async def amark_shutting_down(self, job_id) -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            # Write-path pruning: fetch created_at first so PG prunes to one partition.
            created_at = None
            if self._partitioned_pg():
                res = await session.execute(
                    select(QueuedJob.created_at).where(QueuedJob.id == job_id)
                )
                row = res.first()
                created_at = row[0] if row else None
            # Old (id-only filter):
            # update(QueuedJob).where(QueuedJob.id == job_id).values(status="shutting_down")
            stmt = update(QueuedJob).where(QueuedJob.id == job_id)
            if created_at is not None:
                stmt = stmt.where(QueuedJob.created_at == created_at)
            stmt = stmt.values(status="shutting_down")
            await session.execute(stmt)
            await session.commit()

    # ----- read paths ------------------------------------------------------

    async def aget_status(self, job_id) -> str | None:
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(
                select(QueuedJob.status).where(QueuedJob.id == job_id)
            )
            row = res.first()
            return row[0] if row is not None else None

    async def aget_job(self, job_id):
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(
                select(QueuedJob).where(QueuedJob.id == job_id)
            )
            return res.scalars().first()

    # ----- worker registry -------------------------------------------------

    async def aupdate_heartbeat(self, worker_id) -> None:
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            await session.execute(
                update(Worker).where(Worker.id == worker_id).values(last_heartbeat=now)
            )
            await session.commit()

    async def aregister_worker(self, worker_id, metadata: dict) -> None:
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            existing = await session.execute(
                select(Worker).where(Worker.id == worker_id)
            )
            row: Worker | None = existing.scalars().first()
            fields: dict[str, Any] = {
                "node_id": metadata.get("node_id", ""),
                "pid": int(metadata.get("pid", 0)),
                "status": metadata.get("status", "idle"),
                "queues": metadata.get("queues", []),
                "last_heartbeat": now,
            }
            if row is None:
                row = Worker(id=worker_id, **fields)
                session.add(row)
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
                session.add(row)
            await session.commit()

    async def aunregister_worker(self, worker_id) -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            await session.execute(delete(Worker).where(Worker.id == worker_id))
            await session.commit()

    # ----- leases ----------------------------------------------------------

    async def aclaim_lease(
        self, queue_name: str, worker_id: str, ttl_seconds: int
    ) -> bool:
        """Claim or take over a queue lease. Returns True if held by us after the call."""
        now = _utcnow()
        expires = now + timedelta(seconds=ttl_seconds)
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(
                select(Lease).where(Lease.queue_name == queue_name)
            )
            existing: Lease | None = res.scalars().first()
            if existing is None:
                session.add(
                    Lease(
                        queue_name=queue_name,
                        daemon_id=worker_id,
                        node_id=str(worker_id),
                        pid=0,
                        acquired_at=now,
                        expires_at=expires,
                    )
                )
                await session.commit()
                return True

            # Owned by us, or expired → take over.
            # SQLite strips tzinfo on round-trip; normalise both sides.
            exp = existing.expires_at
            if exp is not None and exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if existing.daemon_id == worker_id or exp < now:
                existing.daemon_id = worker_id
                existing.node_id = str(worker_id)
                existing.acquired_at = now
                existing.expires_at = expires
                session.add(existing)
                await session.commit()
                return True

            # Live lease owned by someone else.
            await session.commit()
            return False

    async def arenew_lease(self, queue_name: str, worker_id: str) -> bool:
        now = _utcnow()
        expires = now + timedelta(seconds=30)
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(
                update(Lease)
                .where(Lease.queue_name == queue_name)
                .where(Lease.daemon_id == worker_id)
                .values(expires_at=expires)
            )
            await session.commit()
            return res.rowcount > 0

    async def arelease_lease(self, queue_name: str, worker_id: str) -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            await session.execute(
                delete(Lease)
                .where(Lease.queue_name == queue_name)
                .where(Lease.daemon_id == worker_id)
            )
            await session.commit()

    # ----- scheduler -------------------------------------------------------

    async def aget_due_scheduled_tasks(self, now) -> list:
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(
                select(ScheduledTask)
                .where(ScheduledTask.enabled == True)  # noqa: E712 (SQLA)
                .where(ScheduledTask.next_run_at <= now)
                .order_by(ScheduledTask.next_run_at)
            )
            return list(res.scalars().all())

    # ----- registry --------------------------------------------------------

    async def aregistry_add(self, registry_name: str, job_id) -> None:
        factory = get_async_session_factory()
        async with factory() as session:
            entry = JobRegistry(
                job_id=job_id,
                registry_type=registry_name,
                extra_data={},
            )
            session.add(entry)
            await session.commit()

    async def aregistry_remove(self, registry_name: str, job_id) -> None:
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            await session.execute(
                update(JobRegistry)
                .where(JobRegistry.job_id == job_id)
                .where(JobRegistry.registry_type == registry_name)
                .where(JobRegistry.exited_at.is_(None))
                .values(exited_at=now)
            )
            await session.commit()
