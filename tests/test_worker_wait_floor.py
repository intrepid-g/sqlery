"""Regression for #105: the sync worker must not hold every job for 0.5 s."""

import time

import pytest

from sqlery.core.job import job
from sqlery.core.worker import WorkerProcess


@job
def noop():
    return "done"


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


def test_trivial_job_is_not_held_for_half_a_second(backend):
    for _ in range(4):
        noop.enqueue()
    worker = WorkerProcess(queues=["default"], backend=backend)
    durations = []
    for _ in range(4):
        result, elapsed = _run_next(backend, worker)
        assert result.get("success"), result
        durations.append(elapsed)
    assert max(durations) < 0.25, durations


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
