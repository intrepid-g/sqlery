"""Test helpers: put a job into the running state the way a claim does (#157).

A claim leaves the job row alone: the job keeps ``status='queued'``, loses its ready entry and
gets an open attempt. Tests that need a running job (to exercise recovery, stats, cancel...)
build exactly that instead of writing ``status='running'`` to the row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


def django_make_running(job, started_at=None, worker=None, worker_pid=None, child_pid=None):
    """Make an existing Django ``QueuedJob`` running (no ready entry, one open attempt).

    ``worker`` is a Worker row (or None); returns the job reloaded (shown running).
    """
    from sqlery.django_sqlery import ready
    from sqlery.django_sqlery.models import JobAttempt

    ready.remove([job.id])
    JobAttempt.objects.create(
        job_id=job.id,
        token=str(uuid.uuid4()),
        worker_id=getattr(worker, "id", None),
        worker_pid=worker_pid,
        child_pid=child_pid,
        claimed_at=started_at or _now(),
    )
    job.refresh_from_db()
    return job


def django_running_job(**fields):
    """Create a Django ``QueuedJob`` that is running. ``started_at``, ``worker``, ``worker_pid``
    and ``child_pid`` go to the attempt (the job row carries none of them); the rest are job
    fields."""
    from sqlery.django_sqlery.models import QueuedJob

    attempt = {
        k: fields.pop(k) for k in ("started_at", "worker", "worker_pid", "child_pid") if k in fields
    }
    fields.pop("status", None)
    job = QueuedJob.objects.create(status="queued", **fields)
    return django_make_running(job, **attempt)


def sa_make_running(backend, job_id, started_at=None, worker_id=None, worker_pid=None):
    """Make a standalone job running through the backend's own claim-free path: delete its
    ready entry and open an attempt (what ``claim_job`` writes besides the entry delete)."""
    from sqlery.core import ready_sql
    from sqlery.fastapi_sqlery import attempt_sql

    with backend._get_session() as session:
        session.exec(ready_sql.delete_ready_stmt([job_id]))
        row = attempt_sql.new_attempt(job_id, worker_id, started_at or _now())
        if worker_pid is not None:
            row.worker_pid = worker_pid
        session.add(row)
        session.commit()
    return backend.get_job_by_id(job_id)
