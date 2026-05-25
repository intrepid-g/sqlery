"""Compatibility layer for RQ (Redis Queue).

Permanent first-class feature — drop-in replacement for RQ. Change only imports
to migrate from RQ to SQLery.

    # Before (RQ + django-tasks-scheduler)
    from rq import Retry
    from scheduler.queues import get_queue
    from rq import get_current_job

    # After (SQLery)
    from sqlery.compat.rq import Retry, get_queue, get_current_job
"""

import logging
import random
from datetime import timedelta, datetime, UTC
from typing import Any, Callable

from sqlery.core.utils import import_task
from sqlery.django_sqlery.models import Worker as _Worker

from sqlery.compat.scheduler import Retry, get_current_job, JobStatus
from sqlery.django_sqlery.models import QueuedJob
from sqlery.django_sqlery.queue import Queue as _DjangoQueue
from sqlery.django_sqlery.backend import DjangoBackend as _DjangoBackend


def _make_django_queue(name: str) -> _DjangoQueue:
    """Instantiate a Django-backed Queue with DjangoBackend."""
    return _DjangoQueue(name, backend=_DjangoBackend())

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RQ-compatible Queue class
# ---------------------------------------------------------------------------

class Queue:
    """Drop-in replacement for RQ's Queue class.

    Accepts RQ-style kwargs in enqueue() and translates them to sqlery's
    native Queue API so migrating projects need only change imports.


    """

    def __init__(self, name: str = "default", default_timeout: int | None = None, **_ignored):
        """Initialise a Queue wrapping sqlery's Django Queue.

        Args:
            name: Queue name (default: 'default').
            default_timeout: Default timeout in seconds (optional).
            **_ignored: Any additional RQ kwargs (e.g. connection) are silently dropped.
        """
        self.name = name
        self.default_timeout = default_timeout
        self._q = _make_django_queue(name)

    # --- Internal helpers ---

    def _map_rq_kwargs(self, kwargs: dict) -> dict:
        """Translate RQ enqueue kwargs to sqlery kwargs in-place.

        Handles:
        - job_id → job_name
        - timeout → timeout_seconds
        - retry=Retry(...) → max_retries, retry_backoff / retry_intervals
        - at_front=True → priority=100
        - args=[...] → _args (positional serialisation)
        - result_ttl, failure_ttl, description → silently dropped
        """
        mapped: dict[str, Any] = {}

        # Positional args provided as kwarg (RQ style: queue.enqueue(fn, args=[1, 2]))
        rq_args = kwargs.pop("args", None)
        if rq_args is not None:
            mapped["_args"] = list(rq_args)

        # job_id → job_name
        job_id = kwargs.pop("job_id", None)
        if job_id is not None:
            mapped["job_name"] = str(job_id)

        # timeout → timeout_seconds (fall back to default_timeout)
        timeout = kwargs.pop("timeout", self.default_timeout)
        if timeout is not None:
            mapped["timeout_seconds"] = timeout

        # retry= Retry(...) → max_retries + retry_backoff / retry_intervals
        retry: Retry | None = kwargs.pop("retry", None)
        if retry is not None:
            mapped["max_retries"] = retry.max
            if len(retry.intervals) == 1:
                mapped["retry_backoff"] = retry.intervals[0]
            else:
                mapped["retry_intervals"] = retry.intervals

        # at_front → priority
        if kwargs.pop("at_front", False):
            mapped["priority"] = 100

        # Silently drop RQ-only kwargs
        for dropped in ("result_ttl", "failure_ttl", "description", "ttl"):
            kwargs.pop(dropped, None)

        # meta: pass through to QueuedJob after creation (handled at call site)
        # Keep remaining kwargs as task kwargs
        mapped.update(kwargs)
        return mapped

    # --- Public API ---

    def enqueue(self, func: Callable, *args, **kwargs) -> QueuedJob:
        """Enqueue a job immediately.

        Accepts all standard RQ enqueue kwargs (job_id, retry, at_front,
        timeout, result_ttl, etc.) and translates them to sqlery.

        Args:
            func: Callable to enqueue.
            *args: Positional arguments forwarded to func.
            **kwargs: RQ-style options + keyword arguments for func.

        Returns:
            QueuedJob instance.
        """
        meta = kwargs.pop("meta", None)
        mapped = self._map_rq_kwargs(kwargs)

        # Positional args from *args take precedence over args= kwarg
        if args and "_args" not in mapped:
            mapped["_args"] = list(args)
        elif args:
            mapped["_args"] = list(args) + mapped["_args"]

        job = self._q.enqueue(func, **mapped)

        if meta:
            job.meta = dict(meta)
            job.save_meta()

        return job

    def enqueue_in(self, delay: timedelta, func: Callable, *args, **kwargs) -> QueuedJob:
        """Enqueue a job after a timedelta delay.

        """
        meta = kwargs.pop("meta", None)
        mapped = self._map_rq_kwargs(kwargs)
        if args:
            mapped.setdefault("_args", list(args))
        job = self._q.enqueue_in(delay, func, **mapped)
        if meta:
            job.meta = dict(meta)
            job.save_meta()
        return job

    def enqueue_at(self, when: datetime, func: Callable, *args, **kwargs) -> QueuedJob:
        """Enqueue a job at a specific datetime.

        """
        meta = kwargs.pop("meta", None)
        mapped = self._map_rq_kwargs(kwargs)
        if args:
            mapped.setdefault("_args", list(args))
        job = self._q.enqueue_at(when, func, **mapped)
        if meta:
            job.meta = dict(meta)
            job.save_meta()
        return job

    def __repr__(self) -> str:
        return f"<rq.compat.Queue '{self.name}'>"


