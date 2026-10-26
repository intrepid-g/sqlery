"""SQL on ``sqlery_job_ready`` / ``sqlery_job_blocked`` for the SQLAlchemy backends (#139).

Only statement builders live here (no session handling), so the sync backend executes them
with ``session.exec`` and the async backend with ``await session.execute`` and both write
exactly the same SQL. The decisions are in ``sqlery.core.ready``.

It also registers the session hook that keeps the ready table in step with every ORM write
of a ``QueuedJob`` (a new queued job, a status change, a priority / schedule edit, a delete),
so the many code paths that add or change jobs through the ORM cannot forget it. Core
``UPDATE`` statements do not pass through the ORM; the backend handles those explicitly.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from sqlalchemy import and_, delete, event, inspect, or_, select, text, update
from sqlalchemy.orm import Session as _SASession

from .models import (
    JobBlocked,
    JobReady,
    QueuedJob,
    _generate_job_id_from_uuid7,
)
from . import rotation
from .ready import SLOT_CURRENT, SLOTS, dependency_ids

_EDITABLE = ("queue_name", "priority", "scheduled_at")


def _insert(dialect_name: str, table):
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - other dialects are not supported by sqlery
        from sqlalchemy import insert
    return insert(table)


def ready_values(job: Any, slot: int = SLOT_CURRENT) -> dict:
    """Column values of the ready entry for ``job`` (a QueuedJob-like object)."""
    return {
        "slot": slot,
        "job_id": job.id,
        "queue_name": job.queue_name,
        "priority": job.priority or 0,
        "created_at": job.created_at,
        "scheduled_at": job.scheduled_at,
    }


def _rotation_enabled() -> bool:
    from sqlery.compat import get_config

    return rotation.rotation_enabled(get_config)


def write_ready(target, dialect_name: str, jobs) -> None:
    """Insert the ready entries of ``jobs`` through a Session or Connection.

    With rotation on (PostgreSQL) the shared generation lock is taken first and the slot read
    in the same transaction; otherwise slot 0 and no lock, as before #141.
    """
    jobs = list(jobs)
    if not jobs:
        return
    run = target.exec if hasattr(target, "exec") else target.execute
    slot = rotation.writer_slot(
        lambda sql: run(text(sql)).scalar(), _rotation_enabled(), dialect_name
    )
    run(insert_ready_stmt(dialect_name, [ready_values(j, slot) for j in jobs]))


async def awrite_ready(session, dialect_name: str, jobs) -> None:
    """``write_ready`` for an async session."""
    jobs = list(jobs)
    if not jobs:
        return

    async def read_one(sql):
        return (await session.execute(text(sql))).scalar()

    slot = await rotation.awriter_slot(read_one, _rotation_enabled(), dialect_name)
    await session.execute(insert_ready_stmt(dialect_name, [ready_values(j, slot) for j in jobs]))


def insert_ready_stmt(dialect_name: str, rows: list[dict]):
    """Idempotent insert of ready entries (an existing entry is left alone)."""
    stmt = _insert(dialect_name, JobReady.__table__).values(rows)
    if dialect_name in ("postgresql", "sqlite"):
        stmt = stmt.on_conflict_do_nothing(index_elements=["slot", "job_id"])
    return stmt


def insert_blocked_stmt(dialect_name: str, job: Any, now: datetime | None = None):
    """Idempotent insert of one blocked row per dependency of ``job``."""
    now = now or datetime.now(UTC)
    rows = [
        {"job_id": job.id, "dependency_id": dep, "created_at": job.created_at, "blocked_at": now}
        for dep in dependency_ids(job)
    ]
    if not rows:
        return None
    stmt = _insert(dialect_name, JobBlocked.__table__).values(rows)
    if dialect_name in ("postgresql", "sqlite"):
        stmt = stmt.on_conflict_do_nothing(index_elements=["job_id", "dependency_id"])
    return stmt


def delete_entry_stmt(slot: int, job_id: int):
    """Delete exactly one ready entry (rowcount 1 = the caller owns the claim)."""
    return (
        delete(JobReady)
        .where(JobReady.slot == slot)
        .where(JobReady.job_id == job_id)
        .execution_options(synchronize_session=False)
    )


def delete_ready_stmt(job_ids):
    """Delete the ready entries of ``job_ids`` from every slot."""
    return (
        delete(JobReady)
        .where(JobReady.slot.in_(SLOTS))
        .where(JobReady.job_id.in_(list(job_ids)))
        .execution_options(synchronize_session=False)
    )


def delete_blocked_stmt(job_ids):
    return (
        delete(JobBlocked)
        .where(JobBlocked.job_id.in_(list(job_ids)))
        .execution_options(synchronize_session=False)
    )


def update_ready_stmt(job_id: int, values: dict):
    return (
        update(JobReady)
        .where(JobReady.slot.in_(SLOTS))
        .where(JobReady.job_id == job_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def next_entry_stmt(queues, now: datetime, after=None, lock: bool = False, limit: int = 1):
    """Next claimable entries in claim order, strictly after the keyset cursor ``after``.

    ``after`` is ``(priority, created_at, job_id)`` of the last entry inspected. Paging by
    cursor (not by an ever-growing exclusion list) means entries skipped as tag-limited are
    never revisited within one claim and the scan always makes progress.
    """
    t = JobReady
    stmt = (
        select(t)
        .where(t.queue_name.in_(list(queues)))
        .where(or_(t.scheduled_at.is_(None), t.scheduled_at <= now))
    )
    if after is not None:
        priority, created_at, job_id = after
        stmt = stmt.where(
            or_(
                t.priority < priority,
                and_(
                    t.priority == priority,
                    or_(
                        t.created_at > created_at,
                        and_(t.created_at == created_at, t.job_id > job_id),
                    ),
                ),
            )
        )
    stmt = stmt.order_by(t.priority.desc(), t.created_at, t.job_id).limit(limit)
    if lock:
        stmt = stmt.with_for_update(skip_locked=True)
    return stmt


def job_of_entry_stmt(entry: JobReady):
    """The job row under a ready entry, by primary key ``(created_at, id)``."""
    return (
        select(QueuedJob)
        .where(QueuedJob.id == entry.job_id)
        .where(QueuedJob.created_at == entry.created_at)
    )


def next_due_stmt(queues, now: datetime):
    from sqlalchemy import func

    return select(func.min(JobReady.scheduled_at)).where(
        JobReady.queue_name.in_(list(queues)), JobReady.scheduled_at > now
    )


# --------------------------------------------------------------------------------------------
# Session hook: ORM writes of QueuedJob keep the ready table in step
# --------------------------------------------------------------------------------------------


def _drop_entries(conn, job_ids) -> None:
    ids = [i for i in job_ids if i is not None]
    if ids:
        conn.execute(delete_ready_stmt(ids))
        conn.execute(delete_blocked_stmt(ids))


def _ensure_entry(conn, job) -> None:
    dialect = conn.dialect.name
    write_ready(conn, dialect, [job])
    conn.execute(
        update_ready_stmt(
            job.id,
            {
                "queue_name": job.queue_name,
                "priority": job.priority or 0,
                "scheduled_at": job.scheduled_at,
            },
        )
    )


@event.listens_for(_SASession, "before_flush")
def _sync_ready_with_jobs(session, flush_context, instances):
    """Insert / update / delete ready entries for the QueuedJob rows this flush writes.

    Runs after ``_assign_composite_pk_ids`` (registered first), so new jobs have their id.
    The statements join the flush's transaction: a job and its entry commit or roll back
    together.
    """
    new = [o for o in session.new if isinstance(o, QueuedJob)]
    dirty = [o for o in session.dirty if isinstance(o, QueuedJob)]
    gone = [o for o in session.deleted if isinstance(o, QueuedJob)]
    if not (new or dirty or gone):
        return
    conn = session.connection()
    for job in new:
        if job.id is None:
            job.id = _generate_job_id_from_uuid7()
        if job.status == "queued":
            _ensure_entry(conn, job)
    for job in dirty:
        if job.id is None:
            continue
        state = inspect(job)
        if state.attrs.status.history.has_changes():
            if job.status == "queued":
                _ensure_entry(conn, job)
            else:
                _drop_entries(conn, [job.id])
        elif job.status == "queued" and any(
            state.attrs[k].history.has_changes() for k in _EDITABLE
        ):
            conn.execute(
                update_ready_stmt(
                    job.id,
                    {
                        "queue_name": job.queue_name,
                        "priority": job.priority or 0,
                        "scheduled_at": job.scheduled_at,
                    },
                )
            )
    _drop_entries(conn, [o.id for o in gone])
