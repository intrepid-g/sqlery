"""Celery-style result handle (``AsyncResult``) and ``apply_async`` plumbing.

Works in both Django and standalone modes by going through the active
:class:`~sqlery.compat.DatabaseBackend`. No schema changes: results live in the
job's ``output`` column, JSON-encoded by :func:`serialize_result`.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AsyncResult",
    "TaskFailedError",
    "apply_async_job",
    "serialize_result",
    "deserialize_result",
]

# sqlery job status -> Celery-style state name
_STATE_MAP = {
    "queued": "PENDING",
    "running": "STARTED",
    "shutting_down": "STARTED",
    "success": "SUCCESS",
    "failed": "FAILURE",
    "archived": "RETRY",
}
_READY_STATES = frozenset({"SUCCESS", "FAILURE"})


class TaskFailedError(Exception):
    """Raised by :meth:`AsyncResult.get` when the task ended in failure."""

    def __init__(self, message: str, traceback: str = ""):
        super().__init__(message)
        self.traceback = traceback


def serialize_result(result: Any) -> str:
    """Encode a task return value for the ``output`` column.

    ``None`` -> ``""``; strings are stored verbatim (unchanged from before); everything
    else is JSON (``default=str`` so odd types degrade instead of failing the job). Also
    fixes falsy results (``0``, ``False``, ``[]``) being dropped by ``result or ""``.
    """
    if result is None:
        return ""
    if inspect.isawaitable(result):
        # Leave it for mark_success's reject_unawaited_coroutine guard to catch.
        return result  # type: ignore[return-value]
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


def deserialize_result(output: Any) -> Any:
    """Best-effort inverse of :func:`serialize_result`.

    A string result that is itself valid JSON (e.g. ``"123"``) decodes to that value; read
    ``AsyncResult.output`` for the raw text.
    """
    if output is None or output == "":
        return None
    if not isinstance(output, str):
        return output
    try:
        return json.loads(output)
    except ValueError:
        return output


class AsyncResult:
    """Handle on an enqueued job: ``state``, ``ready()``, ``get()``.

    Follows retries: if the job failed and a retry was created, the handle reports on the
    latest attempt, like Celery.
    """

    _MAX_RETRY_HOPS = 1000

    def __init__(self, job_id: int, backend: Any = None):
        self.id = job_id
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None:
            from sqlery.compat import get_backend

            self._backend = get_backend()
        return self._backend

    def _job(self):
        job = self.backend.get_job_by_id(self.id)
        hops = 0
        # A failed/archived attempt with a retry child: report on the newest attempt.
        while job is not None and getattr(job, "status", None) in ("archived", "failed"):
            child = self.backend.get_latest_retry(job.id)
            hops += 1
            if child is None or hops > self._MAX_RETRY_HOPS:
                break
            job = child
        return job

    def exists(self) -> bool:
        return self.backend.get_job_by_id(self.id) is not None

    @property
    def state(self) -> str:
        job = self._job()
        if job is None:
            return "PENDING"  # Celery semantics: unknown ids look pending
        # Staged (far-future) jobs have no status attribute yet.
        return _STATE_MAP.get(getattr(job, "status", "queued"), "PENDING")

    status = state

    def ready(self) -> bool:
        return self.state in _READY_STATES

    def successful(self) -> bool:
        return self.state == "SUCCESS"

    def failed(self) -> bool:
        return self.state == "FAILURE"

    @property
    def output(self) -> str | None:
        """Raw stored output text (``None`` until the task succeeds)."""
        job = self._job()
        if job is None or getattr(job, "status", None) != "success":
            return None
        return getattr(job, "output", "") or ""

    @property
    def result(self) -> Any:
        """Decoded return value on success, else ``None``."""
        out = self.output
        return None if out is None else deserialize_result(out)

    @property
    def error(self) -> str | None:
        job = self._job()
        if job is not None and getattr(job, "status", None) == "failed":
            return getattr(job, "error", "") or ""
        return None

    @property
    def traceback(self) -> str | None:
        job = self._job()
        if job is not None and getattr(job, "status", None) == "failed":
            return getattr(job, "traceback", "") or ""
        return None

    def get(self, timeout: float | None = None, interval: float = 0.2, propagate: bool = True):
        """Block until the job finishes and return its result.

        Args:
            timeout: Seconds to wait; ``None`` waits forever.
            interval: Poll interval in seconds.
            propagate: Raise :class:`TaskFailedError` on failure; otherwise return the error text.

        Raises:
            TimeoutError: ``timeout`` elapsed before the job finished.
            TaskFailedError: The job failed and ``propagate`` is true.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            state = self.state
            if state == "SUCCESS":
                return self.result
            if state == "FAILURE":
                if propagate:
                    raise TaskFailedError(self.error or "task failed", self.traceback or "")
                return self.error
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"Job {self.id} not ready after {timeout}s (state={state})")
            time.sleep(interval)

    def __repr__(self) -> str:
        return f"<AsyncResult: {self.id}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AsyncResult) and other.id == self.id

    def __hash__(self) -> int:
        return hash(("AsyncResult", self.id))


def apply_async_job(
    task_path: str,
    payload: dict,
    *,
    queue: str | None = None,
    priority: int | None = None,
    max_retries: int | None = None,
    retry_backoff: float | None = None,
    allow_parallel: bool | None = None,
    timeout_seconds: int | None = None,
    tags: list[str] | None = None,
    countdown: float | None = None,
    eta: datetime | None = None,
    expires: float | datetime | None = None,
    backend: Any = None,
) -> AsyncResult:
    """Create a job with Celery-style scheduling options and return its :class:`AsyncResult`.

    ``countdown`` (seconds) / ``eta`` (datetime) delay execution; ``expires`` (seconds or
    datetime) maps to the job ``ttl`` so a job still queued past it is failed instead of run.
    """
    from sqlery.compat import get_backend, get_config

    if countdown is not None and eta is not None:
        raise ValueError("Specify either countdown or eta, not both")

    now = datetime.now(timezone.utc)
    scheduled_at = None
    if eta is not None:
        scheduled_at = eta if eta.tzinfo else eta.replace(tzinfo=timezone.utc)
    elif countdown is not None:
        scheduled_at = now + timedelta(seconds=float(countdown))

    ttl = None
    if expires is not None:
        if isinstance(expires, datetime):
            expires = (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) - now
            expires = expires.total_seconds()
        ttl = max(1, int(expires))

    backend = backend or get_backend()
    extra: dict[str, Any] = {}
    if tags:
        extra["tags"] = list(tags)
    if ttl is not None:
        extra["ttl"] = ttl
    job = backend.create_job(
        task_path=task_path,
        kwargs=payload,
        queue_name=queue if queue is not None else get_config("DEFAULT_QUEUE", "default"),
        priority=priority if priority is not None else get_config("DEFAULT_PRIORITY", 0),
        scheduled_at=scheduled_at,
        max_retries=max_retries if max_retries is not None else get_config("DEFAULT_MAX_RETRIES", 0),
        retry_backoff=(
            retry_backoff if retry_backoff is not None else get_config("DEFAULT_RETRY_BACKOFF", 1.0)
        ),
        allow_parallel=bool(allow_parallel),
        timeout_seconds=timeout_seconds,
        **extra,
    )
    return AsyncResult(job.id, backend=backend)
