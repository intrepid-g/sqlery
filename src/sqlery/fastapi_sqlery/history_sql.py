"""SQL on ``sqlery_job_history`` for the SQLAlchemy backends (#140).

Only statement builders and tiny pure helpers live here (no session handling), so the sync
backend executes them with ``session.exec`` and the async backend with
``await session.execute``, and both write exactly the same SQL. The decisions are in
``sqlery.core.history``.

The terminal transition is two statements in the caller's transaction::

    INSERT INTO sqlery_job_history (...) SELECT ... FROM sqlery_queued_job WHERE <job>
    DELETE FROM sqlery_queued_job WHERE <job>

where ``<job>`` is the job's key plus the fences the caller asks for (``version``, status).
Both must affect exactly one row (:func:`moved`); anything else means the completion is
stale and the caller rolls the transaction back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, literal, or_
from sqlmodel import select

from sqlery.core.history import TERMINAL_STATUSES
from sqlery.core.models import JOB_COLUMNS, JobAll, JobHistory, QueuedJob

_LIVE = QueuedJob.__table__
_HIST = JobHistory.__table__


def _where(job_id, created_at=None, expected_version=None, require_status=None):
    conds = [_LIVE.c.id == job_id]
    if created_at is not None:
        conds.append(_LIVE.c.created_at == created_at)
    if expected_version is not None:
        conds.append(_LIVE.c.version == expected_version)
    if require_status is not None:
        if isinstance(require_status, (list, tuple, set, frozenset)):
            conds.append(_LIVE.c.status.in_(list(require_status)))
        else:
            conds.append(_LIVE.c.status == require_status)
    return and_(*conds)


def move_statements(
    job_id: int,
    status: str,
    values: dict[str, Any] | None = None,
    *,
    created_at=None,
    expected_version: int | None = None,
    require_status=None,
    now: datetime | None = None,
):
    """``(lock, insert, delete)`` statements that move one live job into history.

    ``values`` are columns that differ from the live row at the moment of the transition
    (``output``, ``error``, ``runs``, ``termination_reason`` ...). The history row's version
    is the live version plus one (the transition is a state change like any other), and its
    ``finished_at`` is the given value, else the row's own, else ``now``.
    """
    values = dict(values or {})
    now = now or datetime.now(UTC)
    exprs: dict[str, Any] = {
        "status": literal(status, type_=_LIVE.c.status.type),
        "version": _LIVE.c.version + 1,
    }
    if "finished_at" in values:
        exprs["finished_at"] = literal(values.pop("finished_at"), type_=_LIVE.c.finished_at.type)
    else:
        exprs["finished_at"] = func.coalesce(
            _LIVE.c.finished_at, literal(now, type_=_LIVE.c.finished_at.type)
        )
    for name, value in values.items():
        if name in ("status", "version"):
            continue
        exprs[name] = literal(value, type_=_LIVE.c[name].type)
    select_list = [exprs.get(name, _LIVE.c[name]).label(name) for name in JOB_COLUMNS]
    where = _where(job_id, created_at, expected_version, require_status)
    ins = (
        insert(_HIST)
        .from_select(list(JOB_COLUMNS), select(*select_list).where(where))
        .returning(_HIST.c.id)
    )
    dele = delete(_LIVE).where(where)
    # Row lock first (PostgreSQL; a no-op on SQLite, whose single writer needs none): a
    # concurrent writer of the same job finishes or is waited for, and the two statements
    # below then see the same row.
    lock = select(_LIVE.c.id).where(where).with_for_update()
    return lock, ins, dele


def moved(insert_result, delete_result) -> bool:
    """True iff the transition touched exactly one row in each table (the insert's row count
    is not reliable for INSERT .. SELECT on every driver, so it reports the id it wrote)."""
    return len(insert_result.all()) == 1 and delete_result.rowcount == 1


def history_by_id_stmt(job_id: int):
    return select(JobHistory).where(JobHistory.id == job_id)


def anywhere_by_id_stmt(job_id: int):
    """The job whether live or finished (read-only view row)."""
    return select(JobAll).where(JobAll.id == job_id)


def anywhere_in_stmt(job_ids):
    return select(JobAll).where(JobAll.id.in_(list(job_ids)))


def latest_retry_stmt(job_id: int):
    """Newest retry child of ``job_id``, live or finished."""
    return (
        select(JobAll)
        .where(JobAll.parent_job_id == job_id)
        .order_by(JobAll.created_at.desc(), JobAll.id.desc())
        .limit(1)
    )


def has_retry_child_stmt(job_id: int):
    return select(JobAll.id).where(JobAll.parent_job_id == job_id).limit(1)


def stray_terminal_stmt(limit: int, cutoff: datetime):
    """Terminal rows still in the live table that finished before ``cutoff`` (settle sweep).

    The grace period keeps the sweep from racing a completion that is still in flight (a
    failure the worker is about to retry is marked in place for a moment).
    """
    return (
        select(QueuedJob)
        .where(QueuedJob.status.in_(TERMINAL_STATUSES))
        .where(~QueuedJob.termination_reason.like("retry_pending%"))
        .where(or_(QueuedJob.finished_at.is_(None), QueuedJob.finished_at <= cutoff))
        .order_by(QueuedJob.created_at, QueuedJob.id)
        .limit(limit)
    )
