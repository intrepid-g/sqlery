"""Workflow primitives: ``chain``, ``group``, ``chord`` and task signatures.

Built on the existing job ``dependencies`` mechanism, so there is no schema change and the
same code runs in Django and standalone modes:

* a step with dependencies is only claimed once every dependency has **succeeded**; if one
  fails (after its retries) the step fails too, which stops the rest of the chain;
* a step that is not *immutable* receives its upstream result(s) as leading argument(s):
  ``chain`` passes the previous result, a ``chord`` body gets the list of header results.

    from sqlery import chain, group, chord

    res = (add.s(1, 2) | add.s(10) | add.si(0)).apply_async()       # chain
    res = group(add.s(i, i) for i in range(5)).apply_async()        # group
    res = chord(group(add.s(i, i) for i in range(5)), total.s()).apply_async()
    res.get(timeout=30)

All jobs of a workflow are created up front. If creating one fails part-way, the earlier
ones stay queued (there is no cross-job transaction); treat that as an enqueue error.
"""

from __future__ import annotations

from typing import Any, Iterable

from .result import AsyncResult, TaskFailedError, apply_async_job

__all__ = ["Signature", "chain", "group", "chord", "GroupResult", "signature"]


def _defaults(task: Any) -> dict[str, Any]:
    """Enqueue defaults of a ``@job`` wrapper (core or Django flavour)."""
    if hasattr(task, "queue_name"):  # django_sqlery.decorators.JobFunction
        return dict(
            queue=task.queue_name, priority=task.priority, max_retries=task.max_retries,
            retry_backoff=task.retry_backoff, allow_parallel=task.allow_parallel,
            timeout_seconds=task.timeout, tags=task.tags,
        )
    return dict(  # core.job.JobWrapper
        queue=task.queue, priority=task.priority, max_retries=task.max_retries,
        retry_backoff=task.retry_backoff, allow_parallel=task.allow_parallel,
        timeout_seconds=task.timeout_seconds, tags=task.tags,
    )


def _payload(task: Any, args: tuple, kwargs: dict, lead: int) -> dict:
    """Stored kwargs for a step. ``lead`` positional slots are reserved for upstream results
    that the executor prepends at run time."""
    if hasattr(task, "_bind_partial"):  # Django wrapper stores arguments by name
        import inspect

        sig = inspect.signature(task.func)
        params = list(sig.parameters.values())
        if task.bind:
            params = params[1:]
        sig = sig.replace(parameters=params[lead:])
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    payload = dict(kwargs)
    if args:
        payload["_args"] = list(args)
    return payload


class Signature:
    """A task plus bound arguments/options, not yet enqueued. Build with ``task.s(...)``."""

    def __init__(self, task: Any, args: tuple = (), kwargs: dict | None = None,
                 options: dict | None = None, immutable: bool = False):
        self.task = task
        self.args = tuple(args)
        self.kwargs = dict(kwargs or {})
        self.options = dict(options or {})
        self.immutable = immutable

    def set(self, **options: Any) -> "Signature":
        """Override ``queue``, ``priority``, ``countdown``, ``eta``, ``expires``, ... for this step."""
        return Signature(self.task, self.args, self.kwargs, {**self.options, **options}, self.immutable)

    def clone(self) -> "Signature":
        return Signature(self.task, self.args, self.kwargs, self.options, self.immutable)

    def __or__(self, other: Any) -> "chain":
        return chain(self, other)

    def _enqueue(self, depends_on: list[int] | None = None, pass_result: str | None = None):
        lead = 0 if self.immutable or not pass_result else 1
        opts = {**_defaults(self.task), **self.options}
        meta = {"canvas": {"pass_result": pass_result}} if lead else None
        return apply_async_job(
            self.task.task_path, _payload(self.task, self.args, self.kwargs, lead),
            dependencies=depends_on, meta=meta, **opts,
        )

    def apply_async(self) -> AsyncResult:
        return self._enqueue()

    delay = apply_async
    __call__ = apply_async

    def __repr__(self) -> str:
        return f"<Signature {self.task.task_path}{'!' if self.immutable else ''}>"


def signature(task: Any, *args: Any, immutable: bool = False, **kwargs: Any) -> Signature:
    return Signature(task, args, kwargs, immutable=immutable)


class GroupResult:
    """Handle on a set of independent jobs."""

    def __init__(self, results: list[AsyncResult]):
        self.results = results

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def ready(self) -> bool:
        return all(r.ready() for r in self.results)

    def successful(self) -> bool:
        return all(r.successful() for r in self.results)

    def failed(self) -> bool:
        return any(r.failed() for r in self.results)

    def get(self, timeout: float | None = None, interval: float = 0.2, propagate: bool = True) -> list:
        """Results in submission order. ``timeout`` is shared across the whole group."""
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        out = []
        for r in self.results:
            left = None if deadline is None else max(deadline - time.monotonic(), 0)
            out.append(r.get(timeout=left, interval=interval, propagate=propagate))
        return out

    def __repr__(self) -> str:
        return f"<GroupResult: {[r.id for r in self.results]}>"


class group:
    """Run several signatures independently (in parallel, subject to workers)."""

    def __init__(self, *signatures: Signature | Iterable[Signature]):
        if len(signatures) == 1 and not isinstance(signatures[0], Signature):
            signatures = tuple(signatures[0])
        self.tasks = list(signatures)

    def __or__(self, other: Any) -> "chain":
        return chain(self, other)

    def _enqueue(self, depends_on: list[int] | None = None, pass_result: str | None = None):
        return GroupResult([s._enqueue(depends_on, pass_result) for s in self.tasks])

    def apply_async(self) -> GroupResult:
        return self._enqueue()

    delay = apply_async
    __call__ = apply_async


class chain:
    """Run steps in order; each starts after the previous one (or group) succeeded."""

    def __init__(self, *steps: Any):
        flat: list[Any] = []
        for step in steps:
            flat.extend(step.steps if isinstance(step, chain) else [step])
        if not flat:
            raise ValueError("chain needs at least one step")
        self.steps = flat

    def __or__(self, other: Any) -> "chain":
        return chain(self, other)

    def apply_async(self) -> AsyncResult | GroupResult:
        prev_ids: list[int] | None = None
        prev_single = False
        last: Any = None
        for step in self.steps:
            mode = None
            if prev_ids:
                mode = "single" if prev_single else "list"
            result = step._enqueue(prev_ids, mode)
            if isinstance(result, GroupResult):
                prev_ids, prev_single = [r.id for r in result], False
            else:
                prev_ids, prev_single = [result.id], True
            if last is not None:
                result.parent = last
            last = result
        return last

    delay = apply_async
    __call__ = apply_async


class chord:
    """Run a header group, then call ``body`` with the list of header results."""

    def __init__(self, header: group | Iterable[Signature], body: Signature):
        self.header = header if isinstance(header, group) else group(*header)
        self.body = body

    def apply_async(self) -> AsyncResult:
        header = self.header.apply_async()
        res = self.body._enqueue([r.id for r in header], "list")
        res.parent = header
        return res

    delay = apply_async
    __call__ = apply_async
