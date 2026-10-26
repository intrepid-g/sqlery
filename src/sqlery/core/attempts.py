"""Execution attempts: one fenced row per claim of a job (issue #138).

Every claim of a job inserts a row into ``sqlery_job_attempt`` carrying a fresh random
token. The claimed job object carries the resulting :class:`AttemptHandle` in memory, and
the completion write (success, failure or retry) closes exactly that attempt in the same
transaction as the job update::

    UPDATE sqlery_job_attempt SET finished_at=now(), outcome=:o
     WHERE id=:id AND token=:t AND finished_at IS NULL

A rowcount other than 1 means the attempt was already closed (finished before, or
abandoned by recovery), so the completion is stale: :class:`JobFencingError` is raised
and the job row is not touched. The job's ``version`` CAS remains a second fence.

This module holds the framework-agnostic parts (handle, outcomes, the decision on a close
result, the start-up guard). The SQL lives in the two backends, behind the
``DatabaseBackend`` attempt methods.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..compat import JobFencingError
from ..tables import JOB_ATTEMPT

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_ABANDONED = "abandoned"
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_ABANDONED)

ATTEMPT_TABLE = JOB_ATTEMPT

# Attribute under which a claim stores its handle on the in-memory job object.
_HANDLE_ATTR = "_sqlery_attempt"

MISSING_TABLE_MESSAGE = (
    f"The execution attempt table '{ATTEMPT_TABLE}' does not exist, so this worker refuses to "
    "start. Run the database migrations (Django: 'python manage.py migrate sqlery'; "
    "standalone: 'sqlery-migrate' / 'alembic upgrade head'). Stop all running workers "
    "before migrating: a worker started before the table existed cannot be fenced."
)


class AttemptTableMissingError(RuntimeError):
    """The attempt table is missing: migrations have not been applied."""


@dataclass(frozen=True)
class AttemptHandle:
    """Identity of one open execution attempt (what a completion must present)."""

    id: int
    token: str
    job_id: int


@dataclass(frozen=True)
class AttemptInfo:
    """Read view of an attempt row (used by kill, zombie detection and the dashboard)."""

    id: int
    job_id: int
    worker_id: Any
    worker_pid: int | None
    child_pid: int | None
    claimed_at: datetime | None


def new_token() -> str:
    """A fresh unguessable attempt token (36 characters)."""
    return str(uuid.uuid4())


def attach_attempt(job: Any, handle: AttemptHandle | None) -> Any:
    """Carry ``handle`` on the in-memory ``job`` and return the job."""
    if handle is not None and job is not None:
        object.__setattr__(job, _HANDLE_ATTR, handle)
    return job


def attempt_of(job: Any) -> AttemptHandle | None:
    """The attempt this claimed job object carries, or None (e.g. never claimed)."""
    return getattr(job, _HANDLE_ATTR, None)


def accepts_attempt(fn: Any) -> bool:
    """Whether the backend method ``fn`` takes the ``attempt`` keyword."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "attempt" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def attempt_kwargs(job_or_handle: Any, fn: Any = None) -> dict:
    """``{"attempt": handle}`` for a backend call, or ``{}`` when there is nothing to pass.

    Nothing is passed when there is no handle, or when ``fn`` (the backend method about to be
    called) predates attempts and does not take the keyword: custom ``DatabaseBackend``
    subclasses keep working, unfenced as before.
    """
    handle = (
        job_or_handle if isinstance(job_or_handle, AttemptHandle) else attempt_of(job_or_handle)
    )
    if handle is None or (fn is not None and not accepts_attempt(fn)):
        return {}
    return {"attempt": handle}


def decide_close(rowcount: int, handle: AttemptHandle) -> None:
    """Accept the completion (``rowcount == 1``) or reject it as stale.

    Raises:
        JobFencingError: the attempt was already closed or abandoned (or never existed), so
            the caller is superseded and must not touch the job row.
    """
    if rowcount != 1:
        raise JobFencingError(
            f"Attempt {handle.id} of job {handle.job_id} is no longer open "
            "(already finished or abandoned by recovery); completion rejected"
        )


def require_attempt_table(backend: Any) -> None:
    """Refuse to start a worker on a database that has no attempt table.

    Raises:
        AttemptTableMissingError: with the migration instructions.
    """
    check = getattr(backend, "attempt_table_exists", None)
    if check is None:
        return
    if not check():
        raise AttemptTableMissingError(MISSING_TABLE_MESSAGE)
