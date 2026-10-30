"""Regression for #105: the sync worker must not hold every job for 0.5 s."""

import time

import pytest

from sqlery.core.job import job
from sqlery.core.worker import WorkerProcess


@job
def noop():
    return time.time()  # the job's own end time, for the diagnostics below


@job
def nap(seconds):
    time.sleep(seconds)
    return "slept"


@pytest.fixture
def backend(tmp_path):
    from sqlery import compat
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'floor.db'}")
    b = SQLAlchemyBackend()
    compat._backend = b
    yield b
    compat._backend = None


def _run_next(backend, worker):
    claimed = backend.claim_job(["default"], worker.worker_id)
    assert claimed is not None
    start = time.monotonic()
    result = worker._fork_and_execute(claimed)
    return result, time.monotonic() - start


def test_trivial_job_is_not_held_for_half_a_second(backend, monkeypatch):
    import os

    import sqlery.core.worker as worker_module

    # Diagnostics only (timing-neutral, behaviour-neutral): per job, when the child forked,
    # when the parent saw it done, whether pidfd_open worked, and every fd wait as
    # (woken, seconds waited). A failure message then shows whether the child was slow
    # (child_end - fork large) or the parent missed the wake (reaped - child_end large).
    marks = {"fork": None, "reaped": None, "pidfd_ok": None, "waits": []}
    worker = WorkerProcess(queues=["default"], backend=backend)

    real_fork = worker._fork_ctx.fork

    def spying_fork():
        marks["fork"] = time.time()
        return real_fork()

    monkeypatch.setattr(worker._fork_ctx, "fork", spying_fork)

    if hasattr(os, "pidfd_open"):
        real_pidfd_open = os.pidfd_open

        def spying_pidfd_open(pid, *args):
            try:
                fd = real_pidfd_open(pid, *args)
            except OSError:
                marks["pidfd_ok"] = False
                raise
            marks["pidfd_ok"] = True
            return fd

        monkeypatch.setattr(os, "pidfd_open", spying_pidfd_open)

    real_wait = worker_module._wait_readable

    def spying_wait(fd, timeout):
        start = time.monotonic()
        woken = real_wait(fd, timeout)
        if timeout > 0:  # skip the zero-timeout "anything to read?" probes
            marks["waits"].append((woken, round(time.monotonic() - start, 4)))
        return woken

    monkeypatch.setattr(worker_module, "_wait_readable", spying_wait)

    real_poll = WorkerProcess._poll_forked_child

    def spying_poll(child_pid):
        done, code = real_poll(child_pid)
        if done and marks["reaped"] is None:
            marks["reaped"] = time.time()
        return done, code

    monkeypatch.setattr(WorkerProcess, "_poll_forked_child", staticmethod(spying_poll))

    for _ in range(4):
        noop.enqueue()
    durations = []
    diagnostics = []
    for _ in range(4):
        marks.update(fork=None, reaped=None, pidfd_ok=None, waits=[])
        result, elapsed = _run_next(backend, worker)
        assert result.get("success"), result
        durations.append(elapsed)
        try:
            child_end = float(result.get("output"))
        except (TypeError, ValueError):
            child_end = None
        fork, reaped = marks["fork"], marks["reaped"]
        diagnostics.append(
            (
                None if child_end is None or fork is None else round(child_end - fork, 4),
                None if child_end is None or reaped is None else round(reaped - child_end, 4),
                marks["pidfd_ok"],
                list(marks["waits"]),
            )
        )
    # diagnostics per job: (child_end - fork, reaped - child_end, pidfd_ok, [(woken, waited)])
    assert max(durations) < 0.25, (durations, diagnostics)


def test_longer_job_still_completes_and_runs_duties(backend, monkeypatch):
    nap.enqueue(seconds=1.2)
    worker = WorkerProcess(queues=["default"], backend=backend)
    renews = []
    worker._owned_queues = {"default"}
    monkeypatch.setattr(
        backend, "renew_queue_leases", lambda *a, **k: renews.append(time.monotonic())
    )
    result, elapsed = _run_next(backend, worker)
    assert result.get("success"), result
    assert result["output"]
    assert elapsed >= 1.2
    # Lease renewal stays at the ~0.5 s cadence, not once per fast early tick.
    assert 1 <= len(renews) <= 3, renews
