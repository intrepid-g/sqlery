"""SQL statements on ``sqlery_job_attempt`` shared by the sync and async SQLAlchemy backends.

Only statement builders live here (no session handling), so the sync backend executes them
with ``session.exec`` and the async backend with ``await session.execute`` and both write
exactly the same SQL. The decisions (accept or reject a close) are in ``sqlery.core.attempts``.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import select, update

from sqlery.core.attempts import (
    OUTCOME_ABANDONED,
    OUTCOME_SUCCESS,
    AttemptHandle,
    new_token,
)
from sqlery.core.models import JobAttempt


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