# ---------------------------------------------------------------------------
# Module-level utility functions (replacing utils/rq.py)
# ---------------------------------------------------------------------------

def get_queue(name: str = "default") -> Queue:
    """Return an RQ-compatible Queue instance.

    Drop-in for ``scheduler.queues.get_queue``.

    Args:
        name: Queue name (default: 'default').

    Returns:
        Queue wrapper instance.
    """
    return Queue(name)


def get_job_registry_summary(queue_name: str) -> dict[str, list[int]]:
    """Return job IDs grouped by status for the given queue.

    Drop-in for RQ's StartedJobRegistry / FinishedJobRegistry enumeration.


    Args:
        queue_name: Name of the queue to inspect.

    Returns:
        Dict with keys 'started', 'finished', 'failed', 'scheduled', 'queued',
        each containing a list of job PKs.
    """
    qs = QueuedJob.objects.filter(queue_name=queue_name).values("id", "status", "scheduled_at")
    summary: dict[str, list[int]] = {
        "started": [],
        "finished": [],
        "failed": [],
        "scheduled": [],
        "queued": [],
    }
    for row in qs:
        status = row["status"]
        pk = row["id"]
        if status == "running":
            summary["started"].append(pk)
        elif status == "success":
            summary["finished"].append(pk)
        elif status == "failed":
            summary["failed"].append(pk)
        elif status == "queued" and row.get("scheduled_at"):
            summary["scheduled"].append(pk)
        else:
            summary["queued"].append(pk)
    return summary


def clear_failed_jobs(queue_name: str) -> int:
    """Delete all failed jobs in the given queue.



    Args:
        queue_name: Name of the queue to clear.

    Returns:
        Number of jobs deleted.
    """
    count, _ = QueuedJob.objects.filter(queue_name=queue_name, status="failed").delete()
    return count


def delete_other_jobs_by_same_meta_tag(current_job_id: int, meta_tag: str) -> int:
    """Cancel all non-running jobs sharing the same meta['tag'] value.

    Args:
        current_job_id: PK of the currently-executing job (excluded from cancellation).
        meta_tag: Value of meta['tag'] to match.

    Returns:
        Number of jobs cancelled.
    """
    cancelled = 0
    candidates = QueuedJob.objects.filter(
        status="queued",
    ).exclude(pk=current_job_id)

    for job in candidates:
        if (job.meta or {}).get("tag") == meta_tag:
            job.status = "cancelled"
            job.save(update_fields=["status"])
            cancelled += 1

    return cancelled


def is_final_retry(job: QueuedJob) -> bool:
    """Return True if this is the last retry attempt for the given job.

    Args:
        job: QueuedJob instance currently executing.

    Returns:
        True if no more retries will occur after this attempt.
    """
    return job.retry_count >= job.max_retries


def get_queue_wait_time(queue_name: str) -> int:
    """Return seconds since the oldest queued job was enqueued.

    Returns 0 if the queue is empty.


    Args:
        queue_name: Name of the queue to measure.

    Returns:
        Integer seconds since oldest queued job was created (0 if empty).
    """
    oldest = (
        QueuedJob.objects.filter(queue_name=queue_name, status="queued")
        .order_by("created_at")
        .values("created_at")
        .first()
    )
    if oldest is None:
        return 0
    delta = datetime.now(UTC) - oldest["created_at"]
    return max(0, int(delta.total_seconds()))


