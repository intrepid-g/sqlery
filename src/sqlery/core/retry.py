"""Celery-style retry policy: ``autoretry_for``, ``Retry`` / ``self.retry()``, ``bind=True``.

Policy is declared on the task function (``@job(autoretry_for=..., retry_backoff_max=...,
retry_jitter=..., bind=True)``) and read by the executors when a job fails, so it applies
uniformly to ``enqueue`` / ``delay`` / ``apply_async`` / scheduled runs with no extra storage.
Jobs whose task declares none of these behave exactly as before.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "Retry",
    "TaskContext",
    "RetryPolicy",
    "get_retry_policy",
    "policy_allows_retry",
    "retry_countdown",
    "shape_retry_delay",
    "invoke_task",
]


class Retry(Exception):
    """Ask the executor to retry the current job (Celery's ``Retry``).

    Raise it from a task, or call ``self.retry(...)`` on a ``bind=True`` task. It still
    honours ``max_retries``: with no retries left the job fails (carrying ``exc``'s message).

    Args:
        exc: Underlying exception that caused the retry (its message is recorded).
        countdown: Seconds to wait before the next attempt (overrides the backoff).
        message: Text used when ``exc`` is not given.
    """

    def __init__(
        self,
        exc: BaseException | None = None,
        countdown: float | None = None,
        message: str | None = None,
    ):
        self.exc = exc
        self.countdown = countdown
        text = message or (f"{type(exc).__name__}: {exc}" if exc is not None else "Retry requested")
        super().__init__(text)


@dataclass
class TaskContext:
    """What a ``bind=True`` task receives as its first argument (Celery's ``self``)."""

    id: Any = None
    task_path: str = ""
    retries: int = 0
    max_retries: int = 0
    queue: str = ""
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_job(cls, job: Any) -> "TaskContext":
        return cls(
            id=getattr(job, "id", None),
            task_path=getattr(job, "task_path", "") or "",
            retries=getattr(job, "retry_count", 0) or 0,
            max_retries=getattr(job, "max_retries", 0) or 0,
            queue=getattr(job, "queue_name", "") or "",
            meta=getattr(job, "meta", None) or {},
        )

    @property
    def request(self) -> "TaskContext":
        """Celery-compatible alias: ``self.request.retries`` works."""
        return self

    def retry(
        self,
        exc: BaseException | None = None,
        countdown: float | None = None,
        message: str | None = None,
    ):
        """Raise :class:`Retry`. Never returns."""
        raise Retry(exc=exc, countdown=countdown, message=message)


@dataclass(frozen=True)
class RetryPolicy:
    autoretry_for: tuple[type, ...] | None = None  # None = retry on any exception (legacy)
    backoff_max: float | None = None
    jitter: bool | None = None  # None = fall back to the RETRY_JITTER config


def get_retry_policy(task_func: Any) -> RetryPolicy:
    """Read the policy declared on a task function (``@job(...)`` attributes)."""
    af = getattr(task_func, "autoretry_for", None)
    return RetryPolicy(
        autoretry_for=tuple(af) if af else None,
        backoff_max=getattr(task_func, "retry_backoff_max", None),
        jitter=getattr(task_func, "retry_jitter", None),
    )


def _global_jitter() -> bool:
    try:
        from sqlery.compat import get_config

        return bool(get_config("RETRY_JITTER", False))
    except Exception:
        return False


def _load_task(job: Any):
    try:
        from .utils import import_task

        return import_task(job.task_path)
    except Exception:
        return None


def policy_allows_retry(job: Any, exc: BaseException | None) -> bool:
    """Whether the *exception* permits a retry (``max_retries`` is checked separately).

    - explicit :class:`Retry` -> always yes
    - task declares ``autoretry_for`` -> only for matching exceptions
    - otherwise -> yes (legacy behaviour: any failure retries while attempts remain)
    """
    if isinstance(exc, Retry):
        return True
    task = _load_task(job)
    if task is None:
        return True
    policy = get_retry_policy(task)
    if policy.autoretry_for is None:
        return True
    return exc is not None and isinstance(exc, policy.autoretry_for)


def retry_countdown(exc: BaseException | None) -> float | None:
    """Explicit delay requested via ``Retry(countdown=...)``, if any."""
    return exc.countdown if isinstance(exc, Retry) else None


def shape_retry_delay(delay: float, job: Any, *, countdown: float | None = None) -> float:
    """Apply per-task ``retry_backoff_max`` / ``retry_jitter`` (explicit countdown wins)."""
    if countdown is not None:
        return float(countdown)
    task = _load_task(job)
    policy = get_retry_policy(task) if task is not None else RetryPolicy()
    if policy.backoff_max:
        delay = min(delay, float(policy.backoff_max))
    if policy.jitter and not _global_jitter():
        delay = random.uniform(0, delay)  # (global RETRY_JITTER already jittered upstream)
    return delay


def invoke_task(task_func: Any, job: Any, args: tuple | list, kwargs: dict) -> Any:
    """Call the task, injecting a :class:`TaskContext` first when it is ``bind=True``."""
    if getattr(task_func, "bind", False):
        inner = getattr(task_func, "func", task_func)
        return inner(TaskContext.from_job(job), *args, **kwargs)
    return task_func(*args, **kwargs)
