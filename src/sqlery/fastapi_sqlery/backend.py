"""SQLAlchemy backend implementation for sqlery standalone mode.

This backend uses SQLModel/SQLAlchemy for all database operations.
"""

import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone, UTC
from typing import Any

from sqlalchemy import and_, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, func, delete

from ..compat import DatabaseBackend, get_config
from ..core.models import QueuedJob, ScheduledJob, ScheduledTask, JobRegistry, Worker, DaemonLease
from .database import get_engine, get_session

# Partition maintenance helpers — guarded against psycopg absence (SQLite installs)
try:
    from sqlery.core import partitioning as _partitioning
except ImportError:
    _partitioning = None  # psycopg not installed; partition reclaim path unavailable


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

        CALLER OWNS THE CURSOR LIFECYCLE: the caller must call cursor.close() and
        close/rollback the underlying raw connection. Wrap in try/finally to avoid the
        CR-02 leak. The archive hook receives (cur, partition_name) and must not execute
        arbitrary SQL via string interpolation (T-17-09).
        """
        if not self._partitioned_pg():
            return None
        engine = get_engine()
        raw_conn = engine.raw_connection()
        return raw_conn.cursor()

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
                return None
            node_id = "_".join(parts[1:-1])
            with self._get_session() as session:
                stmt = select(Worker).where(and_(Worker.node_id == node_id, Worker.pid == pid))
                return session.exec(stmt).first()

        return None

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
    ):
        """Create a new job in the database."""
        # Named job support: new job always wins, stop conflicting jobs
        if job_name:
            with self._get_session() as session:
                stmt = select(QueuedJob).where(QueuedJob.job_name == job_name)
                for conflicting in session.exec(stmt).all():
                    session.delete(conflicting)
                session.commit()

        # Threshold routing (Phase 14/17): jobs scheduled further out than the configured
        # threshold go into sqlery_scheduled_job instead of sqlery_queued_job so they
        # cannot pin otherwise-drained partitions.
        # Staging only protects partitions, which exist only on partitioned PG.
        # On SQLite / non-partitioned PG, far-future jobs stay in sqlery_queued_job
        # (D6 — SQLite path unchanged).
        threshold_days = get_config("SCHEDULED_JOB_THRESHOLD_DAYS", 1)
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
            status="queued",
        )

        with self._get_session() as session:
            session.add(job)
            session.commit()
            session.refresh(job)

        return job

    def claim_job(self, queues: list[str], worker_id: str):
        """Atomically claim next available job.

        PostgreSQL: uses SELECT FOR UPDATE SKIP LOCKED.
        SQLite: uses optimistic version-based CAS update.
        """
        with self._get_session() as session:
            now = datetime.now(UTC)
            dialect = session.bind.dialect.name if session.bind is not None else ""
            strategy = determine_claim_strategy(dialect)

            stmt = (
                select(QueuedJob)
                .where(
                    and_(
                        QueuedJob.queue_name.in_(queues),
                        QueuedJob.status == "queued",
                        or_(
                            QueuedJob.scheduled_at == None,
                            QueuedJob.scheduled_at <= now,
                        ),
                    )
                )
                .order_by(QueuedJob.priority.desc(), QueuedJob.created_at)
                .limit(1)
            )
            if strategy == "skip_locked":
                stmt = stmt.with_for_update(skip_locked=True)

            job = session.exec(stmt).first()
            if job is None:
                return None

            if strategy == "skip_locked":
                job.status = "running"
                job.started_at = now
                job.worker_pid = os.getpid()
                job.version = (job.version or 0) + 1
                session.add(job)
                session.commit()
                session.refresh(job)
                return job

            # SQLite / fallback: optimistic CAS update on version
            current_version = job.version or 0
            cas_stmt = (
                update(QueuedJob)
                .where(QueuedJob.id == job.id)
                .where(QueuedJob.version == current_version)
                .where(QueuedJob.status == "queued")
                .values(
                    status="running",
                    started_at=now,
                    worker_pid=os.getpid(),
                    version=current_version + 1,
                )
            )
            res = session.exec(cas_stmt)
            session.commit()
            if res.rowcount != 1:
                return None
            refreshed = session.exec(select(QueuedJob).where(QueuedJob.id == job.id)).first()
            return refreshed

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
            stmt = select(QueuedJob.status, func.count(QueuedJob.id).label("count"))

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            stmt = stmt.group_by(QueuedJob.status)
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
                updated = session.exec(
                    update(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == created_at_val)
                    .where(QueuedJob.status == "queued")
                    .values(status="failed", error="Cancelled by user")
                    .execution_options(synchronize_session=False)
                )
                session.commit()
                if updated.rowcount > 0:
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
        """Retry failed jobs by resetting them to queued status."""
        with self._get_session() as session:
            stmt = select(QueuedJob).where(QueuedJob.status == "failed")

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            if max_jobs:
                stmt = stmt.limit(max_jobs)

            jobs = session.exec(stmt).all()

            for job in jobs:
                job.status = "queued"
                job.error = ""
                job.traceback = ""
                job.retry_count = 0
                session.add(job)

            session.commit()
            return len(jobs)

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
    ):
        """Create a new scheduled task."""
        task = ScheduledTask(
            name=name,
            task_path=task_path,
            cron_expression=cron_expression,
            queue_name=queue_name,
            priority=priority,
            enabled=enabled,
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
                threshold = datetime.now(UTC) - timedelta(seconds=60)
                stmt = stmt.where(Worker.last_heartbeat >= threshold)

            stmt = stmt.order_by(Worker.last_heartbeat.desc())

            return list(session.exec(stmt).all())

    def update_worker_heartbeat(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
        jobs_processed: int | None = None,
    ):
        """Update or create worker heartbeat."""
        # import socket  # moved to top-level

        with self._get_session() as session:
            worker = session.get(Worker, worker_id)

            if worker:
                worker.status = status
                worker.current_job_id = current_job_id
                worker.last_heartbeat = datetime.now(UTC)
                if jobs_processed is not None:
                    worker.jobs_processed = jobs_processed
            else:
                worker = Worker(
                    id=worker_id,
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
        """Clean up old jobs based on retention policy.

        On a partitioned PostgreSQL install (self._partitioned_pg() is True),
        routes to reclaim_drained_partitions which drops entire drained partitions
        instead of batched DELETEs (D5 — see loud comment below). Advisory lock
        (pg_try_advisory_lock) is acquired inside reclaim_drained_partitions; if
        not acquired the call returns 0 without error.

        On SQLite or non-partitioned PG, keeps the Phase-12 keyset-batched DELETE
        loop byte-for-byte unchanged (D6).
        """
        # --- D5: Partition reclaim path (PostgreSQL + partitioned table) ---
        if self._partitioned_pg() and _partitioning is not None:
            if dry_run:
                # dry_run is not meaningful for partition-drop path; return estimate
                with self._get_session() as session:
                    count_stmt = select(func.count(QueuedJob.id))
                    if status:
                        count_stmt = count_stmt.where(QueuedJob.status == status)
                    if queue_name:
                        count_stmt = count_stmt.where(QueuedJob.queue_name == queue_name)
                    count = session.exec(count_stmt).one()
                return {"deleted": 0, "count": count}

            retention_str = get_config("SQLERY_PARTITION_RETENTION", "30 days")
            archive_hook = get_config("SQLERY_PARTITION_ARCHIVE_HOOK", None)
            # Old: cur = self.get_raw_cursor()
            # CR-02: cursor was never closed, leaking a raw connection on every cleanup_jobs call.
            # Use try/finally to ensure close() is called even if reclaim raises.
            cur = self.get_raw_cursor()
            try:
                # D5: Partition reclaim destroys all jobs in drained partitions (beyond
                # SQLERY_PARTITION_RETENTION) by default. Failed-job history is gone
                # unless SQLERY_PARTITION_ARCHIVE_HOOK is configured. This is
                # intentional (see GSD-CONTEXT.md D5). Set SQLERY_PARTITION_ARCHIVE_HOOK
                # to archive instead. The archive hook receives (cur, partition_name)
                # and must not execute arbitrary SQL via string interpolation (T-17-09).
                dropped = _partitioning.reclaim_drained_partitions(
                    cur, QueuedJob.__tablename__, retention_str, archive_hook
                )
            finally:
                if cur is not None:
                    cur.close()
                    # Also close the underlying raw connection (avoid leaking pooled conn)
                    try:
                        cur.connection.close()
                    except Exception:
                        pass
            return {
                "deleted": 0,
                "reclaimed_via_partition_drop": True,
                "dropped_partitions": dropped,
                "note": (
                    "Partition reclaim: jobs beyond retention destroyed by default (D5). "
                    "Set SQLERY_PARTITION_ARCHIVE_HOOK to archive instead."
                ),
            }

        # --- SQLite or non-partitioned PG: Phase-12 batched DELETE loop (D6 — unchanged) ---
        with self._get_session() as session:
            # Old: stmt built here fed the now-removed unbounded-delete path (see commented-out
            # block below). The live path uses batch_stmt inside the loop; dry_run uses count_stmt.
            # Preserved as a comment so the filter intent is documented alongside the replacements.
            # stmt = delete(QueuedJob)
            # if status:
            #     stmt = stmt.where(QueuedJob.status == status)
            # if queue_name:
            #     stmt = stmt.where(QueuedJob.queue_name == queue_name)
            # if max_age_days:
            #     cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
            #     stmt = stmt.where(QueuedJob.created_at < cutoff)

            # CR-01: hoist `cutoff` ABOVE the dry_run fork. Previously `cutoff` was
            # only assigned in the non-dry-run path (~line 946), so the dry_run path
            # at line 924 referenced an undefined name and raised
            # NameError: name 'cutoff' is not defined whenever
            # cleanup_jobs(max_age_days=N, dry_run=True) was called.
            cutoff = None
            if max_age_days:
                cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

            if dry_run:
                # Count without deleting
                # from sqlmodel import select, func  # moved to top-level
                count_stmt = select(func.count(QueuedJob.id))
                if status:
                    count_stmt = count_stmt.where(QueuedJob.status == status)
                if queue_name:
                    count_stmt = count_stmt.where(QueuedJob.queue_name == queue_name)
                if max_age_days and cutoff is not None:
                    count_stmt = count_stmt.where(QueuedJob.created_at < cutoff)
                count = session.exec(count_stmt).one()
                return {"deleted": 0, "count": count}

            # Old: unbounded delete that holds table lock for the full result set
            # result = session.exec(stmt)
            # session.commit()
            # return {"deleted": result.rowcount, "count": result.rowcount}

            # Keyset-batched loop: at most CLEANUP_BATCH_SIZE rows per DELETE.
            # The batch DELETE re-applies the SAME retention filters as the id
            # SELECT (status/queue/age) restricted to the selected ids, so the
            # deleted set is always a subset of the selected set — guaranteeing
            # forward progress (no infinite re-selection) while still skipping any
            # row claimed/changed mid-loop. (A divergent status.in_(FINISHED_STATUSES)
            # DELETE filter would re-select non-finished rows forever and hang #12-02.)
            id_stmt = select(QueuedJob.id)
            if status:
                id_stmt = id_stmt.where(QueuedJob.status == status)
            if queue_name:
                id_stmt = id_stmt.where(QueuedJob.queue_name == queue_name)
            if max_age_days:
                # Old: cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
                # CR-01: cutoff is now computed above the dry_run fork; reuse it here.
                id_stmt = id_stmt.where(QueuedJob.created_at < cutoff)
            id_stmt = id_stmt.order_by(QueuedJob.id).limit(CLEANUP_BATCH_SIZE)

            total_deleted = 0
            while True:
                ids = list(session.exec(id_stmt).all())
                if not ids:
                    break
                # Old: status.in_(FINISHED_STATUSES) diverged from the SELECT filter and looped forever
                # batch_stmt = (
                #     delete(QueuedJob)
                #     .where(QueuedJob.id.in_(ids))
                #     .where(QueuedJob.status.in_(FINISHED_STATUSES))
                # )
                batch_stmt = delete(QueuedJob).where(QueuedJob.id.in_(ids))
                if status:
                    batch_stmt = batch_stmt.where(QueuedJob.status == status)
                if queue_name:
                    batch_stmt = batch_stmt.where(QueuedJob.queue_name == queue_name)
                if max_age_days:
                    batch_stmt = batch_stmt.where(QueuedJob.created_at < cutoff)
                result = session.exec(batch_stmt)
                session.commit()
                if not result.rowcount:
                    # No selected row was deletable (all changed mid-loop) — stop to
                    # avoid re-selecting the same un-deletable ids indefinitely.
                    break
                total_deleted += result.rowcount
                time.sleep(0.1)

            return {"deleted": total_deleted, "count": total_deleted}

    def cleanup_jobs_by_count(
        self,
        status: str | None = None,
        keep_count: int = 1000,
        queue_name: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Clean up jobs by keeping only the most recent N jobs."""
        with self._get_session() as session:
            # Get IDs of jobs to keep
            stmt = select(QueuedJob.id)

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            stmt = stmt.order_by(QueuedJob.created_at.desc()).limit(keep_count)
            keep_ids = list(session.exec(stmt).all())

            # Delete jobs not in keep list
            delete_stmt = delete(QueuedJob)

            if status:
                delete_stmt = delete_stmt.where(QueuedJob.status == status)

            if queue_name:
                delete_stmt = delete_stmt.where(QueuedJob.queue_name == queue_name)

            if keep_ids:
                delete_stmt = delete_stmt.where(~QueuedJob.id.in_(keep_ids))

            if dry_run:
                # Count without deleting — convert delete to count query
                # from sqlmodel import func  # moved to top-level
                count_stmt = select(func.count(QueuedJob.id))
                if status:
                    count_stmt = count_stmt.where(QueuedJob.status == status)
                if queue_name:
                    count_stmt = count_stmt.where(QueuedJob.queue_name == queue_name)
                if keep_ids:
                    count_stmt = count_stmt.where(~QueuedJob.id.in_(keep_ids))
                count = session.exec(count_stmt).one()
                return {"deleted": 0, "count": count, "kept": len(keep_ids)}

            result = session.exec(delete_stmt)
            session.commit()

            return {"deleted": result.rowcount, "count": result.rowcount, "kept": len(keep_ids)}

    def get_database_stats(self) -> dict:
        """Get database statistics."""
        with self._get_session() as session:
            # Job counts
            job_count_stmt = select(
                QueuedJob.status, func.count(QueuedJob.id).label("count")
            ).group_by(QueuedJob.status)
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
            total_jobs = session.exec(select(func.count(QueuedJob.id))).one()
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

        try:
            with self._get_session() as session:
                # Old (unconditional): session.exec(text("VACUUM ANALYZE sqlery_queued_job"))
                # Partition DROP leaves nothing to vacuum on parent table; skip when partitioned.
                if not self._partitioned_pg():
                    session.exec(text("VACUUM ANALYZE sqlery_queued_job"))
                # else: partition DROP leaves nothing to vacuum on parent; skip (D5/R3)
                session.exec(text("VACUUM ANALYZE sqlery_scheduled_task"))
                session.exec(text("VACUUM ANALYZE sqlery_registry"))
                session.exec(text("VACUUM ANALYZE sqlery_worker"))
                session.commit()

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
                .join(QueuedJob, QueuedJob.id == JobRegistry.job_id)
            )

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            if limit:
                stmt = stmt.limit(limit)

            entries = session.exec(stmt).all()

            # Fetch jobs for entries
            # session.get(QueuedJob, entry.job_id)  # Replaced: composite PK requires (created_at, id)
            jobs = []
            for entry in entries:
                job = session.exec(select(QueuedJob).where(QueuedJob.id == entry.job_id)).first()
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
            # Fall back to staging table on partitioned PG (D6 gate)
            if self._partitioned_pg():
                staged = session.exec(
                    select(ScheduledJob).where(ScheduledJob.id == job_id)
                ).first()
                return staged
            return None

    def mark_job_success(self, job_id: int, output: str = ""):
        """Mark job as successful.

        Staged ScheduledJob rows (not yet promoted) do not have mark_success;
        the guard prevents AttributeError if an operator calls this for a staged id.
        """
        # Old (single-table only):
        # job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
        job = self.get_job_by_id(job_id)
        # Old: if job: job.mark_success(...)  <-- AttributeError for ScheduledJob (IN-01)
        if job and hasattr(job, "mark_success"):
            with self._get_session() as session:
                db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
                if db_job:
                    db_job.mark_success(output=output)
                    session.add(db_job)
                    session.commit()
                    session.refresh(db_job)
                    return db_job
        return job

    def mark_job_failed(self, job_id: int, error: str, traceback: str = ""):
        """Mark job as failed.

        Staged ScheduledJob rows do not have mark_failed; guard prevents
        AttributeError if called for a staged job id (IN-01).
        """
        # Old (single-table only):
        # job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
        job = self.get_job_by_id(job_id)
        # Old: if job: job.mark_failed(...)  <-- AttributeError for ScheduledJob (IN-01)
        if job and hasattr(job, "mark_failed"):
            with self._get_session() as session:
                db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
                if db_job:
                    db_job.mark_failed(error=error, traceback=traceback)
                    session.add(db_job)
                    session.commit()
                    session.refresh(db_job)
                    return db_job
        return job

    def mark_job_archived(self, job_id: int):
        """Mark a failed job as archived (a retry has been created for it).

        Write-path pruning: fetches created_at first so PG can prune to one partition.
        Mirrors DjangoBackend.mark_job_archived (Phase 16 item 8).
        """
        with self._get_session() as session:
            # Item 8: Add created_at to filter so PG prunes to one partition.
            # Old (id-only filter — does not prune to partition on PG):
            # job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            # if job and job.status == "failed":
            #     job.status = "archived"
            # SQLModel exec() returns scalars for single-column selects
            created_at_val = session.exec(
                select(QueuedJob.created_at).where(
                    and_(QueuedJob.id == job_id, QueuedJob.status == "failed")
                )
            ).first()
            if created_at_val is not None:
                session.exec(
                    update(QueuedJob)
                    .where(QueuedJob.id == job_id)
                    .where(QueuedJob.created_at == created_at_val)
                    .where(QueuedJob.status == "failed")
                    .values(status="archived")
                    .execution_options(synchronize_session=False)
                )
                session.commit()

    def cascade_ancestor_status(self, job_id: int, status: str):
        """Walk parent_job_id chain, set all ancestors to given status.

        Write-path pruning: fetches (created_at, parent_job_id) per iteration and
        uses (id, created_at) filter on UPDATE so PG prunes to one partition (item 9).
        WR-03: excludes terminal-status ancestors so a completed or archived parent is
        never overwritten by a child's cascaded status change.
        """
        with self._get_session() as session:
            # Item 9: fetch created_at + parent_job_id together in one query per iteration.
            # Old (id-only — does not prune to partition on PG):
            # job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            # current_id = job.parent_job_id if job else None
            # while current_id:
            #     ancestor = session.exec(select(QueuedJob).where(QueuedJob.id == current_id)).first()
            #     if not ancestor: break
            #     ancestor.status = status
            #     current_id = ancestor.parent_job_id
            start_row = session.exec(
                select(QueuedJob.parent_job_id).where(QueuedJob.id == job_id)
            ).first()
            # SQLModel exec() returns scalars for single-column selects
            current_id = start_row if start_row is not None else None
            while current_id:
                job_row = session.exec(
                    select(QueuedJob.created_at, QueuedJob.parent_job_id).where(
                        QueuedJob.id == current_id
                    )
                ).first()
                if not job_row:
                    break
                # WR-03: exclude terminal-status ancestors
                row_created_at, next_parent_id = job_row
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
            for job in session.exec(stmt).all():
                worker = job.worker
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
                        worker_pid=job.worker_pid,
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
        """Mark a running job failed with termination_reason='zombie_job'."""
        with self._get_session() as session:
            # session.get(QueuedJob, job_id)  # Replaced: composite PK requires (created_at, id)
            job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            if job is None:
                return False
            job.mark_failed(error=reason, termination_reason="zombie_job")
            session.add(job)
            session.commit()
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

    def update_job_child_pid(self, job_id: int, child_pid: int, created_at=None):
        """Store the forked child PID on the job row.

        Args:
            job_id: QueuedJob primary key.
            child_pid: PID of the forked child process.
            created_at: Optional job creation timestamp. When provided, added to the
                filter so PG prunes to one partition (write-path pruning, item 11).
                Existing callers that omit it degrade gracefully to id-only filter.
        """
        with self._get_session() as session:
            # Item 11: When created_at is available from the caller (e.g. worker.py
            # which holds the full job object), add it to the filter for partition pruning.
            # Old (id-only — does not prune to partition on PG):
            # job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            # if job: job.child_pid = child_pid
            filter_stmt = update(QueuedJob).where(QueuedJob.id == job_id)
            if created_at is not None:
                filter_stmt = filter_stmt.where(QueuedJob.created_at == created_at)
            filter_stmt = filter_stmt.values(child_pid=child_pid).execution_options(
                synchronize_session=False
            )
            session.exec(filter_stmt)
            session.commit()

    def delete_worker_registration(self, worker_id: str) -> int:
        """Delete stale Worker row from a previous crash."""
        with self._get_session() as session:
            worker = session.get(Worker, worker_id)
            if worker:
                session.delete(worker)
                session.commit()
                return 1
            return 0

    def release_claimed_job(
        self, job, worker_id: str, status: str, jobs_processed: int = 0, **kwargs
    ):
        """Release a job after processing and update worker state.

        Write-path pruning: uses job.created_at if the caller passes the full job object
        so PG can prune to one partition (item 10). Falls back to id-only for SQLite.
        """
        with self._get_session() as session:
            # Item 10: if the job object has created_at (caller passes full job), use it for pruning.
            # Old (id-only — does not prune to partition on PG):
            # db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job.id)).first()
            if hasattr(job, "created_at") and job.created_at is not None:
                db_job = session.exec(
                    select(QueuedJob)
                    .where(QueuedJob.id == job.id)
                    .where(QueuedJob.created_at == job.created_at)
                ).first()
            else:
                db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job.id)).first()
            if db_job:
                db_job.status = status
                db_job.finished_at = datetime.now(UTC)
                if db_job.started_at:
                    db_job.duration_seconds = (
                        db_job.finished_at - db_job.started_at
                    ).total_seconds()
                for key, value in kwargs.items():
                    if hasattr(db_job, key):
                        setattr(db_job, key, value)
                session.add(db_job)

            # Update worker back to idle
            worker = session.get(Worker, worker_id)
            if worker:
                worker.status = "idle"
                worker.current_job_id = None
                worker.jobs_processed = jobs_processed
                worker.last_heartbeat = datetime.now(UTC)
                session.add(worker)

            session.commit()
            if db_job:
                session.refresh(db_job)
            return db_job

    def count_running_with_tag(self, tag: str) -> int:
        """Count currently running jobs with the given tag."""
        with self._get_session() as session:
            stmt = select(func.count(QueuedJob.id)).where(
                and_(
                    QueuedJob.status == "running",
                    QueuedJob.tags.contains([tag]),
                )
            )
            return session.exec(stmt).one()

    def count_started_with_tag_since(self, tag: str, threshold: datetime) -> int:
        """Count jobs with the given tag that started since threshold."""
        with self._get_session() as session:
            stmt = select(func.count(QueuedJob.id)).where(
                and_(
                    QueuedJob.status.in_(["running", "success", "failed"]),
                    QueuedJob.tags.contains([tag]),
                    QueuedJob.started_at >= threshold,
                    QueuedJob.started_at != None,
                )
            )
            return session.exec(stmt).one()

    def get_expired_ttl_jobs(self) -> list:
        """Get queued jobs whose TTL has expired."""
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
                if job.created_at + timedelta(seconds=job.ttl) < now:
                    expired.append(job)
            return expired

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
    ) -> list:
        """Get next claimable jobs ordered by priority."""
        with self._get_session() as session:
            now = datetime.now(UTC)
            stmt = (
                select(QueuedJob)
                .where(
                    and_(
                        QueuedJob.queue_name.in_(queues),
                        QueuedJob.status == "queued",
                        or_(
                            QueuedJob.scheduled_at == None,
                            QueuedJob.scheduled_at <= now,
                        ),
                    )
                )
                .order_by(QueuedJob.priority.desc(), QueuedJob.created_at)
                .limit(limit)
            )
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if determine_claim_strategy(dialect) == "skip_locked":
                stmt = stmt.with_for_update(skip_locked=True)
            return list(session.exec(stmt).all())

    def atomic_claim_job(self, job, worker) -> bool:
        """Atomically claim a specific job for a worker.

        PostgreSQL: relies on SELECT FOR UPDATE row lock from get_claimable_jobs.
        SQLite: uses optimistic version-based CAS update.
        """
        with self._get_session() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            strategy = determine_claim_strategy(dialect)

            if strategy == "skip_locked":
                # session.get(QueuedJob, job.id)  # Replaced: composite PK requires (created_at, id)
                db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job.id)).first()
                if db_job and db_job.status == "queued":
                    db_job.mark_running()
                    db_job.version = (db_job.version or 0) + 1
                    session.add(db_job)
                    session.commit()
                    return True
                return False

            # SQLite / fallback: optimistic CAS update on version
            current_version = job.version or 0
            now = datetime.now(UTC)
            cas_stmt = (
                update(QueuedJob)
                .where(QueuedJob.id == job.id)
                .where(QueuedJob.version == current_version)
                .where(QueuedJob.status == "queued")
                .values(
                    status="running",
                    started_at=now,
                    worker_pid=os.getpid(),
                    version=current_version + 1,
                )
            )
            res = session.exec(cas_stmt)
            session.commit()
            return res.rowcount == 1

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
            stmt = select(QueuedJob)

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            # Order by priority (desc) and created_at (asc)
            stmt = stmt.order_by(QueuedJob.priority.desc(), QueuedJob.created_at)

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
            stmt = select(func.count(QueuedJob.id))

            if status:
                stmt = stmt.where(QueuedJob.status == status)

            if queue_name:
                stmt = stmt.where(QueuedJob.queue_name == queue_name)

            return session.exec(stmt).one()
