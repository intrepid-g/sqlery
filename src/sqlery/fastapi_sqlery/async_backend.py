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
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlmodel import Field, SQLModel

from sqlery.compat import AsyncDatabaseBackend, JobFencingError
from sqlery.core.attempts import (
    OUTCOME_ABANDONED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    AttemptHandle,
    attach_attempt,
    decide_close,
)
from sqlery.core import history as history_core
from sqlery.core import ready as ready_core
from sqlery.core import ready_sql
from sqlery.core.models import (
    JobAll,
    JobBlocked,
    JobHistory,
    JobReady,
    JobRegistry,
    QueuedJob,
    ScheduledTask,
    Worker,
)
from sqlery.core.sqltypes import UTCDateTime
from sqlery.core.utils import reject_unawaited_coroutine

from . import attempt_sql, history_sql
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
    acquired_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(sa_type=UTCDateTime, default_factory=lambda: datetime.now(UTC))


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

    async def anext_due_delay(self, queues: list[str]) -> float | None:
        """Seconds until the earliest queued-but-not-yet-due job on ``queues``."""
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            # The earliest not-yet-due entry of the ready table (no scan of the job table).
            result = await session.execute(ready_sql.next_due_stmt(queues, now))
            nxt = result.scalar()
        if nxt is None:
            return None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        return (nxt - now).total_seconds()

    async def aready_tables_exist(self) -> bool:
        from sqlalchemy import inspect as sa_inspect

        factory = get_async_session_factory()
        async with factory() as session:
            conn = await session.connection()

            def _check(sync_conn):
                inspector = sa_inspect(sync_conn)
                return inspector.has_table(JobReady.__tablename__) and inspector.has_table(
                    JobBlocked.__tablename__
                )

            return await conn.run_sync(_check)

    async def ahistory_table_exists(self) -> bool:
        from sqlalchemy import inspect as sa_inspect

        factory = get_async_session_factory()
        async with factory() as session:
            conn = await session.connection()
            return await conn.run_sync(
                lambda sync_conn: sa_inspect(sync_conn).has_table(JobHistory.__tablename__)
            )

    async def aattempt_table_exists(self) -> bool:
        from sqlalchemy import inspect as sa_inspect

        from sqlery.core.models import JobAttempt

        factory = get_async_session_factory()
        async with factory() as session:
            conn = await session.connection()
            return await conn.run_sync(
                lambda sync_conn: sa_inspect(sync_conn).has_table(JobAttempt.__tablename__)
            )

    async def aclaim_job(self, queues: list[str], worker_id: str):
        """Atomically claim the next claimable job from the ready queue (#139).

        Postgres reads the entry with ``with_for_update(skip_locked=True)``; on both
        databases deleting the entry (rowcount 1) is the claim. Like the sync claim it moves
        on past stale entries; unlike it, it applies no dependency / tag checks (as before).
        Returns the claimed ``QueuedJob`` (status='running') or ``None``.
        """
        factory = get_async_session_factory()
        now = _utcnow()  # captured BEFORE await
        async with factory() as session:
            session.sync_session.expire_on_commit = False
            dialect = session.bind.dialect.name if session.bind is not None else ""
            owner_id = await self._registered_worker_id(session, worker_id)
            # The claiming PID is recorded on the attempt, not the job row (#138).
            owner = {"worker_id": owner_id} if owner_id else {}
            cursor = None
            while True:
                result = await session.execute(
                    ready_sql.next_entry_stmt(
                        list(queues), now, cursor, lock=dialect == "postgresql"
                    )
                )
                entry = result.scalars().first()
                if entry is None:
                    await session.commit()
                    return None
                slot, job_id, created_at = entry.slot, entry.job_id, entry.created_at
                cursor = (entry.priority, created_at, job_id)
                job = (await session.execute(ready_sql.job_of_entry_stmt(entry))).scalars().first()
                if job is None or job.status != "queued":
                    await session.execute(ready_sql.delete_entry_stmt(slot, job_id))
                    await session.commit()
                    continue
                deleted = await session.execute(ready_sql.delete_entry_stmt(slot, job_id))
                if deleted.rowcount != 1:
                    await session.commit()
                    continue
                res = await session.execute(
                    update(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == created_at)
                    .where(QueuedJob.status == "queued")
                    .values(
                        status="running",
                        started_at=now,
                        version=QueuedJob.version + 1,
                        **owner,
                    )
                    .execution_options(synchronize_session=False)
                )
                if res.rowcount != 1:
                    # The job changed under the entry we own; keep the entry if it is queued.
                    status = (
                        await session.execute(
                            select(QueuedJob.status)
                            .where(QueuedJob.id == job_id)
                            .where(QueuedJob.created_at == created_at)
                        )
                    ).scalar()
                    if status == "queued":
                        await session.execute(
                            ready_sql.insert_ready_stmt(dialect, [ready_sql.ready_values(job)])
                        )
                    await session.commit()
                    continue
                # The attempt joins the claim's transaction: both commit or neither does.
                handle = await self._aopen_attempt(session, job_id, owner_id)
                await session.commit()
                # The UPDATE above bypassed the identity map: load the row anew.
                refreshed = await session.execute(
                    select(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == created_at)
                    .execution_options(populate_existing=True)
                )
                return attach_attempt(refreshed.scalars().first(), handle)

    @staticmethod
    async def _aopen_attempt(session, job_id, worker_id) -> AttemptHandle:
        """Insert the attempt of a claim inside the claim's transaction (caller commits).

        Any attempt still open for the (queued) job is a leftover of a requeue that skipped
        closing: it is abandoned, keeping "one open attempt per job".
        """
        now = _utcnow()
        await session.execute(attempt_sql.abandon_open_stmt(job_id, now))
        row = attempt_sql.new_attempt(job_id, worker_id, now)
        session.add(row)
        await session.flush()
        return AttemptHandle(row.id, row.token, job_id)

    @staticmethod
    async def _aclose_attempt(session, job_id, attempt, outcome) -> None:
        """Close the attempt a completion belongs to, inside the completion's transaction.

        A stale handle raises ``JobFencingError`` (the caller rolls back, leaving the job row
        untouched). Without a handle every open attempt of the job is closed (see
        ``SQLAlchemyBackend._close_attempt``).
        """
        now = _utcnow()
        if attempt is not None:
            res = await session.execute(attempt_sql.close_exact_stmt(attempt, outcome, now))
            decide_close(res.rowcount, attempt)
            return
        await session.execute(attempt_sql.close_unfenced_stmt(job_id, outcome, now))

    @staticmethod
    async def _registered_worker_id(session, worker_id) -> uuid.UUID | None:
        """Return ``worker_id`` as a UUID if it names a registered Worker row, else ``None``.

        Recording the owner on the claimed job lets the zombie sweep tell a live worker's
        job from an orphan (dead worker / stale heartbeat) instead of by age alone.
        """
        try:
            wid = worker_id if isinstance(worker_id, uuid.UUID) else uuid.UUID(str(worker_id))
        except (ValueError, TypeError):
            return None
        res = await session.execute(select(Worker.id).where(Worker.id == wid))
        return wid if res.first() else None

    # ----- zombie sweep (async mirror of SQLAlchemyBackend liveness) -------

    async def aget_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        """Async twin of ``SQLAlchemyBackend.get_running_jobs_for_liveness``."""
        from sqlery.core.liveness import RunningJobLiveness
        from sqlery.django_sqlery.friendly_name import uuid_to_friendly

        def _aware(dt):
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

        factory = get_async_session_factory()
        async with factory() as session:
            stmt = select(QueuedJob).where(QueuedJob.status == "running")
            if queue_names:
                stmt = stmt.where(QueuedJob.queue_name.in_(list(queue_names)))
            jobs = list((await session.execute(stmt)).scalars().all())
            attempts = {}
            if jobs:
                rows = (
                    await session.execute(attempt_sql.open_attempts_stmt([j.id for j in jobs]))
                ).scalars()
                attempts = {r.job_id: r for r in rows}  # newest wins (ordered by id)
            worker_ids = {j.worker_id for j in jobs if j.worker_id is not None}
            workers: dict[Any, Worker] = {}
            if worker_ids:
                res = await session.execute(select(Worker).where(Worker.id.in_(worker_ids)))
                workers = {w.id: w for w in res.scalars().all()}

        records = []
        for job in jobs:
            worker = workers.get(job.worker_id)
            if worker is not None:
                try:
                    friendly = uuid_to_friendly(worker.id)
                except (ValueError, TypeError):
                    friendly = str(worker.id)
            else:
                friendly = None
            records.append(
                RunningJobLiveness(
                    job_id=job.id,
                    started_at=_aware(job.started_at),
                    # The open attempt; the row column only for jobs claimed before attempts.
                    worker_pid=(
                        attempts[job.id].worker_pid if job.id in attempts else job.worker_pid
                    ),
                    worker_node_id=worker.node_id if worker else None,
                    worker_status=worker.status if worker else None,
                    worker_current_job_id=worker.current_job_id if worker else None,
                    worker_last_heartbeat=_aware(worker.last_heartbeat) if worker else None,
                    worker_friendly_name=friendly,
                    has_worker=worker is not None,
                )
            )
        return records

    async def afail_zombie_job(self, job_id, reason: str) -> bool:
        """Async twin of ``SQLAlchemyBackend.fail_zombie_job`` (still-running jobs only)."""
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(select(QueuedJob).where(QueuedJob.id == job_id))
            job = res.scalars().first()
            if job is None or job.status != "running":
                return False
            # Abandon the open attempt in the same transaction as the failure.
            await self._aclose_attempt(session, job_id, None, OUTCOME_ABANDONED)
            # Detach before mutating: mark_failed only computes the outcome in memory; the
            # write is the move into history.
            session.expunge(job)
            job.mark_failed(error=reason, termination_reason="zombie_job")
            parent = job.parent_job_id
            moved = await self._afinish_in(
                session,
                job_id,
                "failed",
                {
                    "finished_at": job.finished_at,
                    "duration_seconds": job.duration_seconds,
                    "error": job.error,
                    "traceback": job.traceback,
                    "termination_reason": job.termination_reason,
                    "runs": job.runs,
                },
                created_at=job.created_at,
                require_status="running",
            )
            if not moved:
                await session.rollback()
                return False
            await session.commit()
        await self._arelease_dependents_quietly(job_id, parent)
        return True

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
            # Running jobs are not claimable: drop any entry (the claim path already did).
            await session.execute(ready_sql.delete_ready_stmt([job_id]))
            await session.execute(ready_sql.delete_blocked_stmt([job_id]))
            await session.commit()

    async def amark_success(self, job_id, result, attempt=None) -> None:
        # REGRESSION 2026-08-08: this write path bypasses QueuedJob.mark_success
        # (raw statements), so it needs its own guard against unawaited coroutines.
        reject_unawaited_coroutine(result)
        now = _utcnow()
        output = "" if result is None else str(result)
        factory = get_async_session_factory()
        async with factory() as session:
            await self._afinish(
                session, job_id, attempt, OUTCOME_SUCCESS, "success",
                {"finished_at": now, "output": output},
            )

    async def amark_failed(
        self, job_id, error: str, traceback: str | None = None, attempt=None
    ) -> None:
        now = _utcnow()
        factory = get_async_session_factory()
        async with factory() as session:
            # A failure the worker is about to retry is not final: the job stays in the live
            # table until ``arequeue_retry`` has queued the retry and archived it (a crash in
            # between leaves it to the settle sweep). A final failure moves to history now.
            row = (
                await session.execute(
                    select(QueuedJob.max_retries, QueuedJob.retry_count).where(
                        QueuedJob.id == job_id
                    )
                )
            ).first()
            will_retry = bool(row and (row[0] or 0) > 0 and (row[1] or 0) < (row[0] or 0))
            await self._afinish(
                session, job_id, attempt, OUTCOME_FAILED, "failed",
                {"finished_at": now, "error": error or "", "traceback": traceback or ""},
                in_place=will_retry,
            )

    async def _afinish_in(
        self, session, job_id, status, values=None, *, created_at=None,
        expected_version=None, require_status=None,
    ) -> bool:
        """The terminal transition inside the caller's transaction (async twin of
        ``SQLAlchemyBackend._finish_in``)."""
        lock, ins, dele = history_sql.move_statements(
            job_id, status, values, created_at=created_at,
            expected_version=expected_version, require_status=require_status,
        )
        if (await session.execute(lock)).first() is None:
            return False
        inserted = await session.execute(ins)
        deleted = await session.execute(dele)
        if not history_sql.moved(inserted, deleted):
            raise RuntimeError(f"moving job {job_id} to history touched an unexpected row count")
        return True

    async def _afinish(
        self, session, job_id, attempt, outcome, status, values, in_place: bool = False
    ) -> None:
        """Close the attempt and record the terminal state in one transaction: the job moves
        from the live table to history (or, ``in_place``, is only marked, awaiting its retry).

        A stale attempt raises ``JobFencingError`` and rolls back: the job row is untouched.
        """
        try:
            await self._aclose_attempt(session, job_id, attempt, outcome)
        except JobFencingError:
            await session.rollback()
            raise
        parent = None
        if in_place:
            await session.execute(
                update(QueuedJob)
                .where(QueuedJob.id == job_id)
                .values(status=status, version=QueuedJob.version + 1, **values)
                .execution_options(synchronize_session=False)
            )
        else:
            parent = (
                await session.execute(
                    select(QueuedJob.parent_job_id).where(QueuedJob.id == job_id)
                )
            ).scalar()
            if not await self._afinish_in(session, job_id, status, values):
                # Already finished (in history) or gone: there is nothing to write. A stale
                # completion of an attempt that is no longer open was rejected above.
                await session.rollback()
                return
        await session.commit()
        if outcome == OUTCOME_SUCCESS:
            await self._arelease_dependents_quietly(job_id, parent)
        # A failure is released by the worker once it has decided on the retry (releasing
        # here would fail dependents of a job whose retry is not queued yet).

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
            res = await session.execute(select(JobAll.status).where(JobAll.id == job_id))
            row = res.first()
            return row[0] if row is not None else None

    async def aget_job(self, job_id):
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(select(QueuedJob).where(QueuedJob.id == job_id))
            job = res.scalars().first()
            if job is not None:
                return job
            # A finished job is in history (read-only).
            res = await session.execute(select(JobHistory).where(JobHistory.id == job_id))
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

    # ----- ready queue: dependents (#139) --------------------------------------

    async def _arelease_dependents_quietly(self, job_id, parent_job_id=None) -> None:
        """Re-check the jobs blocked on a finished job; never raises (the periodic sweep run
        by the scheduler leader repairs whatever this misses)."""
        try:
            await self.arelease_dependents(job_id, parent_job_id)
        except Exception:
            logger.warning(f"arelease_dependents({job_id}) failed", exc_info=True)

    async def arelease_dependents(self, job_id, parent_job_id=None) -> int:
        """Async twin of ``SQLAlchemyBackend.release_dependents``."""
        factory = get_async_session_factory()
        released = 0
        work = [(job_id, parent_job_id)]
        seen: set = set()
        while work:
            current, parent = work.pop()
            if current in seen:
                continue
            seen.add(current)
            async with factory() as session:
                ids = [current]
                hops = 0
                if parent is None:
                    row = (
                        await session.execute(
                            select(JobAll.parent_job_id).where(JobAll.id == current)
                        )
                    ).first()
                    parent = row[0] if row else None
                while parent and parent not in ids and hops < ready_core.MAX_CHAIN_HOPS:
                    ids.append(parent)
                    row = (
                        await session.execute(
                            select(JobAll.parent_job_id).where(JobAll.id == parent)
                        )
                    ).first()
                    parent = row[0] if row else None
                    hops += 1
                dependents = (
                    await session.execute(
                        select(JobBlocked.job_id, JobBlocked.created_at)
                        .where(JobBlocked.dependency_id.in_(ids))
                        .distinct()
                    )
                ).all()
            for blocked_id, created_at in dependents:
                outcome = await self._arecheck_blocked(blocked_id, created_at)
                if outcome == "ready":
                    released += 1
                elif outcome == "failed":
                    work.append((blocked_id, None))
        return released

    async def _arecheck_blocked(self, job_id, created_at) -> str:
        """Re-judge one blocked job: ``ready``, ``failed``, ``blocked`` or ``gone``."""
        from sqlery.core.retry import is_retry_pending

        factory = get_async_session_factory()
        async with factory() as session:
            session.sync_session.expire_on_commit = False
            dialect = session.bind.dialect.name if session.bind is not None else ""
            job = (
                await session.execute(
                    select(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == created_at)
                )
            ).scalars().first()
            if job is None or job.status != "queued":
                await session.execute(ready_sql.delete_blocked_stmt([job_id]))
                await session.commit()
                return "gone"
            deps = ready_core.dependency_ids(job)
            rows = {
                d.id: d
                for d in (
                    await session.execute(select(JobAll).where(JobAll.id.in_(deps)))
                ).scalars()
            }
            # Pre-fetch the retry chains the judgement may follow (it takes plain callables).
            retries: dict = {}
            frontier = [d for d in rows.values() if d.status in ("failed", "archived")]
            hops = 0
            while frontier and hops < ready_core.MAX_CHAIN_HOPS:
                nxt = []
                for dep in frontier:
                    child = (
                        await session.execute(history_sql.latest_retry_stmt(dep.id))
                    ).scalars().first()
                    retries[dep.id] = child
                    if child is not None and child.status in ("failed", "archived"):
                        nxt.append(child)
                frontier = nxt
                hops += 1
            verdict = ready_core.judge_dependencies(
                deps, rows.get, retries.get, is_retry_pending
            )
            if verdict.state == ready_core.PENDING:
                await session.commit()
                return "blocked"
            if verdict.state == ready_core.FAILED:
                # Entries first, then the job row: the lock order of a claim.
                await session.execute(ready_sql.delete_ready_stmt([job_id]))
                await session.execute(ready_sql.delete_blocked_stmt([job_id]))
                await self._afinish_in(
                    session,
                    job_id,
                    "failed",
                    {
                        "error": f"Dependencies failed: {verdict.failed}",
                        "termination_reason": "dependency_failed",
                        "finished_at": _utcnow(),
                    },
                    created_at=created_at,
                    require_status="queued",
                )
                await session.commit()
                return "failed"
            await session.execute(ready_sql.delete_blocked_stmt([job_id]))
            await session.execute(
                ready_sql.insert_ready_stmt(dialect, [ready_sql.ready_values(job)])
            )
            await session.commit()
            return "ready"

    # ----- retry path --------------------------------------------------

    async def arequeue_retry(self, failed_job) -> None:
        """Insert a fresh ``queued`` row carrying the retry chain."""
        from sqlery.core.retry import retry_job_fields

        fields, delay = retry_job_fields(failed_job)
        scheduled_at = _utcnow() + timedelta(seconds=delay)

        factory = get_async_session_factory()
        async with factory() as session:
            session.add(QueuedJob(status="queued", scheduled_at=scheduled_at, **fields))
            # The failed attempt is superseded by the retry (as in the sync worker).
            res = await session.execute(
                select(QueuedJob.created_at, QueuedJob.termination_reason).where(
                    QueuedJob.id == failed_job.id, QueuedJob.status == "failed"
                )
            )
            row = res.first()
            if row is not None:
                # The superseded failure is final: it moves to history as ``archived``.
                await self._afinish_in(
                    session,
                    failed_job.id,
                    "archived",
                    {"termination_reason": history_core.settled_termination_reason(row[1])},
                    created_at=row[0],
                    require_status="failed",
                )
            await session.commit()

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
