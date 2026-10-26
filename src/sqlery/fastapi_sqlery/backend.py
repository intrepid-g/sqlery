"""SQLAlchemy backend implementation for sqlery standalone mode.

This backend uses SQLModel/SQLAlchemy for all database operations.
"""

import logging
import os
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone, UTC
from typing import Any

from sqlalchemy import and_, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, func, delete

from ..compat import DatabaseBackend, JobFencingError, get_config
from ..core.attempts import (
    OUTCOME_ABANDONED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    AttemptHandle,
    AttemptInfo,
    attach_attempt,
    decide_close,
)
from ..core import history as history_core
from ..core import ready as ready_core
from ..core import ready_sql
from ..core.models import (
    DaemonLease,
    JobAll,
    JobAttempt,
    JobBlocked,
    JobHistory,
    JobReady,
    JobRegistry,
    QueuedJob,
    ScheduledJob,
    ScheduledTask,
    Worker,
)
from ..crontab import next_cron_occurrence
from . import attempt_sql, history_sql
from .database import get_engine, get_session

# Partition maintenance helpers — guarded against psycopg absence (SQLite installs)
try:
    from sqlery.core import partitioning as _partitioning
except ImportError:
    _partitioning = None  # psycopg not installed; partition reclaim path unavailable

# Phase 18: pg_notify hook — guarded so SQLAlchemy backend works without core.pg_notify
try:
    from sqlery.core.pg_notify import notify_queue_sqlalchemy as _notify_queue_sqlalchemy
except ImportError:
    _notify_queue_sqlalchemy = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

CLEANUP_BATCH_SIZE = 500
FINISHED_STATUSES = ("success", "failed", "archived")


def determine_claim_strategy(dialect_name: str | None) -> str:
    """Decide which atomic claiming strategy to use for a database dialect.

    This is a swappable decision function (see bite2 principle #5).

    Args:
        dialect_name: SQLAlchemy dialect name (e.g., 'postgresql', 'sqlite').

    Returns:
        One of 'skip_locked', 'optimistic_version', or 'basic_lock'.
    """
    # I wish I had the time to: make this configurable via backend settings
    # so users can force a strategy (e.g., 'optimistic_version' even on Postgres)
    if dialect_name == "postgresql":
        return "skip_locked"
    if dialect_name == "sqlite":
        return "optimistic_version"
    # Fallback for other databases (mysql, oracle, etc.)
    return "basic_lock"


