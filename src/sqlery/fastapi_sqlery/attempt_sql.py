"""SQL statements on ``sqlery_job_attempt`` shared by the sync and async SQLAlchemy backends.

Only statement builders live here (no session handling), so the sync backend executes them
with ``session.exec`` and the async backend with ``await session.execute`` and both write
exactly the same SQL. The decisions (accept or reject a close) are in ``sqlery.core.attempts``.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import and_, select, update
from sqlalchemy.orm.attributes import set_committed_value

from sqlery.core.attempts import (
    OUTCOME_ABANDONED,
    OUTCOME_SUCCESS,
    RUNNING,
    AttemptHandle,
    new_token,
)
from sqlery.core.models import JobAttempt, QueuedJob


def open_attempt_exists(job_id_column=None):
    """Correlated ``EXISTS``: an attempt is open for the job (``QueuedJob.id`` by default).

    A job is running iff its row is ``queued`` and this holds (#157): the claim writes nothing
    to the job row.
    """
    column = QueuedJob.id if job_id_column is None else job_id_column
    return (
        select(JobAttempt.id)
        .where(JobAttempt.job_id == column)
        .where(JobAttempt.finished_at.is_(None))
        .exists()
    )


def running_clause():
    """``WHERE`` clause for the running jobs of the live table."""
    return and_(QueuedJob.status == "queued", open_attempt_exists())


def waiting_clause():
    """``WHERE`` clause for the live jobs that are queued and not running."""
    return and_(QueuedJob.status == "queued", ~open_attempt_exists())


def running_with_attempts_stmt(queue_names=None):
    """``(job, open attempt)`` of every running job, newest attempt per job last."""
    stmt = (
        select(QueuedJob, JobAttempt)
        .join(JobAttempt, JobAttempt.job_id == QueuedJob.id)
        .where(JobAttempt.finished_at.is_(None))
        .where(QueuedJob.status == "queued")
        .order_by(JobAttempt.id)
    )
    if queue_names:
        stmt = stmt.where(QueuedJob.queue_name.in_(list(queue_names)))
    return stmt


def row_columns(attempt) -> dict:
    """The job-row columns a finished job records from its attempt (``{}`` without one)."""
    if attempt is None:
        return {}
    return {
        "started_at": attempt.claimed_at,
        "worker_id": attempt.worker_id,
        "worker_pid": attempt.worker_pid,
        "child_pid": attempt.child_pid,
    }


def overlay_running(job, attempt):
    """Show ``job`` as the running job its open ``attempt`` makes it, in memory only.

    ``set_committed_value`` records the values as loaded state, not as changes, so a later
    flush of this object never writes them to the row (the claim leaves the row alone and
    the derived values must not leak back into it). Returns ``job``.
    """
    if job is None or attempt is None:
        return job
    set_committed_value(job, "status", RUNNING)
    set_committed_value(job, "started_at", attempt.claimed_at)
    set_committed_value(job, "worker_id", attempt.worker_id)
    set_committed_value(job, "worker_pid", attempt.worker_pid)
    set_committed_value(job, "child_pid", attempt.child_pid)
    return job


def abandon_open_stmt(job_id, now: datetime):
    """Close every open attempt of ``job_id`` as ``abandoned``."""
    return (
        update(JobAttempt)
        .where(JobAttempt.job_id == job_id)
        .where(JobAttempt.finished_at.is_(None))
        .values(finished_at=now, outcome=OUTCOME_ABANDONED)
        .execution_options(synchronize_session=False)
    )


def close_exact_stmt(handle: AttemptHandle, outcome: str, now: datetime):
    """Compare-and-set close of exactly ``handle``'s attempt (rowcount 1 = accepted)."""
    return (
        update(JobAttempt)
        .where(JobAttempt.id == handle.id)
        .where(JobAttempt.token == handle.token)
        .where(JobAttempt.finished_at.is_(None))
        .values(finished_at=now, outcome=outcome)
        .execution_options(synchronize_session=False)
    )


def close_unfenced_stmt(job_id, outcome: str, now: datetime):
    """Close every open attempt of ``job_id`` for a caller that holds no handle.

    A success stays a success; anything else means the attempt was abandoned.
    """
    return (
        update(JobAttempt)
        .where(JobAttempt.job_id == job_id)
        .where(JobAttempt.finished_at.is_(None))
        .values(
            finished_at=now,
            outcome=OUTCOME_SUCCESS if outcome == OUTCOME_SUCCESS else OUTCOME_ABANDONED,
        )
        .execution_options(synchronize_session=False)
    )


def new_attempt(job_id, worker_id, now: datetime) -> JobAttempt:
    """The row inserted by a claim (token minted here)."""
    return JobAttempt(
        job_id=job_id,
        token=new_token(),
        worker_id=worker_id,
        worker_pid=os.getpid(),
        claimed_at=now,
    )


def open_attempts_stmt(job_ids):
    return (
        select(JobAttempt)
        .where(JobAttempt.job_id.in_(list(job_ids)))
        .where(JobAttempt.finished_at.is_(None))
        .order_by(JobAttempt.id)
    )


def child_pid_stmt(job_id, child_pid: int, attempt: AttemptHandle | None):
    stmt = update(JobAttempt).where(JobAttempt.finished_at.is_(None))
    if attempt is not None:
        stmt = stmt.where(JobAttempt.id == attempt.id).where(JobAttempt.token == attempt.token)
    else:
        stmt = stmt.where(JobAttempt.job_id == job_id)
    return stmt.values(child_pid=child_pid).execution_options(synchronize_session=False)
