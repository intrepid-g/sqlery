"""Celery-style task signals: ``task_prerun``, ``task_postrun``, ``task_success``,
``task_failure``, ``task_retry``.

    from sqlery.core.signals import task_failure

    @task_failure.connect
    def alert(sender, job=None, exc=None, **kw):
        ...

``sender`` is the task path (``"myapp.tasks.send_email"``); pass ``sender=`` to ``connect`` to
receive only that task. Receivers run in the process executing the job (the forked child in
daemon/worker mode), so register them at import time of the module that defines the task or
in code the worker imports. A receiver that raises is logged and never fails the job.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "Signal",
    "task_prerun",
    "task_postrun",
    "task_success",
    "task_failure",
    "task_retry",
]


class Signal:
    """Minimal synchronous dispatcher (strong references, optional sender filter)."""

    def __init__(self, name: str):
        self.name = name
        self._receivers: list[tuple[Callable, str | None]] = []
        self._lock = threading.Lock()

    def connect(self, receiver: Callable | None = None, *, sender: str | None = None):
        """Register ``receiver``; usable as ``@signal.connect`` or ``@signal.connect(sender=...)``."""

        def register(fn: Callable) -> Callable:
            with self._lock:
                if not any(r is fn and s == sender for r, s in self._receivers):
                    self._receivers.append((fn, sender))
            return fn

        return register(receiver) if receiver is not None else register

    def disconnect(self, receiver: Callable, *, sender: str | None = None) -> bool:
        with self._lock:
            before = len(self._receivers)
            self._receivers = [(r, s) for r, s in self._receivers if not (r is receiver and s == sender)]
            return len(self._receivers) != before

    def send(self, sender: str, **kwargs: Any) -> int:
        """Call every matching receiver; returns how many ran. Never raises."""
        with self._lock:
            receivers = list(self._receivers)
        called = 0
        for receiver, wanted in receivers:
            if wanted is not None and wanted != sender:
                continue
            try:
                receiver(sender=sender, signal=self, **kwargs)
            except Exception:
                logger.exception(f"{self.name} receiver {receiver!r} raised; ignoring")
            called += 1
        return called

    def __repr__(self) -> str:
        return f"<Signal {self.name}: {len(self._receivers)} receivers>"


task_prerun = Signal("task_prerun")    # job, args, kwargs
task_postrun = Signal("task_postrun")  # job, args, kwargs, state, retval
task_success = Signal("task_success")  # job, result
task_failure = Signal("task_failure")  # job, exc, traceback
task_retry = Signal("task_retry")      # job, retry_job, exc