class SQLAlchemyBackend(DatabaseBackend):
    """SQLAlchemy implementation of DatabaseBackend.

    Uses SQLModel (which wraps SQLAlchemy) for database operations.
    """

    def __init__(self):
        """Initialize SQLAlchemy backend."""
        # from .database import get_session  # moved to top-level

        self._get_session = get_session
        self._partitioned_pg_cache: bool | None = None

    @staticmethod
    def _worker_pk(worker_id) -> uuid.UUID:
        try:
            return uuid.UUID(str(worker_id))
        except (ValueError, TypeError):
            return uuid.uuid5(uuid.NAMESPACE_URL, f"sqlery-worker:{worker_id}")

    def _partitioned_pg(self) -> bool:
        """True iff running on PostgreSQL AND sqlery_queued_job is partitioned.

        Used by the cleanup→reclaim routing, the far-future staging gate, and the
        write-path pruning logic. SQLite and a non-partitioned PG install both return
        False — they keep the Phase-12 batched DELETE path and the in-queue scheduling
        path unchanged (D6). Cached per-process: the table's partition status does not
        change at runtime (only via the stop-the-world cutover migration).

        WR-01: On a transient DB error the cache is NOT written (left None) so the next
        call retries the catalog query. Only a successful query writes the cache.
        """
        if self._partitioned_pg_cache is not None:
            return self._partitioned_pg_cache
        engine = get_engine()
        if engine.dialect.name != "postgresql":
            self._partitioned_pg_cache = False
            return False
        try:
            with engine.connect() as conn:
                # Old: text("... relname = %s ...") + [list] — SQLAlchemy 2.x text()
                # uses :named binds, NOT %s/positional; psycopg3 silently matched
                # nothing so _partitioned_pg always returned False (Bug-SA-01),
                # disabling ALL partition routing on PG.
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
        """Return a raw psycopg DBAPI cursor for the daemon's PG-only maintenance loop.

        reclaim_drained_partitions / ensure_future_partitions / check_default_partition
        need a live psycopg cursor (not a SQLAlchemy session). Returns a cursor from
        engine.raw_connection() on partitioned PostgreSQL; returns None on SQLite (and
        any non-partitioned install) so the daemon skips PG-only maintenance cleanly.

        WR-03 — POOL-CONNECTION LEAK WARNING: engine.raw_connection() checks out a
        POOLED connection. Calling only ``cursor.close()`` does NOT return that
        connection to the pool — it leaks for the lifetime of the process. The caller
        MUST also close the underlying connection via ``cur.connection.close()``.
        Prefer the ``raw_cursor()`` context manager below, which owns and releases both
        the cursor AND the connection automatically; only use this lower-level method
        if you cannot use the context manager, and always wrap it in try/finally that
        closes the cursor and then ``cur.connection.close()``.

        The archive hook receives (cur, partition_name) and must not execute
        arbitrary SQL via string interpolation (T-17-09).
        """
        if not self._partitioned_pg():
            return None
        engine = get_engine()
        raw_conn = engine.raw_connection()
        return raw_conn.cursor()

    @contextmanager
    def raw_cursor(self):
        """Context manager yielding a raw psycopg cursor and releasing the pool conn.

        WR-03: wraps get_raw_cursor()'s connection lifecycle so callers cannot leak the
        pooled connection. Yields a live psycopg cursor on partitioned PostgreSQL, or
        None on SQLite / any non-partitioned install (so the daemon skips PG-only
        maintenance cleanly). On exit it closes the cursor AND the underlying
        raw_connection, returning it to the pool — even if the body raises.
        """
        if not self._partitioned_pg():
            yield None
            return
        engine = get_engine()
        raw_conn = engine.raw_connection()
        cur = raw_conn.cursor()
        try:
            yield cur
            # The partition DDL (create / detach / drop) must survive the connection going
            # back to the pool, which rolls back whatever is uncommitted.
            raw_conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                raw_conn.close()
            except Exception:
                pass

    def _resolve_worker(self, worker_id):
        """Resolve a worker_id (UUID or "worker_<node>_<pid>" string) to a Worker.

        Mirrors DjangoBackend._resolve_worker so daemon code can pass either a
        UUID (from get_worker_heartbeats) or the "worker_<node>_<pid>" id format.
        """
        # UUID (object or string) — primary-key lookup.
        if isinstance(worker_id, uuid.UUID):
            with self._get_session() as session:
                return session.get(Worker, worker_id)
        try:
            worker_uuid = uuid.UUID(str(worker_id))
            with self._get_session() as session:
                return session.get(Worker, worker_uuid)
        except (ValueError, TypeError):
            pass

        # Parse "worker_<node>_<pid>" format.
        parts = str(worker_id).split("_")
        if parts[0] == "worker" and len(parts) >= 3:
            try:
                pid = int(parts[-1])
            except ValueError:
                pid = None
            if pid is not None:
                node_id = "_".join(parts[1:-1])
                with self._get_session() as session:
                    stmt = select(Worker).where(and_(Worker.node_id == node_id, Worker.pid == pid))
                    return session.exec(stmt).first()

        with self._get_session() as session:
            return session.get(Worker, self._worker_pk(worker_id))

    # def create_job(  # Original 9-param signature
    #     self,
    #     task_path: str,
    #     kwargs: dict,
    #     queue_name: str,
    #     priority: int,
    #     scheduled_at: datetime | None,
    #     max_retries: int,
    #     retry_backoff: float,
    #     allow_parallel: bool,
    #     timeout_seconds: int | None,
    #     parent_job_id: int | None = None,
    # ):
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
    ):
        """Create a new job in the database."""
        # Named job support: new job always wins, stop conflicting jobs
        if job_name:
            replaced: list[int] = []
            with self._get_session() as session:
                stmt = select(QueuedJob).where(QueuedJob.job_name == job_name)
                for conflicting in session.exec(stmt).all():
                    replaced.append(conflicting.id)
                    session.delete(conflicting)  # the session hook drops its ready entries
                session.commit()
            # Jobs that waited on a replaced job learn that it is gone (they fail, as before).
            for job_id in replaced:
                self._release_dependents_quietly(job_id)

        # Threshold routing (Phase 14/17): jobs scheduled further out than the configured
        # threshold go into sqlery_scheduled_job instead of sqlery_queued_job so they
        # cannot pin otherwise-drained partitions.
        # Staging only protects partitions, which exist only on partitioned PG.
        # On SQLite / non-partitioned PG, far-future jobs stay in sqlery_queued_job
        # (D6 — SQLite path unchanged).
        # CR-02: use the SQLERY_-prefixed key so the lookup hits StandaloneConfig's
        # stored value (and matches daemon.py's SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS read).
        # Old: threshold_days = get_config("SCHEDULED_JOB_THRESHOLD_DAYS", 1)
        threshold_days = get_config("SQLERY_SCHEDULED_JOB_THRESHOLD_DAYS", 1)
        staging_threshold = timedelta(days=threshold_days)
        now_utc = datetime.now(UTC)
        if (
            self._partitioned_pg()
            and scheduled_at is not None
            and scheduled_at > now_utc + staging_threshold
        ):
            # Store full job-creation spec in payload for lossless promotion (WR-01/WR-02).
            # payload schema: {"kwargs": <task kwargs>, "job_spec": {<all execution params>}}
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
                },
            }
            staging_row = ScheduledJob(
                queue_name=queue_name,
                task_path=task_path,
                payload=full_payload,
                scheduled_at=scheduled_at,
                priority=priority,
                max_retries=max_retries,
            )
            with self._get_session() as session:
                session.add(staging_row)
                session.commit()
                session.refresh(staging_row)
            return staging_row

        # Below threshold or immediate — insert into main queue.
        job = QueuedJob(
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
        )

        with self._get_session() as session:
            session.add(job)
            session.commit()
            session.refresh(job)

        # Phase 18 (D1) + CR-01: emit pg_notify on a separate AUTOCOMMIT
        # connection AFTER the session-with-block has committed and closed.
        # Old: notify was called inside the with-block through the session,
        #      which SA2 put in an implicit transaction that session.close()
        #      rolled back — NOTIFY was never dispatched (CR-01).
        # Old (inside the with-block):
        #     if (... and get_engine().dialect.name == "postgresql"):
        #         _notify_queue_sqlalchemy(queue_name, session)
        # No-op when SQLERY_PG_NOTIFY=False (default) or on SQLite. Any NOTIFY
        # failure is caught + logged inside notify_queue_sqlalchemy — never
        # crashes enqueue. Staging path (ScheduledJob) does NOT reach here.
        if (
            _notify_queue_sqlalchemy is not None
            and get_config("SQLERY_PG_NOTIFY", False)
            and get_engine().dialect.name == "postgresql"
        ):
            _notify_queue_sqlalchemy(queue_name, get_engine())

        return job

    def next_due_delay(self, queues: list[str]) -> float | None:
        now = datetime.now(UTC)
        with self._get_session() as session:
            # The earliest not-yet-due entry of the ready table (no scan of the job table).
            nxt = session.exec(ready_sql.next_due_stmt(queues, now)).scalar()
        if nxt is None:
            return None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        return (nxt - now).total_seconds()

    def next_scheduled_run_at(self, queue_names: list[str]) -> datetime | None:
        with self._get_session() as session:
            nxt = session.exec(
                select(func.min(ScheduledTask.next_run_at)).where(
                    ScheduledTask.enabled == True,  # noqa: E712
                    ScheduledTask.queue_name.in_(queue_names),
                )
            ).first()
        if nxt is not None and nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        return nxt

    # ----- execution attempts (#138) --------------------------------------

    # Claimer ids minted by the Lambda and HTTP-trigger paths: they have no Worker row.
    _EPHEMERAL_CLAIMERS = ("lambda-", "trigger-")

    def _attempt_worker_id(self, worker_id):
        """Worker row id recorded on an attempt; None for ephemeral claimers (mirrors Django)."""
        if worker_id is None or str(worker_id).startswith(self._EPHEMERAL_CLAIMERS):
            return None
        return self._worker_pk(worker_id)

    @staticmethod
    def _open_attempt(session, job_id, worker_id) -> AttemptHandle:
        """Insert the attempt of a claim inside the claim's transaction (caller commits).

        A job is claimed only while queued, so an attempt still open for it is a leftover of
        a requeue that skipped closing: it is abandoned here, which keeps "one open attempt
        per job" and fences the old holder.
        """
        now = datetime.now(UTC)
        session.exec(attempt_sql.abandon_open_stmt(job_id, now))
        row = attempt_sql.new_attempt(job_id, worker_id, now)
        session.add(row)
        session.flush()
        return AttemptHandle(row.id, row.token, job_id)

    @staticmethod
    def _close_attempt(session, job_id, attempt, outcome) -> None:
        """Close the attempt a completion belongs to, inside the completion's transaction.

        With a handle only that exact attempt may be closed (compare-and-set on id+token,
        still open); a stale completion raises ``JobFencingError`` and the caller rolls back
        without touching the job row. Without a handle (unfenced callers: recovery, a job
        that was never claimed) every open attempt of the job is closed; a failure there
        means the attempt was abandoned.
        """
        now = datetime.now(UTC)
        if attempt is not None:
            res = session.exec(attempt_sql.close_exact_stmt(attempt, outcome, now))
            decide_close(res.rowcount, attempt)
            return
        session.exec(attempt_sql.close_unfenced_stmt(job_id, outcome, now))

    def attempt_table_exists(self) -> bool:
        from sqlalchemy import inspect as sa_inspect

        return sa_inspect(get_engine()).has_table(JobAttempt.__tablename__)

    @staticmethod
    def _open_attempts_in(session, job_ids) -> dict:
        ids = list(job_ids)
        if not ids:
            return {}
        rows = session.exec(attempt_sql.open_attempts_stmt(ids)).scalars().all()
        # Newest wins if several are open (cannot happen through claim; defensive).
        return {
            r.job_id: AttemptInfo(
                r.id, r.job_id, r.worker_id, r.worker_pid, r.child_pid, r.claimed_at
            )
            for r in rows
        }

    def get_open_attempts(self, job_ids: list) -> dict:
        with self._get_session() as session:
            return self._open_attempts_in(session, job_ids)

    def abandon_attempts(self, job_id: int) -> int:
        with self._get_session() as session:
            res = session.exec(attempt_sql.abandon_open_stmt(job_id, datetime.now(UTC)))
            session.commit()
            return res.rowcount

    def claim_job(self, queues: list[str], worker_id: str):
        """Atomically claim the next claimable job from the ready queue (#139).

        One short transaction per candidate: read the next ready entry in claim order
        (``FOR UPDATE SKIP LOCKED`` on PostgreSQL), check eligibility, delete the entry
        (rowcount 1 is the claim; on SQLite it is the only mutex), set the job running and
        insert its attempt. The scan continues past candidates it cannot run, so a runnable
        job is never hidden behind blocked ones: a dependency-blocked candidate moves to the
        blocked set (and is not examined again until a dependency finishes); a tag-limited
        candidate stays in the ready table (the condition is transient). Tag locks are
        try-locks: a busy lock makes the candidate tag-limited instead of waiting.
        """
        blocked: list[tuple[int, datetime]] = []  # moved to the blocked set by this claim
        failed: list[int] = []  # failed by this claim (dependents are released afterwards)
        try:
            return self._claim_from_ready(queues, worker_id, blocked, failed)
        finally:
            # After the claim's transaction: a dependency may have finished while the job was
            # being moved to the blocked set (its release did not see the new rows yet).
            for job_id, created_at in blocked:
                self._recheck_blocked_quietly(job_id, created_at)
            for job_id in failed:
                self._release_dependents_quietly(job_id)

    def _claim_from_ready(self, queues, worker_id, blocked: list, failed: list):
        with self._get_session() as session:
            # Entries and jobs are read again after commits that delete them; do not expire.
            session.expire_on_commit = False
            now = datetime.now(UTC)
            dialect = session.bind.dialect.name if session.bind is not None else ""
            lock = determine_claim_strategy(dialect) == "skip_locked"
            attempt_worker = self._attempt_worker_id(worker_id)
            cursor = None
            while True:
                entry = session.exec(
                    ready_sql.next_entry_stmt(queues, now, cursor, lock=lock)
                ).scalars().first()
                if entry is None:
                    session.commit()
                    return None
                slot, job_id, created_at = entry.slot, entry.job_id, entry.created_at
                cursor = (entry.priority, created_at, job_id)

                job = session.exec(ready_sql.job_of_entry_stmt(entry)).scalars().first()
                if job is None:  # not found by (created_at, id): fall back to the id alone
                    job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
                if job is None or job.status != "queued":
                    # Stale entry (the job was cancelled, deleted or claimed elsewhere).
                    session.exec(ready_sql.delete_entry_stmt(slot, job_id))
                    session.commit()
                    continue

                decision = self._claim_decision(session, job, now)
                if decision in ("expired", "failed"):
                    # Marked failed by _claim_decision; its entries are dropped with it.
                    session.exec(ready_sql.delete_ready_stmt([job_id]))
                    session.commit()
                    failed.append(job_id)
                    continue
                if decision == "pending":
                    # Waiting on dependencies: out of the ready table, not examined again
                    # until a dependency reaches a terminal state.
                    insert = ready_sql.insert_blocked_stmt(dialect, job)
                    if (
                        insert is not None
                        and session.exec(ready_sql.delete_entry_stmt(slot, job_id)).rowcount == 1
                    ):
                        session.exec(insert)
                        blocked.append((job_id, created_at))
                    # (no usable dependency id to wait on: the job just stays ready)
                    session.commit()
                    continue
                if self._tag_limited(session, job, now):
                    session.commit()  # release the entry lock; the job stays ready
                    continue

                # Claim: deleting the entry is the mutex (a lost race deletes nothing).
                if session.exec(ready_sql.delete_entry_stmt(slot, job_id)).rowcount != 1:
                    session.commit()
                    continue
                res = session.exec(
                    update(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == job.created_at)
                    .where(QueuedJob.status == "queued")
                    .values(
                        status="running",
                        started_at=now,
                        # worker_pid is recorded on the attempt, not the job row (#138).
                        # #CLEANUP: drop QueuedJob.worker_pid/child_pid once no reader falls
                        # back to them.
                        version=QueuedJob.version + 1,
                    )
                    .execution_options(synchronize_session=False)
                )
                if res.rowcount != 1:
                    # The job changed under the entry we own: whoever changed it owns it
                    # now; if it is somehow still queued, put the entry back.
                    self._restore_entry_if_queued(session, dialect, job)
                    session.commit()
                    continue
                # The attempt joins the claim's transaction: both commit or neither does.
                handle = self._open_attempt(session, job_id, attempt_worker)
                session.commit()
                # The UPDATE above bypassed the identity map: load the row anew.
                claimed = session.exec(
                    select(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == job.created_at)
                    .execution_options(populate_existing=True)
                ).first()
                return attach_attempt(claimed, handle)

    @staticmethod
    def _restore_entry_if_queued(session, dialect: str, job) -> None:
        """Re-insert the ready entry of ``job`` if the job is (still) queued."""
        status = session.exec(
            select(QueuedJob.status)
            .where(QueuedJob.id == job.id)
            .where(QueuedJob.created_at == job.created_at)
        ).first()
        if status == "queued":
            ready_sql.write_ready(session, dialect, [job])

    def _claim_decision(self, session, job, now) -> str:
        """Decide a claim candidate: ``ok``, ``pending`` (wait for dependencies),
        ``expired`` (TTL) or ``failed`` (a dependency failed). The last two have already
        failed the job (its error says why)."""
        ttl = getattr(job, "ttl", None)
        created = getattr(job, "created_at", None)
        if ttl and created is not None:
            created_aware = created if created.tzinfo else created.replace(tzinfo=UTC)
            if (now - created_aware).total_seconds() > ttl:
                self._fail_queued(
                    session, job, f"Job expired after {ttl}s in queue (ttl)", "expired", now
                )
                return "expired"
        deps = ready_core.dependency_ids(job)
        if not deps:
            return "ok"
        verdict = self._judge_dependencies(session, deps)
        if verdict.state == ready_core.FAILED:
            self._fail_queued(
                session, job, f"Dependencies failed: {verdict.failed}", "dependency_failed", now
            )
            return "failed"
        return "pending" if verdict.state == ready_core.PENDING else "ok"

    @staticmethod
    def _fail_queued(session, job, error: str, reason: str, now) -> bool:
        """Fail a job that is still queued: it moves to history as ``failed`` (compare-and-set
        on its status).

        Its ready / blocked entries go with it, in the same transaction (entries first, then
        the job row: the lock order of a claim). If the job is no longer queued the move
        touches nothing and the transaction is left to the caller to commit or roll back.
        """
        session.exec(ready_sql.delete_ready_stmt([job.id]))
        session.exec(ready_sql.delete_blocked_stmt([job.id]))
        return SQLAlchemyBackend._finish_in(
            session,
            job.id,
            "failed",
            {"error": error, "termination_reason": reason, "finished_at": now},
            created_at=job.created_at,
            require_status="queued",
        )

    @staticmethod
    def _finish_in(
        session,
        job_id: int,
        status: str,
        values: dict | None = None,
        *,
        created_at=None,
        expected_version: int | None = None,
        require_status=None,
    ) -> bool:
        """The terminal transition: move one live job into history inside the caller's
        transaction (``INSERT .. SELECT`` + ``DELETE``, both must touch exactly one row).

        Returns False, with nothing changed by this call, when the job is not (or no longer)
        in the state the fences demand; the caller decides whether that is stale (rollback)
        or fine.
        """
        lock, ins, dele = history_sql.move_statements(
            job_id,
            status,
            values,
            created_at=created_at,
            expected_version=expected_version,
            require_status=require_status,
        )
        if session.exec(lock).first() is None:
            return False
        inserted = session.exec(ins)
        deleted = session.exec(dele)
        if not history_sql.moved(inserted, deleted):
            # Cannot happen after the row lock; never leave a half move behind.
            raise RuntimeError(f"moving job {job_id} to history touched an unexpected row count")
        return True

    @staticmethod
    def _judge_dependencies(session, deps: list):
        """Verdict on ``deps`` (see ``sqlery.core.ready.judge_dependencies``)."""
        from sqlery.core.retry import is_retry_pending

        # A dependency may be live or finished (history): read both through the union view.
        rows = {d.id: d for d in session.exec(history_sql.anywhere_in_stmt(deps)).all()}

        def latest_retry(job_id):
            return session.exec(history_sql.latest_retry_stmt(job_id)).first()

        return ready_core.judge_dependencies(deps, rows.get, latest_retry, is_retry_pending)

    def _tag_limited(self, session, job, now) -> bool:
        """True if claiming ``job`` now would exceed a tag concurrency/rate limit."""
        tags = getattr(job, "tags", None)
        if not tags:
            return False
        from sqlery.core.claiming import _config_limits
        from sqlery.core.utils import parse_rate_limit

        conc = _config_limits("TAG_CONCURRENCY_LIMITS")
        rate = _config_limits("TAG_RATE_LIMITS")
        limited = sorted(t for t in tags if t in conc or t in rate)
        if not limited:
            return False
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # Serialize limit check + claim per tag until this transaction ends.
            # Never wait on another worker's transaction: if the tag lock is busy, treat the
            # candidate as tag-limited (the caller skips it and releases our locks).
            for tag in limited:
                got = session.exec(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:t))").bindparams(t=tag)
                ).scalar()
                if not got:
                    return True
        for tag in limited:
            limit = conc.get(tag)
            if limit is not None and self._count_tag_in_session(
                session, tag, ["running"]
            ) >= limit:
                return True
            if rate.get(tag):
                try:
                    count, window = parse_rate_limit(rate[tag])
                except ValueError:
                    continue
                started = self._count_tag_in_session(
                    session, tag, ["running", "success", "failed"], since=now - window
                )
                if started >= count:
                    return True
        return False

    def request_job_revoke(self, job_id: int) -> bool:
        """Flag a running job for termination (see DatabaseBackend.request_job_revoke)."""
        with self._get_session() as session:
            res = session.exec(
                update(QueuedJob)
                .where(QueuedJob.id == job_id)
                .where(QueuedJob.status == "running")
                .values(termination_reason="revoke_requested")
            )
            session.commit()
            return res.rowcount > 0

    def is_job_revoke_requested(self, job_id: int) -> bool:
        with self._get_session() as session:
            return (
                session.exec(
                    select(QueuedJob.id)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.status == "running")
                    .where(QueuedJob.termination_reason == "revoke_requested")
                ).first()
                is not None
            )

    def get_jobs_pending_retry(self, older_than_seconds: float = 60, limit: int = 100) -> list:
        """Failed jobs whose retry was never created (see DatabaseBackend)."""
        from sqlery.core.retry import RETRY_PENDING

        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        with self._get_session() as session:
            return list(
                session.exec(
                    select(QueuedJob)
                    .where(QueuedJob.status == "failed")
                    .where(QueuedJob.termination_reason.like(f"{RETRY_PENDING}%"))
                    .where(QueuedJob.finished_at != None, QueuedJob.finished_at <= cutoff)  # noqa: E711
                    .order_by(QueuedJob.finished_at)
                    .limit(limit)
                ).all()
            )

    def settle_retry_pending(self, job_id: int) -> None:
        """No retry is owed any more: the failed job becomes final (moves to history as
        ``failed`` with the marker cleared). A job already in history is left alone."""
        with self._get_session() as session:
            row = session.exec(
                select(QueuedJob.created_at, QueuedJob.termination_reason).where(
                    QueuedJob.id == job_id, QueuedJob.status == "failed"
                )
            ).first()
            if row is not None:
                created_at, reason = row
                self._finish_in(
                    session,
                    job_id,
                    "failed",
                    {"termination_reason": history_core.settled_termination_reason(reason)},
                    created_at=created_at,
                    require_status="failed",
                )
                session.commit()

    def get_latest_retry(self, job_id: int):
        """Newest retry attempt (child via parent_job_id) of ``job_id``, live or finished,
        or None."""
        with self._get_session() as session:
            return session.exec(history_sql.latest_retry_stmt(job_id)).first()

    def claim_job_by_id(self, job_id, worker_id: str):
        """Atomically claim one specific queued job (Lambda / HTTP-trigger paths).

        Locks go ready entry first, then job row (the order every claim uses). An entry that
        another claimer holds means the job is being claimed: return None instead of waiting.
        """
        with self._get_session() as session:
            session.expire_on_commit = False
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "postgresql":
                held = session.exec(
                    select(JobReady)
                    .where(JobReady.slot.in_(ready_core.SLOTS))
                    .where(JobReady.job_id == job_id)
                    .with_for_update(skip_locked=True)
                ).all()
                if not held and session.exec(
                    select(JobReady.job_id)
                    .where(JobReady.slot.in_(ready_core.SLOTS))
                    .where(JobReady.job_id == job_id)
                ).first() is not None:
                    session.commit()
                    return None  # the entry exists but is locked: someone else is claiming it
            job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            if job is None or job.status != "queued":
                session.commit()
                return None
            current_version = job.version or 0
            created_at = job.created_at
            session.exec(ready_sql.delete_ready_stmt([job_id]))
            session.exec(ready_sql.delete_blocked_stmt([job_id]))
            res = session.exec(
                update(QueuedJob)
                .where(QueuedJob.id == job_id)
                .where(QueuedJob.created_at == created_at)
                .where(QueuedJob.version == current_version)
                .where(QueuedJob.status == "queued")
                .values(
                    status="running",
                    started_at=datetime.now(UTC),
                    version=current_version + 1,
                )
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                session.rollback()  # keeps the entries: the job was not claimed by us
                return None
            handle = self._open_attempt(session, job_id, self._attempt_worker_id(worker_id))
            session.commit()
            claimed = session.exec(
                select(QueuedJob)
                .where(QueuedJob.id == job_id)
                .where(QueuedJob.created_at == created_at)
                .execution_options(populate_existing=True)
            ).first()
            return attach_attempt(claimed, handle)

    def claim_queue_leases(
        self,
        queues: list[str],
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
    ) -> list[str]:
        """Claim scheduler leases for the given queues.

        Atomically claims one lease per queue, returning the subset successfully
        claimed. Expired leases are taken over; live leases held by other daemons
        are skipped. PostgreSQL uses a blocking ``SELECT FOR UPDATE`` row lock on
        the single-key lease row (see CR-01); SQLite uses an optimistic
        version-based CAS update.

        Parity note (WR-01): re-claiming a lease THIS daemon already holds (still
        live) returns ``True`` here — the standalone contract treats own-live
        re-claim as an idempotent refresh. The Django backend returns ``False``
        in that case (its conditional UPDATE only matches ``expires_at < now``,
        so a live own-lease falls through to an INSERT that conflicts on the PK).
        This divergence is intentional and currently latent: the daemon caller
        (``core/daemon.py``) only ever claims ``queues - owned_queues``, so it
        never re-claims a queue it already owns. Callers depending on Django
        semantics for own-live re-claim must not assume parity on the return
        value.

        Args:
            queues: Queue names to attempt to claim.
            daemon_id: Unique daemon identifier.
            node_id: Node/host identifier.
            pid: Daemon process ID.
            lease_secs: Lease duration in seconds.

        Returns:
            The subset of ``queues`` successfully claimed.
        """
        with self._get_session() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            strategy = determine_claim_strategy(dialect)

            claimed: list[str] = []
            for queue_name in queues:
                if self._claim_one_lease(
                    session, queue_name, daemon_id, node_id, pid, lease_secs, strategy
                ):
                    claimed.append(queue_name)
            return claimed

    def _claim_one_lease(
        self,
        session: Session,
        queue_name: str,
        daemon_id: str,
        node_id: str,
        pid: int,
        lease_secs: int,
        strategy: str,
    ) -> bool:
        """Atomically claim a single queue lease within an open session.

        Args:
            session: Active SQLAlchemy session to operate within.
            queue_name: Queue whose lease to claim.
            daemon_id: Unique daemon identifier.
            node_id: Node/host identifier.
            pid: Daemon process ID.
            lease_secs: Lease duration in seconds.
            strategy: Claim strategy from ``determine_claim_strategy``.

        Returns:
            True if the lease was claimed (insert or take-over), else False.
        """
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=lease_secs)

        if strategy == "skip_locked":
            # Old (CR-01): SKIP LOCKED on a single-keyed lease row makes a live,
            # locked lease read back as None, routing into the INSERT branch and
            # leaving the take-over path (below) unreachable under contention.
            # stmt = (
            #     select(DaemonLease)
            #     .where(DaemonLease.queue_name == queue_name)
            #     .with_for_update(skip_locked=True)
            # )
            # New (CR-01): use a blocking row lock (no skip_locked) so a
            # concurrent claimant waits on the lock and then observes the real
            # row, mirroring the Django reference's contention semantics. There
            # is no "different row" to fall through to for a single-key lease,
            # so SKIP LOCKED is the wrong primitive here.
            stmt = select(DaemonLease).where(DaemonLease.queue_name == queue_name).with_for_update()
            existing = session.exec(stmt).first()
            if existing is None:
                lease = DaemonLease(
                    queue_name=queue_name,
                    daemon_id=daemon_id,
                    node_id=node_id,
                    pid=pid,
                    acquired_at=now,
                    expires_at=expires,
                    version=0,
                )
                session.add(lease)
                try:
                    session.commit()
                except IntegrityError:
                    # Another claimer inserted the row concurrently — lost the race.
                    session.rollback()
                    return False
                return True

            # Take over an expired lease, or refresh our own (idempotent re-claim).
            # SQLite returns naive datetimes; normalize to UTC-aware before compare.
            existing_expires = (
                existing.expires_at
                if existing.expires_at.tzinfo
                else existing.expires_at.replace(tzinfo=UTC)
            )
            # WR-03: take-over is guarded by the same predicate as the version-CAS
            # branch below (expired OR our own lease). With the blocking row lock
            # from CR-01, concurrent claimants are serialized, so this read-then-
            # write of the locked row is now contention-safe.
            if existing_expires < now or existing.daemon_id == daemon_id:
                existing.daemon_id = daemon_id
                existing.node_id = node_id
                existing.pid = pid
                existing.acquired_at = now
                existing.expires_at = expires
                existing.version = (existing.version or 0) + 1
                session.add(existing)
                session.commit()
                return True

            # Live lease held by another daemon — do not steal.
            return False

        # optimistic_version (SQLite) / basic_lock fallback: version-CAS take-over.
        existing = session.exec(
            select(DaemonLease).where(DaemonLease.queue_name == queue_name)
        ).first()
        if existing is None:
            lease = DaemonLease(
                queue_name=queue_name,
                daemon_id=daemon_id,
                node_id=node_id,
                pid=pid,
                acquired_at=now,
                expires_at=expires,
                version=0,
            )
            session.add(lease)
            try:
                session.commit()
            except IntegrityError:
                # Another claimer won the insert race.
                session.rollback()
                return False
            return True

        current_version = existing.version or 0
        # SQLite returns naive datetimes; normalize to UTC-aware before compare.
        existing_expires = (
            existing.expires_at
            if existing.expires_at.tzinfo
            else existing.expires_at.replace(tzinfo=UTC)
        )

        # Idempotent re-claim of a lease we already hold (still live or not).
        if existing.daemon_id == daemon_id:
            cas_stmt = (
                update(DaemonLease)
                .where(DaemonLease.queue_name == queue_name)
                .where(DaemonLease.version == current_version)
                .values(
                    daemon_id=daemon_id,
                    node_id=node_id,
                    pid=pid,
                    acquired_at=now,
                    expires_at=expires,
                    version=current_version + 1,
                )
                # Match the expired-takeover CAS: skip the ORM synchronize
                # evaluator so both lease CAS statements share one consistent
                # rule and stay future-proof against any datetime/JSON predicate
                # later added to this UPDATE (WR-01).
                .execution_options(synchronize_session=False)
            )
            res = session.exec(cas_stmt)
            session.commit()
            return res.rowcount == 1

        # Take over an expired lease via version-CAS (guards against a concurrent
        # claimer that mutated the row between the read and the update).
        if existing_expires < now:
            cas_stmt = (
                update(DaemonLease)
                .where(DaemonLease.queue_name == queue_name)
                .where(DaemonLease.version == current_version)
                .where(DaemonLease.expires_at < now)
                .values(
                    daemon_id=daemon_id,
                    node_id=node_id,
                    pid=pid,
                    acquired_at=now,
                    expires_at=expires,
                    version=current_version + 1,
                )
                # Emit raw SQL: skip the ORM evaluator, which cannot compare the
                # SQLite naive `expires_at` column against the aware `now`.
                .execution_options(synchronize_session=False)
            )
            res = session.exec(cas_stmt)
            session.commit()
            return res.rowcount == 1

        # Live lease held by another daemon — do not steal.
        return False

    def renew_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
        lease_secs: int,
    ) -> None:
        """Extend expires_at for all owned leases by lease_secs from now.

        Only rows whose ``queue_name`` is in ``owned_queues`` AND whose
        ``daemon_id`` matches are touched; leases owned by other daemons are
        left intact.

        Args:
            owned_queues: Owned queue names to renew.
            daemon_id: Daemon identifier that owns the leases.
            lease_secs: New lease duration from now, in seconds.
        """
        # WR-02: defend against an empty list — `in_([])` emits a SQLAlchemy
        # warning on some versions and behaves inconsistently across dialects.
        if not owned_queues:
            return
        with self._get_session() as session:
            stmt = (
                update(DaemonLease)
                .where(DaemonLease.queue_name.in_(owned_queues))
                .where(DaemonLease.daemon_id == daemon_id)
                .values(expires_at=datetime.now(UTC) + timedelta(seconds=lease_secs))
            )
            session.exec(stmt)
            session.commit()

    def release_queue_leases(
        self,
        owned_queues: list[str],
        daemon_id: str,
    ) -> None:
        """Delete lease rows for all owned queues on clean shutdown.

        Only rows whose ``queue_name`` is in ``owned_queues`` AND whose
        ``daemon_id`` matches are deleted; leases owned by other daemons survive.

        Args:
            owned_queues: Owned queue names to release.
            daemon_id: Daemon identifier that owns the leases.
        """
        # WR-02: defend against an empty list — `in_([])` emits a SQLAlchemy
        # warning on some versions and behaves inconsistently across dialects.
        if not owned_queues:
            return
        with self._get_session() as session:
            stmt = (
                delete(DaemonLease)
                .where(DaemonLease.queue_name.in_(owned_queues))
                .where(DaemonLease.daemon_id == daemon_id)
            )
            session.exec(stmt)
            session.commit()

    def get_queue_stats(self, queue_name: str | None = None) -> dict:
        """Get queue statistics (counts by status)."""
        with self._get_session() as session:
            stmt = select(JobAll.status, func.count(JobAll.id).label("count"))

            if queue_name:
                stmt = stmt.where(JobAll.queue_name == queue_name)

            stmt = stmt.group_by(JobAll.status)
            results = session.exec(stmt).all()

            stats = {
                "queued": 0,
                "running": 0,
                "success": 0,
                "failed": 0,
            }

            for status, count in results:
                stats[status] = count

            if queue_name:
                stats["queue_name"] = queue_name

            return stats

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a queued or staged job, spanning QueuedJob and ScheduledJob tables.

        Write-path pruning: fetches created_at first so PG can prune to one partition.
        On SQLite the id-only fallback is used (D6).
        """
        with self._get_session() as session:
            # Fetch created_at for partition pruning (write-path pruning, mirrors DjangoBackend)
            # Old (id-only filter — does not prune to partition on PG):
            # job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            # SQLModel exec() returns scalars for single-column selects
            created_at_val = session.exec(
                select(QueuedJob.created_at).where(
                    and_(QueuedJob.id == job_id, QueuedJob.status == "queued")
                )
            ).first()
            if created_at_val is not None:
                # Unclaimable: drop its entries in the same transaction as the cancel. Entries
                # first, then the job row: the lock order of a claim (a claimer that holds the
                # entry finishes first; the cancel then finds the job running).
                session.exec(ready_sql.delete_ready_stmt([job_id]))
                session.exec(ready_sql.delete_blocked_stmt([job_id]))
                # The cancelled job is final: it moves to history as failed.
                cancelled = self._finish_in(
                    session,
                    job_id,
                    "failed",
                    {"error": "Cancelled by user"},
                    created_at=created_at_val,
                    require_status="queued",
                )
                if cancelled:
                    session.commit()
                else:
                    session.rollback()  # not cancelled: keep the entries
                if cancelled:
                    # Jobs that waited on it fail now (they would at their next claim).
                    self._release_dependents_quietly(job_id)
                    return True

            # Old: return False here — staged jobs uncancellable.
            # Fall through to ScheduledJob staging table on partitioned PG.
            if self._partitioned_pg():
                staged = session.exec(
                    select(ScheduledJob).where(ScheduledJob.id == job_id)
                ).first()
                if staged:
                    session.delete(staged)
                    session.commit()
                    return True

            return False

    def retry_failed_jobs(self, queue_name: str | None = None, max_jobs: int | None = None) -> int:
        """Retry failed jobs: each failed job that has not been retried gets a fresh queued
        copy (``retry_count`` 0, ``parent_job_id`` = the failed job). The failed job itself
        is in history and stays as it was (history is insert-only); having a retry child, it
        is not retried a second time."""
        with self._get_session() as session:
            has_child = (
                select(JobAll.id).where(JobAll.parent_job_id == JobHistory.id).exists()
            )
            stmt = (
                select(JobHistory)
                .where(JobHistory.status == "failed")
                .where(~has_child)
                .order_by(JobHistory.finished_at, JobHistory.id)
            )
            if queue_name:
                stmt = stmt.where(JobHistory.queue_name == queue_name)
            if max_jobs:
                stmt = stmt.limit(max_jobs)
            failed = session.exec(stmt).all()
            for old in failed:
                session.add(
                    QueuedJob(
                        task_path=old.task_path,
                        kwargs=dict(old.kwargs or {}),
                        queue_name=old.queue_name,
                        priority=old.priority,
                        max_retries=old.max_retries,
                        retry_backoff=old.retry_backoff,
                        allow_parallel=old.allow_parallel,
                        timeout_seconds=old.timeout_seconds,
                        retry_count=0,
                        scheduled_task_id=old.scheduled_task_id,
                        job_name=old.job_name,
                        retry_intervals=old.retry_intervals,
                        meta=old.meta,
                        dependencies=list(old.dependencies or []),
                        tags=list(old.tags or []),
                        on_success_path=old.on_success_path,
                        on_failure_path=old.on_failure_path,
                        ttl=old.ttl,
                        result_ttl=old.result_ttl,
                        failure_ttl=old.failure_ttl,
                        parent_job_id=old.id,
                        status="queued",
                    )
                )
            session.commit()
            return len(failed)

    def get_due_scheduled_tasks(self):
        """Get scheduled tasks that are due to run."""
        with self._get_session() as session:
            stmt = (
                select(ScheduledTask)
                .where(
                    and_(
                        ScheduledTask.enabled == True,
                        ScheduledTask.next_run_at <= datetime.now(UTC),
                    )
                )
                .order_by(ScheduledTask.next_run_at)
            )

            return list(session.exec(stmt).all())

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
        """Create a new scheduled task.

        ``next_run_at`` defaults to the next cron occurrence so the task becomes due.
        """
        task = ScheduledTask(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
            next_run_at=next_run_at or next_cron_occurrence(cron_expression, datetime.now(UTC)),
        )

        with self._get_session() as session:
            session.add(task)
            session.commit()
            session.refresh(task)

        return task

    def get_worker_heartbeats(self, active_only: bool = True):
        """Get worker heartbeats."""
        with self._get_session() as session:
            stmt = select(Worker)

            if active_only:
                # Old: hardcoded 60s — a fourth, disagreeing definition of "alive".
                threshold = datetime.now(UTC) - timedelta(
                    seconds=get_config("WORKER_ALIVE_TIMEOUT", 30)
                )
                stmt = stmt.where(
                    Worker.last_heartbeat >= threshold, Worker.status != "dead"
                )

            stmt = stmt.order_by(Worker.last_heartbeat.desc())

            return list(session.exec(stmt).all())

    def update_worker_heartbeat(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
        jobs_processed: int | None = None,
        total_busy_seconds: float | None = None,
    ):
        """Update or create worker heartbeat.

        ``total_busy_seconds`` is accepted for parity with the Django backend (the worker
        always sends it); the standalone worker table has no column for it, so it is ignored.
        """
        # import socket  # moved to top-level

        with self._get_session() as session:
            worker = session.get(Worker, self._worker_pk(worker_id))

            if worker:
                worker.status = status
                worker.current_job_id = current_job_id
                worker.last_heartbeat = datetime.now(UTC)
                if jobs_processed is not None:
                    worker.jobs_processed = jobs_processed
            else:
                worker = Worker(
                    id=self._worker_pk(worker_id),
                    node_id=socket.gethostname(),
                    pid=os.getpid(),
                    status=status,
                    current_job_id=current_job_id,
                    last_heartbeat=datetime.now(UTC),
                    # WR-06: carry jobs_processed through on the create branch so
                    # a first heartbeat with a non-zero count is not silently
                    # dropped (the update branch already handles this).
                    jobs_processed=jobs_processed if jobs_processed is not None else 0,
                )

            session.add(worker)
            session.commit()

    def refresh_worker_heartbeat(self, worker_id):
        """Update ONLY last_heartbeat for a worker. Does not touch status or current_job.

        Used by the daemon to keep workers alive without interfering with
        the worker's own state management (status, current_job). Mirrors
        DjangoBackend.refresh_worker_heartbeat semantics.
        """
        worker = self._resolve_worker(worker_id)
        if worker is None:
            return
        with self._get_session() as session:
            row = session.get(Worker, worker.id)
            if row is not None:
                row.last_heartbeat = datetime.now(UTC)
                session.add(row)
                session.commit()

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
        whole partitions past ``SQLERY_PARTITION_RETENTION`` are dropped (the attempt table
        is partitioned too and drops with them; see ``sqlery.core.history``). Elsewhere
        history is deleted in keyset batches of ``CLEANUP_BATCH_SIZE`` rows, and the finished
        attempts of the deleted jobs go with each batch.
        """
        # --- Partition reclaim path (PostgreSQL + partitioned history) ---
        if self._partitioned_pg() and _partitioning is not None:
            if dry_run:
                # dry_run is not meaningful for partition-drop path; return estimate
                with self._get_session() as session:
                    count_stmt = select(func.count(JobHistory.id))
                    if status:
                        count_stmt = count_stmt.where(JobHistory.status == status)
                    if queue_name:
                        count_stmt = count_stmt.where(JobHistory.queue_name == queue_name)
                    count = session.exec(count_stmt).one()
                return {"deleted": 0, "count": count}

            retention_str = get_config("SQLERY_PARTITION_RETENTION", "30 days")
            archive_hook = get_config("SQLERY_PARTITION_ARCHIVE_HOOK", None)
            with self.raw_cursor() as cur:
                # Partition reclaim destroys all finished jobs in drained partitions (beyond
                # SQLERY_PARTITION_RETENTION) by default. Set SQLERY_PARTITION_ARCHIVE_HOOK
                # to archive instead. The archive hook receives (cur, partition_name) and
                # must not execute arbitrary SQL via string interpolation.
                dropped_by_table = history_core.reclaim_partitions(
                    cur, retention_str, archive_hook, _partitioning
                )
            dropped = sum(dropped_by_table.values())
            if not self._attempts_partitioned():
                self._prune_attempts_without_history()
            return {
                "deleted": 0,
                "reclaimed_via_partition_drop": True,
                "dropped_partitions": dropped,
                "dropped_by_table": dropped_by_table,
                "note": (
                    "Partition reclaim: jobs beyond retention destroyed by default. "
                    "Set SQLERY_PARTITION_ARCHIVE_HOOK to archive instead."
                ),
            }

        # --- SQLite or non-partitioned PG: batched DELETE of history ---
        cutoff = None
        if max_age_days:
            cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

        def _filters(stmt):
            if status:
                stmt = stmt.where(JobHistory.status == status)
            if queue_name:
                stmt = stmt.where(JobHistory.queue_name == queue_name)
            if cutoff is not None:
                stmt = stmt.where(JobHistory.finished_at < cutoff)
            return stmt

        with self._get_session() as session:
            if dry_run:
                count = session.exec(_filters(select(func.count(JobHistory.id)))).one()
                return {"deleted": 0, "count": count}

            # Keyset-batched loop: at most CLEANUP_BATCH_SIZE rows per DELETE. The DELETE
            # re-applies the SAME filters restricted to the selected ids, so the deleted set is
            # always a subset of the selected set (forward progress, no infinite re-selection).
            id_stmt = _filters(select(JobHistory.id)).order_by(
                JobHistory.finished_at, JobHistory.id
            ).limit(CLEANUP_BATCH_SIZE)
            total_deleted = 0
            while True:
                ids = list(session.exec(id_stmt).all())
                if not ids:
                    break
                deleted = self._delete_history_batch(session, ids, _filters)
                session.commit()
                if not deleted:
                    # No selected row was deletable (all gone mid-loop) — stop to avoid
                    # re-selecting the same ids indefinitely.
                    break
                total_deleted += deleted
                time.sleep(0.1)

            return {"deleted": total_deleted, "count": total_deleted}

    @staticmethod
    def _delete_history_batch(session, ids: list, apply_filters=None) -> int:
        """Delete history rows ``ids`` (and their finished attempts); returns rows deleted.

        Retention is the only thing that removes history, and an attempt outlives neither
        its job nor the history row of it.
        """
        stmt = delete(JobHistory).where(JobHistory.id.in_(ids))
        if apply_filters is not None:
            stmt = apply_filters(stmt)  # the same predicates as the selection
        result = session.exec(stmt)
        session.exec(
            delete(JobAttempt)
            .where(JobAttempt.job_id.in_(ids))
            .where(JobAttempt.finished_at.is_not(None))
            .execution_options(synchronize_session=False)
        )
        return result.rowcount

    def _attempts_partitioned(self) -> bool:
        """True iff the attempt table is partitioned (PostgreSQL), so partition drops reclaim it."""
        if not self._partitioned_pg() or _partitioning is None:
            return False
        with self.raw_cursor() as cur:
            return cur is not None and _partitioning.is_partitioned(cur, JobAttempt.__tablename__)

    def _prune_attempts_without_history(self, limit: int = 5000) -> int:
        """Delete finished attempts whose job is in neither the live table nor history (one
        bounded batch per call). Used where attempts cannot be dropped with a partition."""
        with self._get_session() as session:
            has_job = select(JobAll.id).where(JobAll.id == JobAttempt.job_id).exists()
            ids = list(
                session.exec(
                    select(JobAttempt.id)
                    .where(JobAttempt.finished_at.is_not(None))
                    .where(~has_job)
                    .limit(limit)
                ).all()
            )
            if not ids:
                return 0
            res = session.exec(
                delete(JobAttempt)
                .where(JobAttempt.id.in_(ids))
                .execution_options(synchronize_session=False)
            )
            session.commit()
            return res.rowcount

    def cleanup_per_job_ttl(self, dry_run: bool = False) -> dict:
        """Delete finished jobs (history) whose per-job result_ttl / failure_ttl has elapsed.

        A TTL of None or -1 keeps the job forever (RQ semantics).
        """
        now = datetime.now(UTC)
        total = 0
        with self._get_session() as session:
            for statuses, ttl_col in (
                (["success"], JobHistory.result_ttl),
                (["failed", "archived"], JobHistory.failure_ttl),
            ):
                rows = session.exec(
                    select(JobHistory.id, JobHistory.finished_at, ttl_col).where(
                        JobHistory.status.in_(statuses),
                        ttl_col.is_not(None),
                        ttl_col != -1,
                    )
                ).all()
                expired = [
                    job_id
                    for job_id, finished_at, ttl in rows
                    # SQLite returns naive datetimes; treat them as UTC.
                    if (finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=UTC))
                    + timedelta(seconds=ttl)
                    < now
                ]
                total += len(expired)
                if expired and not dry_run:
                    for i in range(0, len(expired), CLEANUP_BATCH_SIZE):
                        self._delete_history_batch(session, expired[i : i + CLEANUP_BATCH_SIZE])
            if not dry_run:
                session.commit()
        return {"deleted" if not dry_run else "would_delete": total}

    def cleanup_jobs_by_count(
        self,
        status: str | None = None,
        keep_count: int = 1000,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Keep only the most recent N finished jobs (by finish time); delete older history."""
        with self._get_session() as session:
            def _filters(stmt):
                if status:
                    stmt = stmt.where(JobHistory.status == status)
                if queue_name:
                    stmt = stmt.where(JobHistory.queue_name == queue_name)
                return stmt

            total = session.exec(_filters(select(func.count(JobHistory.id)))).one()
            kept = min(total, keep_count)
            if dry_run:
                return {"deleted": 0, "count": max(0, total - keep_count), "kept": kept}

            # Rows past the newest ``keep_count``: each pass takes the next batch of them
            # (what was deleted is gone from the offset window, so the offset stays put).
            older = (
                _filters(select(JobHistory.id))
                .order_by(JobHistory.finished_at.desc(), JobHistory.id.desc())
                .offset(keep_count)
                .limit(CLEANUP_BATCH_SIZE)
            )
            deleted = 0
            while True:
                ids = list(session.exec(older).all())
                if not ids:
                    break
                batch = self._delete_history_batch(session, ids)
                session.commit()
                if not batch:
                    break
                deleted += batch
            return {"deleted": deleted, "count": deleted, "kept": kept}

    def get_database_stats(self) -> dict:
        """Get database statistics."""
        with self._get_session() as session:
            # Job counts
            job_count_stmt = select(JobAll.status, func.count(JobAll.id).label("count")).group_by(
                JobAll.status
            )
            job_counts = {status: count for status, count in session.exec(job_count_stmt).all()}

            # Registry counts
            registry_count_stmt = select(
                JobRegistry.registry_type, func.count(JobRegistry.id).label("count")
            ).group_by(JobRegistry.registry_type)
            registry_counts = {
                registry_type: count
                for registry_type, count in session.exec(registry_count_stmt).all()
            }

            # Total counts
            total_jobs = session.exec(select(func.count(JobAll.id))).one()
            total_registries = session.exec(select(func.count(JobRegistry.id))).one()
            total_scheduled_tasks = session.exec(select(func.count(ScheduledTask.id))).one()
            enabled_scheduled_tasks = session.exec(
                select(func.count(ScheduledTask.id)).where(ScheduledTask.enabled == True)
            ).one()
            total_workers = session.exec(select(func.count(Worker.id))).one()

            stats = {
                "total_jobs": total_jobs,
                "job_counts": job_counts,
                "total_registries": total_registries,
                "registry_counts": registry_counts,
                "total_scheduled_tasks": total_scheduled_tasks,
                "enabled_scheduled_tasks": enabled_scheduled_tasks,
                "total_workers": total_workers,
            }

            return stats

    def vacuum_database(self) -> dict:
        """Run database vacuum/optimize (PostgreSQL VACUUM).

        On a partitioned PG install, VACUUM ANALYZE on the parent table
        (sqlery_queued_job) is skipped — partition DROP leaves nothing to
        vacuum on the parent and individual partitions are vacuumed by
        autovacuum per-child (D5/R3). Other tables are always vacuumed.
        SQLite uses a single whole-database VACUUM.

        Mirrors DjangoBackend.vacuum_database (Phase 16 carry-forward).
        """
        # from sqlalchemy import text  # moved to top-level

        # CR-03: VACUUM cannot run inside a transaction block on PostgreSQL. The old
        # implementation issued VACUUM via session.exec() inside get_session(), which
        # SQLAlchemy wraps in a transaction, so PG raised
        # "VACUUM cannot run inside a transaction block"; the except caught it and
        # silently returned success:False on every call. Run VACUUM on a raw connection
        # with isolation_level="AUTOCOMMIT" instead (mirrors the Django backend, which
        # uses connection.cursor() outside the ORM transaction).
        #
        # Old (broken — transaction-wrapped):
        # try:
        #     with self._get_session() as session:
        #         if not self._partitioned_pg():
        #             session.exec(text("VACUUM ANALYZE sqlery_queued_job"))
        #         session.exec(text("VACUUM ANALYZE sqlery_scheduled_task"))
        #         session.exec(text("VACUUM ANALYZE sqlery_registry"))
        #         session.exec(text("VACUUM ANALYZE sqlery_worker"))
        #         session.commit()
        #     return {"success": True, "message": "Database vacuumed successfully"}
        # except Exception as e:
        #     return {"success": False, "error": str(e)}
        try:
            engine = get_engine()
            is_sqlite = engine.dialect.name == "sqlite"
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                if is_sqlite:
                    # SQLite: single whole-database VACUUM (no per-table / ANALYZE),
                    # mirroring DjangoBackend.vacuum_database's SQLite branch.
                    conn.execute(text("VACUUM"))
                else:
                    # PostgreSQL: per-table VACUUM ANALYZE.
                    # Partition DROP leaves nothing to vacuum on the parent table;
                    # skip the parent when partitioned (D5/R3).
                    if not self._partitioned_pg():
                        conn.execute(text("VACUUM ANALYZE sqlery_queued_job"))
                        conn.execute(text("VACUUM ANALYZE sqlery_job_history"))
                    # else: partition DROP leaves nothing to vacuum on parent; skip (D5/R3)
                    #       (the history and the attempt table are partitioned the same way)
                    conn.execute(text("VACUUM ANALYZE sqlery_scheduled_task"))
                    conn.execute(text("VACUUM ANALYZE sqlery_registry"))
                    conn.execute(text("VACUUM ANALYZE sqlery_worker"))

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
        entry = JobRegistry(
            job_id=job_id,
            registry_type=registry_type,
            extra_data=metadata or {},
        )

        with self._get_session() as session:
            session.add(entry)
            session.commit()

    def remove_job_from_registry(self, job_id: int, registry_type: str):
        """Remove job from a registry."""
        with self._get_session() as session:
            stmt = select(JobRegistry).where(
                and_(
                    JobRegistry.job_id == job_id,
                    JobRegistry.registry_type == registry_type,
                    JobRegistry.exited_at == None,
                )
            )

            entries = session.exec(stmt).all()

            for entry in entries:
                entry.exited_at = datetime.now(UTC)
                session.add(entry)

            session.commit()

    def get_registry_jobs(
        self,
        registry_type: str,
        queue_name: str | None = None,
        limit: int | None = None,
    ) -> list:
        """Get jobs in a specific registry."""
        with self._get_session() as session:
            stmt = (
                select(JobRegistry)
                .where(
                    and_(JobRegistry.registry_type == registry_type, JobRegistry.exited_at == None)
                )
                .join(JobAll, JobAll.id == JobRegistry.job_id)
            )

            if queue_name:
                stmt = stmt.where(JobAll.queue_name == queue_name)

            if limit:
                stmt = stmt.limit(limit)

            entries = session.exec(stmt).all()

            # Fetch jobs for entries
            # session.get(QueuedJob, entry.job_id)  # Replaced: composite PK requires (created_at, id)
            jobs = []
            for entry in entries:
                # live or finished: the registries mostly list finished jobs
                job = session.exec(history_sql.anywhere_by_id_stmt(entry.job_id)).first()
                if job:
                    jobs.append(job)

            return jobs

    def cleanup_registry(
        self,
        registry_type: str | None = None,
        max_age_days: int | None = None,
    ) -> dict:
        """Clean up old registry entries."""
        with self._get_session() as session:
            stmt = delete(JobRegistry)

            if registry_type:
                stmt = stmt.where(JobRegistry.registry_type == registry_type)

            if max_age_days:
                cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
                stmt = stmt.where(JobRegistry.entered_at < cutoff)

            result = session.exec(stmt)
            session.commit()

            return {"deleted": result.rowcount}

    def get_job_by_id(self, job_id: int):
        """Get job by ID, spanning both sqlery_queued_job and sqlery_scheduled_job.

        On partitioned PG, falls back to ScheduledJob when not found in QueuedJob.
        On SQLite single-table lookup is unchanged (D6).
        """
        with self._get_session() as session:
            # session.get(QueuedJob, job_id)  # Replaced: composite PK requires (created_at, id)
            job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            if job is not None:
                return job
            # A finished job is in history (read-only).
            finished = session.exec(history_sql.history_by_id_stmt(job_id)).first()
            if finished is not None:
                return finished
            # Fall back to staging table on partitioned PG (D6 gate)
            if self._partitioned_pg():
                staged = session.exec(
                    select(ScheduledJob).where(ScheduledJob.id == job_id)
                ).first()
                return staged
            return None

    def mark_job_success(
        self,
        job_id: int,
        output: str = "",
        expected_version: int | None = None,
        attempt=None,
    ):
        """Mark job as successful.

        Staged ScheduledJob rows (not yet promoted) do not have mark_success;
        the guard prevents AttributeError if an operator calls this for a staged id.

        expected_version fences the write: when given, the update only
        applies if the row's current version still matches (optimistic-
        locking CAS, mirroring claim_job's version-CAS branch above). If it
        doesn't match, another worker already reclaimed this job (its lease/
        heartbeat went stale and the zombie detector requeued it) — raise
        JobFencingError instead of silently clobbering the reclaiming
        worker's result.

        ``attempt`` is the second fence: the claim's attempt is closed in the same
        transaction as the job update, and a completion whose attempt is no longer open
        (finished already, or abandoned by recovery) raises JobFencingError without
        touching the job row.
        """
        job = self.get_job_by_id(job_id)
        if isinstance(job, JobHistory):
            return self._already_finished(job, expected_version, attempt)
        # Old: if job: job.mark_success(...)  <-- AttributeError for ScheduledJob (IN-01)
        if job and hasattr(job, "mark_success"):
            with self._get_session() as session:
                db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
                if db_job:
                    # Detach before mutating: an attached instance's dirty attributes
                    # would autoflush unconditionally on the next session.exec() call,
                    # bypassing the version-fenced UPDATE below (the CAS check has to
                    # be the only write path, not a side effect of ORM tracking).
                    session.expunge(db_job)
                    db_job.mark_success(output=output)
                    values = {
                        "status": db_job.status,
                        "finished_at": db_job.finished_at,
                        "duration_seconds": db_job.duration_seconds,
                        "output": db_job.output,
                        "runs": db_job.runs,
                    }
                    return self._fenced_update(
                        session, job_id, expected_version, values, attempt, OUTCOME_SUCCESS,
                        parent_job_id=db_job.parent_job_id,
                    )
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
            with self._get_session() as session:
                db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
                if db_job:
                    session.expunge(db_job)
                    db_job.mark_failed(
                        error=error, traceback=traceback, termination_reason=termination_reason
                    )
                    values = {
                        "status": db_job.status,
                        "finished_at": db_job.finished_at,
                        "duration_seconds": db_job.duration_seconds,
                        "error": db_job.error,
                        "traceback": db_job.traceback,
                        "termination_reason": db_job.termination_reason,
                        "runs": db_job.runs,
                    }
                    return self._fenced_update(
                        session, job_id, expected_version, values, attempt, OUTCOME_FAILED,
                        parent_job_id=db_job.parent_job_id,
                    )
        return job

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

    def _fenced_update(
        self,
        session: Session,
        job_id: int,
        expected_version: int | None,
        values: dict,
        attempt=None,
        outcome: str = OUTCOME_FAILED,
        parent_job_id: int | None = None,
    ):
        """Record a completion: the job leaves the live table for history, fenced.

        The transition (``INSERT INTO sqlery_job_history .. SELECT`` + ``DELETE``) is one
        transaction with the attempt close. When ``expected_version`` is given it only
        applies to a row whose version still matches (compare-and-set in the WHERE clause,
        mirroring DjangoBackend); zero rows means another worker already reclaimed the job
        (its lease/heartbeat went stale and the zombie detector requeued it) -- raise
        JobFencingError instead of silently clobbering the reclaiming worker's result, and
        write nothing to history.

        The execution attempt is closed first, in the same transaction (see
        ``_close_attempt``): a stale attempt raises JobFencingError before the job row is
        touched, and any later failure rolls the attempt close back with it.

        A failure that will be retried (``retry_pending`` marker) is not final: the job stays
        in the live table, marked failed, until its retry exists (``mark_job_archived`` /
        ``settle_retry_pending`` then move it).
        """
        try:
            self._close_attempt(session, job_id, attempt, outcome)
        except JobFencingError:
            session.rollback()
            raise
        status = values["status"]
        row_values = {k: v for k, v in values.items() if k != "status"}
        from sqlery.core.retry import is_retry_pending

        if status == "failed" and is_retry_pending(row_values.get("termination_reason")):
            stmt = update(QueuedJob).where(QueuedJob.id == job_id)
            if expected_version is not None:
                stmt = stmt.where(QueuedJob.version == expected_version)
            stmt = stmt.values(version=QueuedJob.version + 1, **values).execution_options(
                synchronize_session=False
            )
            applied = session.exec(stmt).rowcount > 0
        else:
            applied = self._finish_in(
                session, job_id, status, row_values, expected_version=expected_version
            )
        if not applied:
            session.rollback()
            if expected_version is not None:
                raise JobFencingError(
                    f"Job {job_id} was reclaimed by another worker "
                    f"(expected version {expected_version})"
                )
            # No row matched at all (job deleted concurrently) -- nothing to return.
            return None
        session.commit()
        if status in ("success", "failed") and not is_retry_pending(
            row_values.get("termination_reason")
        ):
            # Terminal: jobs waiting on this one are re-checked (moved to ready, or failed).
            self._release_dependents_quietly(job_id, parent_job_id)
        return self._find_job(session, job_id)

    @staticmethod
    def _find_job(session, job_id):
        """The job as a model object: the live row, else its history row, else None."""
        job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
        if job is not None:
            return job
        return session.exec(history_sql.history_by_id_stmt(job_id)).first()

    def mark_job_archived(self, job_id: int):
        """Mark a failed job as archived (a retry has been created for it): the job moves to
        history as ``archived`` and its retry marker is cleared. A job that is already in
        history is final and stays as it is.

        Write-path pruning: fetches created_at first so PG can prune to one partition.
        Mirrors DjangoBackend.mark_job_archived (Phase 16 item 8).
        """
        with self._get_session() as session:
            row = session.exec(
                select(QueuedJob.created_at, QueuedJob.termination_reason).where(
                    and_(QueuedJob.id == job_id, QueuedJob.status == "failed")
                )
            ).first()
            if row is not None:
                created_at_val, reason = row
                self._finish_in(
                    session,
                    job_id,
                    "archived",
                    {"termination_reason": history_core.settled_termination_reason(reason)},
                    created_at=created_at_val,
                    require_status="failed",
                )
                session.commit()

    def cascade_ancestor_status(self, job_id: int, status: str):
        """Walk the parent_job_id chain, set the live ancestors to the given status.

        Ancestors that are already in history are final (history is insert-only) and are
        only walked through. A live ancestor that is not already ``success`` / ``archived``
        becomes ``status`` (a terminal status moves it into history).
        """
        with self._get_session() as session:
            current_id = session.exec(
                select(JobAll.parent_job_id).where(JobAll.id == job_id)
            ).first()
            seen: set[int] = set()
            while current_id and current_id not in seen:
                seen.add(current_id)
                job_row = session.exec(
                    select(JobAll.created_at, JobAll.parent_job_id).where(
                        JobAll.id == current_id
                    )
                ).first()
                if not job_row:
                    break
                row_created_at, next_parent_id = job_row
                if history_core.is_terminal(status):
                    self._finish_in(
                        session,
                        current_id,
                        status,
                        {},
                        created_at=row_created_at,
                        require_status=("queued", "running", "failed", "shutting_down"),
                    )
                else:
                    session.exec(
                        update(QueuedJob)
                        .where(QueuedJob.id == current_id)
                        .where(QueuedJob.created_at == row_created_at)
                        .where(QueuedJob.status.not_in(("success", "archived")))
                        .values(status=status)
                        .execution_options(synchronize_session=False)
                    )
                current_id = next_parent_id
            session.commit()

    def has_pending_job_for_scheduled_task(self, task_id: int) -> bool:
        """Check if scheduled task has pending jobs."""
        with self._get_session() as session:
            stmt = select(func.count(QueuedJob.id)).where(
                and_(
                    QueuedJob.scheduled_task_id == task_id,
                    QueuedJob.status.in_(["queued", "running"]),
                )
            )

            count = session.exec(stmt).one()
            return count > 0

    def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
        """Update scheduled task's next run time."""
        with self._get_session() as session:
            task = session.get(ScheduledTask, task_id)

            if task:
                task.next_run_at = next_run_at
                session.add(task)
                session.commit()

    def advance_scheduled_task_if_due(
        self,
        task_id: int,
        observed_next_run_at: datetime,
        new_next_run_at: datetime,
        job_kwargs: dict,
    ) -> Any:
        """Atomically advance next_run_at on a CAS and enqueue in the same txn.

        Advances ``next_run_at`` to ``new_next_run_at`` ONLY when the row still
        equals ``observed_next_run_at`` (CAS on the observed due time — the
        ScheduledTask has no version column). On a winning advance, the queued
        job is created from ``job_kwargs`` inside the SAME session so the advance
        and the enqueue commit together (CRON-01); only the caller whose CAS
        wins enqueues, so concurrent leaders cannot double-fire (CRON-04).

        PostgreSQL uses a blocking ``with_for_update()`` row lock (NOT
        ``skip_locked`` — a single-key row, per CR-01) then a read-compare-write.
        SQLite/fallback uses a predicate-CAS ``update(...).where(next_run_at ==
        observed)`` with ``synchronize_session=False`` so the ORM evaluator does
        not run against the naive SQLite datetime column; success is
        ``rowcount == 1``.

        Args:
            task_id: Scheduled task ID.
            observed_next_run_at: The ``next_run_at`` observed when the task was due.
            new_next_run_at: The value to advance to when the CAS wins.
            job_kwargs: Fields passed through to build the QueuedJob in-session.

        Returns:
            The created QueuedJob when this caller won the CAS, otherwise ``None``.
        """
        with self._get_session() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            strategy = determine_claim_strategy(dialect)

            if strategy == "skip_locked":
                # Postgres: blocking row lock on the single-key row (CR-01),
                # then read-compare-write under the lock.
                stmt = select(ScheduledTask).where(ScheduledTask.id == task_id).with_for_update()
                existing = session.exec(stmt).first()
                # # Old (WR-06): guarded only `existing is None`, then dereferenced
                # # existing.next_run_at.tzinfo unconditionally — but next_run_at is
                # # nullable (a concurrent once-disable can null it), raising
                # # AttributeError inside the txn. Treat a None next_run_at as a lost CAS.
                # if existing is None:
                #     return None
                if existing is None or existing.next_run_at is None:
                    return None
                # WR-05: re-check enabled under the lock so a task disabled mid-cycle
                # (operator action, or a `once` task disabling itself) does not fire.
                if not existing.enabled:
                    return None
                # DB column may be naive (SQLite); normalize before compare.
                existing_due = (
                    existing.next_run_at
                    if existing.next_run_at.tzinfo
                    else existing.next_run_at.replace(tzinfo=UTC)
                )
                observed = (
                    observed_next_run_at
                    if observed_next_run_at.tzinfo
                    else observed_next_run_at.replace(tzinfo=UTC)
                )
                if existing_due != observed:
                    # Another leader already advanced this tick — lost the CAS.
                    return None
                existing.next_run_at = new_next_run_at
                session.add(existing)
                job = self._build_queued_job(job_kwargs)
                session.add(job)
                session.commit()
                session.refresh(job)
                return job

            # SQLite / fallback: predicate-CAS on the observed next_run_at.
            cas_stmt = (
                update(ScheduledTask)
                .where(ScheduledTask.id == task_id)
                .where(ScheduledTask.next_run_at == observed_next_run_at)
                # WR-05: re-check enabled in the predicate so a task disabled
                # mid-cycle does not win the CAS and fire.
                .where(ScheduledTask.enabled == True)  # noqa: E712 — SQL boolean compare
                .values(next_run_at=new_next_run_at)
                # Raw SQL: skip the ORM evaluator so the naive SQLite datetime
                # column is compared in the database, not in Python.
                .execution_options(synchronize_session=False)
            )
            res = session.exec(cas_stmt)
            if res.rowcount != 1:
                # Lost the CAS (another leader advanced first) — do not enqueue.
                session.rollback()
                return None
            job = self._build_queued_job(job_kwargs)
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def _build_queued_job(self, job_kwargs: dict) -> QueuedJob:
        """Construct a queued QueuedJob from create_job-style kwargs.

        Mirrors create_job's field mapping so the advance+enqueue can share one
        session/transaction without create_job opening its own session.
        """
        return QueuedJob(
            task_path=job_kwargs["task_path"],
            kwargs=job_kwargs.get("kwargs") or {},
            queue_name=job_kwargs["queue_name"],
            priority=job_kwargs.get("priority", 0),
            scheduled_at=job_kwargs.get("scheduled_at"),
            max_retries=job_kwargs.get("max_retries", 0),
            # # Old (WR-01): defaulted to 0.0, diverging from the model field /
            # # create_job canonical baseline of 1.0 (yields 0.0 * 2**n = 0 backoff).
            # retry_backoff=job_kwargs.get("retry_backoff", 0.0),
            retry_backoff=job_kwargs.get("retry_backoff", 1.0),
            allow_parallel=job_kwargs.get("allow_parallel", False),
            timeout_seconds=job_kwargs.get("timeout_seconds"),
            retry_count=(
                job_kwargs["retry_count"] if job_kwargs.get("retry_count") is not None else 0
            ),
            scheduled_task_id=job_kwargs.get("scheduled_task_id"),
            job_name=job_kwargs.get("job_name"),
            retry_intervals=job_kwargs.get("retry_intervals"),
            meta=job_kwargs.get("meta"),
            dependencies=job_kwargs.get("dependencies") or [],
            on_success_path=job_kwargs.get("on_success_path", ""),
            on_failure_path=job_kwargs.get("on_failure_path", ""),
            ttl=job_kwargs.get("ttl"),
            result_ttl=job_kwargs.get("result_ttl"),
            failure_ttl=job_kwargs.get("failure_ttl"),
            parent_job_id=job_kwargs.get("parent_job_id"),
            status="queued",
        )

    def update_scheduled_task(self, task_id: int, **updates) -> Any:
        """Update scheduled task fields."""
        with self._get_session() as session:
            task = session.get(ScheduledTask, task_id)

            if task:
                for key, value in updates.items():
                    setattr(task, key, value)

                session.add(task)
                session.commit()
                session.refresh(task)

            return task

    def delete_scheduled_task(self, task_id: int) -> bool:
        """Delete scheduled task."""
        with self._get_session() as session:
            task = session.get(ScheduledTask, task_id)

            if task:
                session.delete(task)
                session.commit()
                return True

            return False

    def get_scheduled_tasks(self, enabled_only: bool = False) -> list:
        """Get all scheduled tasks."""
        with self._get_session() as session:
            stmt = select(ScheduledTask)

            if enabled_only:
                stmt = stmt.where(ScheduledTask.enabled == True)

            stmt = stmt.order_by(ScheduledTask.name)

            return list(session.exec(stmt).all())

    def get_scheduled_task(self, task_id: int):
        """Get scheduled task by ID."""
        with self._get_session() as session:
            return session.get(ScheduledTask, task_id)

    def get_running_jobs(self, queue_name: str | None = None) -> list:
        """Get currently running jobs."""
        with self._get_session() as session:
            stmt = select(QueuedJob).where(QueuedJob.status == "running")

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            return list(session.exec(stmt).all())

    def get_running_jobs_for_liveness(self, queue_names: list[str] | None = None) -> list:
        """Build RunningJobLiveness records for the zombie sweep.

        Loads each ``status='running'`` job together with its assigned worker
        and maps to the framework-agnostic dataclass. Datetimes are normalised
        to timezone-aware UTC (SQLite/Postgres may return naive values).
        """
        from sqlery.core.liveness import RunningJobLiveness
        from sqlery.django_sqlery.friendly_name import uuid_to_friendly

        def _aware(dt):
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

        with self._get_session() as session:
            stmt = select(QueuedJob).where(QueuedJob.status == "running")
            if queue_names:
                stmt = stmt.where(QueuedJob.queue_name.in_(queue_names))

            records = []
            jobs = list(session.exec(stmt).all())
            attempts = self._open_attempts_in(session, [j.id for j in jobs])
            for job in jobs:
                worker = job.worker
                attempt = attempts.get(job.id)
                if worker is not None:
                    try:
                        friendly = uuid_to_friendly(worker.id)
                    except Exception:
                        friendly = str(worker.id)
                else:
                    friendly = None
                records.append(
                    RunningJobLiveness(
                        job_id=job.id,
                        started_at=_aware(job.started_at),
                        # The open attempt; the row column only for jobs claimed before attempts.
                        worker_pid=attempt.worker_pid if attempt else job.worker_pid,
                        worker_node_id=worker.node_id if worker else None,
                        worker_status=worker.status if worker else None,
                        worker_current_job_id=worker.current_job_id if worker else None,
                        worker_last_heartbeat=_aware(worker.last_heartbeat) if worker else None,
                        worker_friendly_name=friendly,
                        has_worker=worker is not None,
                    )
                )
            return records

    def fail_zombie_job(self, job_id: int, reason: str) -> bool:
        """Mark a running job failed with termination_reason='zombie_job'.

        Bumps `version` on reclaim, mirroring DjangoBackend's CAS
        mark_failed (F("version") + 1). Without this, a superseded
        worker's late completion write (mark_job_success/mark_job_failed
        fenced with its originally-observed expected_version) would still
        match the unchanged version and silently overwrite the zombie
        outcome — the fencing check added in the lease-renewal fix would
        be a silent no-op in standalone mode.

        The open attempt is abandoned in the same transaction, so a late completion from
        the dead worker is rejected by the attempt fence as well.
        """
        with self._get_session() as session:
            # session.get(QueuedJob, job_id)  # Replaced: composite PK requires (created_at, id)
            job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            if job is None:
                return False
            self._close_attempt(session, job_id, None, OUTCOME_ABANDONED)
            # Detach before mutating: the in-memory mark_failed only computes the outcome
            # (runs, duration); the write is the move into history below.
            session.expunge(job)
            job.mark_failed(error=reason, termination_reason="zombie_job")
            parent = job.parent_job_id
            moved = self._finish_in(
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
            )
            if not moved:
                session.rollback()
                return False
            session.commit()
        self._release_dependents_quietly(job_id, parent)
        return True

    def has_running_jobs_in_queue(self, queue_name: str, exclude_job_id: int | None = None) -> bool:
        """Check if queue has running jobs."""
        with self._get_session() as session:
            stmt = select(func.count(QueuedJob.id)).where(
                and_(QueuedJob.queue_name == queue_name, QueuedJob.status == "running")
            )

            if exclude_job_id:
                stmt = stmt.where(QueuedJob.id != exclude_job_id)

            count = session.exec(stmt).one()
            return count > 0

    def update_job_child_pid(self, job_id: int, child_pid: int, created_at=None, attempt=None):
        """Store the forked child PID on the job's open attempt.

        Args:
            job_id: QueuedJob primary key.
            child_pid: PID of the forked child process.
            created_at: Unused (the attempt table has no partition key); kept for callers.
            attempt: Handle of the attempt the caller holds; when given only that exact
                attempt is updated, so a superseded worker cannot stamp a newer attempt.
        """
        # Old: wrote QueuedJob.child_pid, partition-pruned by created_at (item 11). The PID
        # now lives on the attempt; #CLEANUP: drop QueuedJob.child_pid once no reader falls
        # back to it.
        with self._get_session() as session:
            session.exec(attempt_sql.child_pid_stmt(job_id, child_pid, attempt))
            session.commit()

    def delete_worker_registration(self, worker_id: str) -> int:
        """Delete stale Worker row from a previous crash."""
        with self._get_session() as session:
            worker = session.get(Worker, self._worker_pk(worker_id))
            if worker:
                session.delete(worker)
                session.commit()
                return 1
            return 0

    def release_claimed_job(
        self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs
    ):
        """Release a job after processing and update worker state.

        A terminal ``status`` moves the job into history; the returned object is its history
        row. Write-path pruning: uses job.created_at if the caller passes the full job object
        so PG can prune to one partition (item 10). Falls back to id-only for SQLite.
        """
        parent_job_id = None
        with self._get_session() as session:
            created_at = getattr(job, "created_at", None)
            stmt = select(QueuedJob).where(QueuedJob.id == job.id)
            if created_at is not None:
                stmt = stmt.where(QueuedJob.created_at == created_at)
            db_job = session.exec(stmt).first()
            result = None
            if db_job:
                parent_job_id = db_job.parent_job_id
                self._close_attempt(
                    session,
                    db_job.id,
                    None,
                    OUTCOME_SUCCESS if status == "success" else OUTCOME_FAILED,
                )
                finished_at = datetime.now(UTC)
                duration = None
                if db_job.started_at:
                    started = db_job.started_at
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=UTC)
                    duration = (finished_at - started).total_seconds()
                columns = set(JobHistory.__table__.c.keys()) - {"id", "status", "version"}
                values = {k: v for k, v in kwargs.items() if k in columns}
                values.setdefault("finished_at", finished_at)
                values.setdefault("duration_seconds", duration)
                if history_core.is_terminal(status):
                    session.expunge(db_job)
                    self._finish_in(
                        session, db_job.id, status, values, created_at=db_job.created_at
                    )
                else:
                    db_job.status = status
                    for key, value in values.items():
                        setattr(db_job, key, value)
                    session.add(db_job)

            # Update worker back to idle
            worker = session.get(Worker, self._worker_pk(worker_id))
            if worker:
                worker.status = "idle"
                worker.current_job_id = None
                worker.jobs_processed = jobs_processed
                worker.last_heartbeat = datetime.now(UTC)
                session.add(worker)

            session.commit()
            if db_job is not None:
                result = self._find_job(session, db_job.id)
        if result is not None and result.status in ("success", "failed"):
            self._release_dependents_quietly(result.id, parent_job_id)
        return result

    @staticmethod
    def _count_tag_in_session(session, tag: str, statuses: list[str], since=None) -> int:
        """Count jobs carrying ``tag``. Filters in Python: JSON ``contains`` is not portable
        (unsupported on SQLite, wrong operator on PostgreSQL ``json``)."""
        # Finished jobs live in history: read the union view.
        stmt = select(JobAll.tags).where(JobAll.status.in_(statuses))
        if since is not None:
            stmt = stmt.where(JobAll.started_at != None, JobAll.started_at >= since)  # noqa: E711
        return sum(1 for tags in session.exec(stmt).all() if tags and tag in tags)

    def count_running_with_tag(self, tag: str) -> int:
        """Count currently running jobs with the given tag."""
        with self._get_session() as session:
            return self._count_tag_in_session(session, tag, ["running"])

    def count_started_with_tag_since(self, tag: str, threshold: datetime) -> int:
        """Count jobs with the given tag that started since threshold."""
        with self._get_session() as session:
            return self._count_tag_in_session(
                session, tag, ["running", "success", "failed"], since=threshold
            )

    def get_expired_ttl_jobs(self) -> list:
        """Get queued jobs whose TTL has expired (detached rows: expire them through
        :meth:`expire_queued_job`, mutating the returned rows persists nothing)."""
        with self._get_session() as session:
            stmt = select(QueuedJob).where(
                and_(
                    QueuedJob.status == "queued",
                    QueuedJob.ttl != None,
                )
            )
            now = datetime.now(UTC)
            expired = []
            for job in session.exec(stmt).all():
                created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
                if created + timedelta(seconds=job.ttl) < now:
                    expired.append(job)
            return expired

    def expire_queued_job(
        self, job, error: str, termination_reason: str = "expired"
    ) -> bool:
        """Fail a job that is still queued and move it to history (fenced on its status).

        The ready / blocked entries go with it in the same transaction and the jobs waiting on
        it are released afterwards. Returns False, writing nothing, when the job is no longer
        queued (claimed, cancelled or already expired by someone else).
        """
        with self._get_session() as session:
            done = self._fail_queued(session, job, error, termination_reason, datetime.now(UTC))
            if done:
                session.commit()
            else:
                session.rollback()
        if done:
            self._release_dependents_quietly(job.id)
        return done

    def acquire_tag_locks(self, tags: list[str]) -> None:
        """Acquire exclusive locks on tag coordination rows (PostgreSQL)."""
        # SQLAlchemy backend uses PostgreSQL advisory locks or SELECT FOR UPDATE
        # For now, the transaction isolation provides basic safety
        pass

    def get_claimable_jobs(
        self,
        queues: list[str],
        priority_weights: dict[str, int] | None = None,
        limit: int = 1,
        exclude_ids: list | None = None,
    ) -> list:
        """Next claimable jobs in claim order, read through the ready queue (#139).

        Entries whose job is no longer queued are left out (a claim removes them).
        """
        with self._get_session() as session:
            now = datetime.now(UTC)
            t = JobReady
            stmt = (
                select(QueuedJob)
                .join(t, and_(t.job_id == QueuedJob.id, t.created_at == QueuedJob.created_at))
                .where(t.queue_name.in_(queues))
                .where(or_(t.scheduled_at.is_(None), t.scheduled_at <= now))
                .where(QueuedJob.status == "queued")
                .order_by(t.priority.desc(), t.created_at, t.job_id)
                .limit(limit)
            )
            if exclude_ids:
                stmt = stmt.where(t.job_id.not_in(exclude_ids))
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if determine_claim_strategy(dialect) == "skip_locked":
                stmt = stmt.with_for_update(skip_locked=True, of=t)
            return list(session.exec(stmt).all())

    def atomic_claim_job(self, job, worker) -> bool:
        """Atomically claim a specific job for a worker.

        Deleting the job's ready entry is the claim (rowcount 1); the job row is then set
        running and the attempt inserted in the same transaction.
        """
        with self._get_session() as session:
            session.expire_on_commit = False
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if session.exec(ready_sql.delete_ready_stmt([job.id])).rowcount < 1:
                session.commit()
                return False  # not ready (claimed by someone else, cancelled, or blocked)
            now = datetime.now(UTC)
            res = session.exec(
                update(QueuedJob)
                .where(QueuedJob.id == job.id)
                .where(QueuedJob.created_at == job.created_at)
                .where(QueuedJob.status == "queued")
                .values(status="running", started_at=now, version=QueuedJob.version + 1)
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                self._restore_entry_if_queued(session, dialect, job)
                session.commit()
                return False
            handle = self._open_attempt(session, job.id, getattr(worker, "id", None))
            session.commit()
            attach_attempt(job, handle)
            return True

    # ----- ready queue (#139) ----------------------------------------------

    def history_table_exists(self) -> bool:
        from sqlalchemy import inspect as sa_inspect

        return sa_inspect(get_engine()).has_table(JobHistory.__tablename__)

    def ready_tables_exist(self) -> bool:
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(get_engine())
        return inspector.has_table(JobReady.__tablename__) and inspector.has_table(
            JobBlocked.__tablename__
        )

    def block_job(self, job) -> bool:
        """Move a ready job whose dependencies are unmet to the blocked set.

        Returns False if the job was not ready any more (claimed or removed meanwhile).
        """
        created_at = job.created_at
        with self._get_session() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            insert = ready_sql.insert_blocked_stmt(dialect, job)
            if insert is None:
                return False  # no usable dependency id to wait on: the job stays ready
            if session.exec(ready_sql.delete_ready_stmt([job.id])).rowcount < 1:
                session.commit()
                return False
            session.exec(insert)
            session.commit()
        self._recheck_blocked_quietly(job.id, created_at)
        return True

    def release_dependents(self, job_id: int, parent_job_id: int | None = None) -> int:
        """Re-check the jobs blocked on ``job_id`` (a job that reached a terminal state).

        Dependents of the job's retry ancestors are re-checked too: a dependency is judged
        by its newest retry attempt. A dependent whose dependencies are all met moves back to
        the ready table; one with a failed dependency fails (as at claim time before), which
        in turn releases the jobs waiting on it. Returns how many dependents became ready.
        """
        released = 0
        work = [(job_id, parent_job_id)]
        seen: set[int] = set()
        while work:
            current, parent = work.pop()
            if current in seen:
                continue
            seen.add(current)
            for blocked_id, created_at in self._dependents_of(current, parent):
                outcome = self._recheck_blocked(blocked_id, created_at)
                if outcome == "ready":
                    released += 1
                elif outcome == "failed":
                    work.append((blocked_id, None))
        return released

    def _dependents_of(self, job_id: int, parent_job_id: int | None) -> list:
        with self._get_session() as session:
            ids = [job_id]
            parent = parent_job_id
            hops = 0
            while parent and parent not in ids and hops < ready_core.MAX_CHAIN_HOPS:
                ids.append(parent)
                parent = session.exec(
                    select(JobAll.parent_job_id).where(JobAll.id == parent)
                ).first()
                hops += 1
            rows = session.exec(
                select(JobBlocked.job_id, JobBlocked.created_at)
                .where(JobBlocked.dependency_id.in_(ids))
                .distinct()
            ).all()
            return [(r[0], r[1]) for r in rows]

    def _recheck_blocked(self, job_id: int, created_at) -> str:
        """Re-judge one blocked job: ``ready``, ``failed``, ``blocked`` or ``gone``."""
        with self._get_session() as session:
            session.expire_on_commit = False
            dialect = session.bind.dialect.name if session.bind is not None else ""
            job = session.exec(
                select(QueuedJob).where(QueuedJob.id == job_id).where(QueuedJob.created_at == created_at)
            ).first()
            if job is None or job.status != "queued":
                session.exec(ready_sql.delete_blocked_stmt([job_id]))
                session.commit()
                return "gone"
            verdict = self._judge_dependencies(session, ready_core.dependency_ids(job))
            if verdict.state == ready_core.PENDING:
                session.commit()
                return "blocked"
            if verdict.state == ready_core.FAILED:
                self._fail_queued(
                    session, job, f"Dependencies failed: {verdict.failed}", "dependency_failed",
                    datetime.now(UTC),
                )
                session.commit()
                return "failed"
            session.exec(ready_sql.delete_blocked_stmt([job_id]))
            ready_sql.write_ready(session, dialect, [job])
            session.commit()
            return "ready"

    def _release_dependents_quietly(self, job_id: int, parent_job_id: int | None = None) -> None:
        """Release the dependents of a finished job; never raises (the periodic sweep
        repairs whatever this misses)."""
        try:
            self.release_dependents(job_id, parent_job_id)
        except Exception:
            logger.warning(f"release_dependents({job_id}) failed", exc_info=True)

    def _recheck_blocked_quietly(self, job_id: int, created_at) -> None:
        try:
            self._recheck_blocked(job_id, created_at)
        except Exception:
            logger.warning(f"recheck of blocked job {job_id} failed", exc_info=True)

    def maintain_ready_queue(
        self, queue_names: list[str] | None = None, limit: int = 200, after_job_id: int = 0
    ) -> dict:
        """Repair the ready queue (run periodically by the scheduler leader).

        * Queued jobs that have neither a ready nor a blocked entry get a ready entry (a job
          whose entry was lost, e.g. by raw SQL, is delayed by one sweep, never lost).
        * Entries of jobs that are no longer queued are dropped.
        * A page of blocked jobs is re-judged, which releases jobs whose dependency finished
          without anyone noticing and fails the ones whose dependency is gone.

        On PostgreSQL the scans are capped by ``READY_QUEUE_SWEEP_TIMEOUT`` seconds. Returns
        ``{"restored", "dropped", "rechecked", "cursor"}``; pass the cursor back as
        ``after_job_id`` to continue (0 restarts).
        """
        restored = dropped = 0
        settled = self.settle_finished_jobs(limit)
        with self._get_session() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "postgresql":
                try:
                    timeout = float(get_config("READY_QUEUE_SWEEP_TIMEOUT", 10))
                except (TypeError, ValueError):
                    timeout = 10.0
                if timeout > 0:
                    session.exec(
                        text("SELECT set_config('statement_timeout', :t, true)").bindparams(
                            t=f"{int(timeout * 1000)}ms"
                        )
                    )
            in_ready = (
                select(JobReady.job_id)
                .where(JobReady.slot.in_(ready_core.SLOTS))
                .where(JobReady.job_id == QueuedJob.id)
                .exists()
            )
            in_blocked = select(JobBlocked.job_id).where(JobBlocked.job_id == QueuedJob.id).exists()
            stmt = (
                select(QueuedJob)
                .where(QueuedJob.status == "queued")
                .where(~in_ready)
                .where(~in_blocked)
                .limit(limit)
            )
            if queue_names:
                stmt = stmt.where(QueuedJob.queue_name.in_(list(queue_names)))
            lost = session.exec(stmt).all()
            if lost:
                ready_sql.write_ready(session, dialect, lost)
                restored = len(lost)
            live = (
                select(QueuedJob.id)
                .where(QueuedJob.id == JobReady.job_id)
                .where(QueuedJob.created_at == JobReady.created_at)
                .where(QueuedJob.status == "queued")
                .exists()
            )
            stale_stmt = select(JobReady).where(~live).limit(limit)
            if queue_names:
                stale_stmt = stale_stmt.where(JobReady.queue_name.in_(list(queue_names)))
            if dialect == "postgresql":
                stale_stmt = stale_stmt.with_for_update(skip_locked=True)
            for entry in session.exec(stale_stmt).all():
                dropped += session.exec(
                    ready_sql.delete_entry_stmt(entry.slot, entry.job_id)
                ).rowcount
            session.commit()
            page = session.exec(
                select(JobBlocked.job_id, JobBlocked.created_at)
                .where(JobBlocked.job_id > after_job_id)
                .distinct()
                .order_by(JobBlocked.job_id)
                .limit(limit)
            ).all()
        cursor = 0
        for blocked_id, created_at in page:
            self._recheck_blocked_quietly(blocked_id, created_at)
            cursor = blocked_id
        if restored:
            logger.warning(f"Restored {restored} queued job(s) that had no ready entry")
        return {
            "restored": restored,
            "dropped": dropped,
            "rechecked": len(page),
            "settled": settled,
            "cursor": cursor if len(page) >= limit else 0,
        }

    def settle_finished_jobs(self, limit: int = 200) -> int:
        """Move terminal rows stranded in the live table into history; returns how many.

        The terminal transition normally leaves nothing behind. This is the safety net for
        raw SQL / third-party code that sets a final status on ``sqlery_queued_job`` and for a
        crash between the steps of a retry. A failed job that still owes a retry (marker) is
        left alone. One short transaction per row; never raises.
        """
        moved = 0
        try:
            grace = history_core.settle_grace_seconds(get_config)
            cutoff = datetime.now(UTC) - timedelta(seconds=grace)
            with self._get_session() as session:
                strays = session.exec(history_sql.stray_terminal_stmt(limit, cutoff)).all()
                for job in strays:
                    has_retry = (
                        session.exec(history_sql.has_retry_child_stmt(job.id)).first()
                        is not None
                    )
                    status = history_core.settle_status(
                        job.status, job.termination_reason, has_retry
                    )
                    if status is None:
                        continue
                    session.expunge(job)
                    ok = self._finish_in(
                        session,
                        job.id,
                        status,
                        {
                            "termination_reason": history_core.settled_termination_reason(
                                job.termination_reason
                            )
                        },
                        created_at=job.created_at,
                        require_status=job.status,
                    )
                    session.commit()
                    if ok:
                        moved += 1
                        self._release_dependents_quietly(job.id, job.parent_job_id)
        except Exception:
            logger.warning("settle_finished_jobs failed", exc_info=True)
        return moved

    def claim_due_scheduled_task(self, task_id: int):
        """Atomically claim a scheduled task for processing."""
        with self._get_session() as session:
            now = datetime.now(UTC)
            stmt = (
                select(ScheduledTask)
                .where(
                    and_(
                        ScheduledTask.id == task_id,
                        ScheduledTask.enabled == True,
                        ScheduledTask.next_run_at <= now,
                    )
                )
                .with_for_update(skip_locked=True)
            )
            task = session.exec(stmt).first()
            return task

    def release_job(self, job_id: int):
        """Release a claimed job back to queued status."""
        with self._get_session() as session:
            # session.get(QueuedJob, job_id)  # Replaced: composite PK requires (created_at, id)
            job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()

            if job:
                # The open attempt is abandoned in the same transaction as the requeue.
                self._close_attempt(session, job_id, None, OUTCOME_ABANDONED)
                job.status = "queued"
                job.started_at = None
                job.worker_pid = None
                session.add(job)
                session.commit()

    def get_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Get jobs with optional filtering and pagination."""
        with self._get_session() as session:
            # Live and finished jobs together (the union view); a terminal status filter
            # is served by history alone.
            stmt = select(JobAll)

            if status:
                stmt = stmt.where(JobAll.status == status)

            if queue_name:
                stmt = stmt.where(JobAll.queue_name == queue_name)

            # Order by priority (desc) and created_at (asc)
            stmt = stmt.order_by(JobAll.priority.desc(), JobAll.created_at, JobAll.id)

            # Apply pagination
            stmt = stmt.limit(limit).offset(offset)

            return list(session.exec(stmt).all())

    def get_staged_jobs(
        self,
        queue_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Return staged (pre-promotion) jobs from sqlery_scheduled_job.

        Returns [] on SQLite / non-partitioned PG (D6).

        Args:
            queue_name: Optional queue filter.
            limit: Maximum number of results to return.
            offset: Pagination offset.

        Returns:
            list of ScheduledJob instances ordered by scheduled_at ascending.
        """
        if not self._partitioned_pg():
            return []
        with self._get_session() as session:
            stmt = select(ScheduledJob)
            if queue_name:
                stmt = stmt.where(ScheduledJob.queue_name == queue_name)
            stmt = stmt.order_by(ScheduledJob.scheduled_at).limit(limit).offset(offset)
            return list(session.exec(stmt).all())

    def count_jobs(
        self,
        status: str | None = None,
        queue_name: str | None = None,
    ) -> int:
        """Count jobs with optional filtering."""
        with self._get_session() as session:
            stmt = select(func.count(JobAll.id))

            if status:
                stmt = stmt.where(JobAll.status == status)

            if queue_name:
                stmt = stmt.where(JobAll.queue_name == queue_name)

            return session.exec(stmt).one()