def requeue_if_jobs_pending(
    current_job: QueuedJob,
    min_delay: int = 5,
    max_delay: int = 20,
    **override_kwargs,
) -> bool:
    """Re-enqueue the current job with a random delay if the queue is busy.

    If there are other queued jobs ahead of ``current_job``, this function
    re-enqueues it with a random delay between ``min_delay`` and ``max_delay``
    seconds and returns True. Returns False if the queue is empty/only has the
    current job, allowing normal processing to proceed.


    Args:
        current_job: The QueuedJob currently being executed.
        min_delay: Minimum re-queue delay in seconds (default: 5).
        max_delay: Maximum re-queue delay in seconds (default: 20).
        **override_kwargs: Extra kwargs passed to enqueue_in() (e.g. priority).

    Returns:
        True if the job was re-enqueued (caller should return early).
        False if the queue is clear and the job should proceed normally.
    """
    pending_count = QueuedJob.objects.filter(
        queue_name=current_job.queue_name,
        status="queued",
    ).exclude(pk=current_job.pk).count()

    if pending_count == 0:
        return False

    delay_seconds = random.randint(min_delay, max_delay)
    q = _make_django_queue(current_job.queue_name)

    # from sqlery.django_sqlery.utils import import_task  # Promoted to core
    # from sqlery.core.utils import import_task  # moved to top-level
    func = import_task(current_job.task_path)

    enqueue_kwargs: dict[str, Any] = {
        "max_retries": current_job.max_retries,
        "retry_backoff": current_job.retry_backoff,
        "priority": current_job.priority,
    }
    if current_job.job_name:
        enqueue_kwargs["job_name"] = current_job.job_name
    if current_job.meta:
        enqueue_kwargs["meta"] = current_job.meta
    enqueue_kwargs.update(override_kwargs)

    task_kwargs = dict(current_job.kwargs or {})
    q.enqueue_in(timedelta(seconds=delay_seconds), func, **task_kwargs, **enqueue_kwargs)
    return True


# ---------------------------------------------------------------------------
# RQ stub classes — allow callers that reference rq.Job, rq.Worker, or
# rq.exceptions.NoSuchJobError to import from the compat layer instead.
# These are thin wrappers around QueuedJob; they cover the surface area
# actually used by the cjpia codebase (fetch, delete, Worker.all).
# ---------------------------------------------------------------------------


class NoSuchJobError(Exception):
    """Drop-in for rq.exceptions.NoSuchJobError."""


class Job:
    """Minimal rq.job.Job compat backed by QueuedJob."""

    def __init__(self, queued_job: QueuedJob):
        self._qj = queued_job

    # -- rq-compatible properties --
    @property
    def id(self):
        return str(self._qj.pk)

    @property
    def meta(self):
        return self._qj.meta or {}

    @property
    def description(self):
        return self._qj.job_name or self._qj.task_path

    def get_id(self):
        return self.id

    def get_status(self):
        return self._qj.status

    def delete(self):
        self._qj.delete()

    @classmethod
    def fetch(cls, job_id, connection=None):
        """Fetch a job by ID. Raises NoSuchJobError if not found."""
        try:
            qj = QueuedJob.objects.get(pk=job_id)
        except (QueuedJob.DoesNotExist, ValueError):
            raise NoSuchJobError(f"No such job: {job_id}")
        return cls(qj)


class Worker:
    """Minimal rq.Worker compat — only supports Worker.all().

    RQ workers are separate OS processes that poll Redis; sqlery workers
    are daemon threads (or management command processes) that poll the
    database.  Worker.all() in RQ discovers processes via Redis keys;
    here it queries the sqlery Worker ORM model instead.
    """

    @classmethod
    def all(cls, connection=None):
        """Return all active sqlery workers (connection kwarg ignored)."""
        # from sqlery.django_sqlery.models import Worker as _Worker  # moved to top-level

        return list(_Worker.objects.filter(status__in=["idle", "busy"]))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Re-exports from scheduler compat
    "Retry",
    "get_current_job",
    "JobStatus",
    # RQ Queue wrapper
    "Queue",
    "get_queue",
    # RQ stub classes
    "Job",
    "Worker",
    "NoSuchJobError",
    # Utility functions
    "get_job_registry_summary",
    "clear_failed_jobs",
    "delete_other_jobs_by_same_meta_tag",
    "is_final_retry",
    "get_queue_wait_time",
    "requeue_if_jobs_pending",
]
