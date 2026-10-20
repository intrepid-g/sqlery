"""Single call site that actually runs a task body for both executors.

Adds, around the user's function: ``bind=True`` context injection, the soft time limit, and
the ``task_prerun`` / ``task_success`` / ``task_failure`` / ``task_postrun`` signals.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal
import traceback as tb
from typing import Any

from .retry import TaskContext
from .signals import task_failure, task_postrun, task_prerun, task_success

logger = logging.getLogger(__name__)

__all__ = ["SoftTimeLimitExceeded", "ainvoke_task", "invoke_task"]


class SoftTimeLimitExceeded(Exception):
    """Raised inside a task when its ``soft_time_limit`` elapses.

    Catch it to clean up; the hard limit (the job's ``timeout_seconds``) still fires
    afterwards if the task keeps running.
    """


def _arm_soft_limit(task_func: Any, job: Any) -> None:
    """Two-phase SIGALRM: soft limit raises SoftTimeLimitExceeded, then the remainder of
    the hard limit raises TimeoutError (same error the executors already produce)."""
    soft = getattr(task_func, "soft_time_limit", None)
    if not soft or not hasattr(signal, "setitimer"):
        return
    hard = getattr(job, "timeout_seconds", None)
    if hard and soft >= hard:
        return  # the hard limit fires first anyway

    state = {"soft_fired": False}

    def handler(signum, frame):
        if not state["soft_fired"]:
            state["soft_fired"] = True
            if hard:
                signal.setitimer(signal.ITIMER_REAL, max(hard - soft, 0.001))
            raise SoftTimeLimitExceeded(f"Job {job.id} exceeded soft time limit of {soft} seconds")
        raise TimeoutError(f"Job {job.id} exceeded timeout of {hard} seconds")

    try:
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, float(soft))
    except ValueError:
        # Not the main thread: signals unavailable, same limitation as the hard timeout.
        logger.warning(f"Job {job.id}: soft_time_limit ignored (not in main thread)")


def _with_upstream_results(job: Any, args: tuple | list) -> tuple:
    """Prepend upstream results for canvas steps (chain/chord body).

    ``meta["canvas"]["pass_result"]`` is ``"single"`` (previous result as first argument) or
    ``"list"`` (list of all dependency results, in dependency order, as first argument).
    """
    meta = getattr(job, "meta", None)
    mode = (meta.get("canvas") or {}).get("pass_result") if isinstance(meta, dict) else None
    if not mode:
        return tuple(args)
    from .result import AsyncResult

    results = [AsyncResult(dep).result for dep in (getattr(job, "dependencies", None) or [])]
    lead = results[0] if mode == "single" and results else results
    return (lead, *args)


def invoke_task(task_func: Any, job: Any, args: tuple | list, kwargs: dict) -> Any:
    """Run the task and emit lifecycle signals. Re-raises task exceptions unchanged."""
    args = _with_upstream_results(job, args)
    sender = getattr(job, "task_path", "") or ""
    task_prerun.send(sender, job=job, args=tuple(args), kwargs=dict(kwargs))
    try:
        _arm_soft_limit(task_func, job)
        if getattr(task_func, "bind", False):
            inner = getattr(task_func, "func", task_func)
            result = inner(TaskContext.from_job(job), *args, **kwargs)
        else:
            result = task_func(*args, **kwargs)
    except BaseException as exc:
        task_failure.send(sender, job=job, exc=exc, traceback=tb.format_exc())
        task_postrun.send(
            sender, job=job, args=tuple(args), kwargs=dict(kwargs), state="FAILURE", retval=exc
        )
        raise
    task_success.send(sender, job=job, result=result)
    task_postrun.send(
        sender, job=job, args=tuple(args), kwargs=dict(kwargs), state="SUCCESS", retval=result
    )
    return result


async def ainvoke_task(task_func: Any, job: Any, args: tuple | list, kwargs: dict) -> Any:
    """Async counterpart of :func:`invoke_task` for the AsyncWorker.

    Same upstream-result injection, ``bind=True`` context and lifecycle signals, but the task
    body is awaited (coroutine functions) or off-loaded to a thread (sync functions), and
    blocking DB lookups never run on the event loop. The soft time limit is not armed: it relies
    on SIGALRM, which would interrupt every other in-flight job on the loop.
    """
    meta = getattr(job, "meta", None)
    if isinstance(meta, dict) and (meta.get("canvas") or {}).get("pass_result"):
        args = await asyncio.to_thread(_with_upstream_results, job, args)
    else:
        args = tuple(args)
    sender = getattr(job, "task_path", "") or ""
    task_prerun.send(sender, job=job, args=args, kwargs=dict(kwargs))
    try:
        inner = getattr(task_func, "func", task_func)
        call_args = (
            (TaskContext.from_job(job), *args) if getattr(task_func, "bind", False) else args
        )
        if inspect.iscoroutinefunction(inner):
            result = await inner(*call_args, **kwargs)
        else:
            result = await asyncio.to_thread(inner, *call_args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
    except BaseException as exc:
        task_failure.send(sender, job=job, exc=exc, traceback=tb.format_exc())
        task_postrun.send(
            sender, job=job, args=args, kwargs=dict(kwargs), state="FAILURE", retval=exc
        )
        raise
    task_success.send(sender, job=job, result=result)
    task_postrun.send(
        sender, job=job, args=args, kwargs=dict(kwargs), state="SUCCESS", retval=result
    )
    return result
