"""Regression for #57: the forked worker child must run jobs through invoke_task."""

import time

import pytest

from sqlery import chord, group
from sqlery.core.job import job
from sqlery.core.worker import WorkerProcess


@job
def one():
    return 1


@job
def total(results):
    return sum(results)


@job(max_retries=2, retry_backoff=0.1, bind=True)
def needs_one_retry(ctx):
    if ctx.retries < 1:
        raise RuntimeError("first attempt fails")
    return ctx.retries


@pytest.fixture
def backend(tmp_path):
    from sqlery import compat
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'fork.db'}")
    b = SQLAlchemyBackend()
    compat._backend = b
    yield b
    compat._backend = None


def run_next(backend, worker):
    """What the daemon's worker does for one job: claim it, fork a child, execute."""
    claimed = backend.claim_job(["default"], worker.worker_id)
    assert claimed is not None
    return worker._fork_and_execute(claimed)


def _all_jobs():
    from sqlmodel import Session, select

    from sqlery.core.models import JobAll
    from sqlery.fastapi_sqlery import database

    # live and finished (history) jobs together
    with Session(database.get_engine()) as s:
        return list(s.exec(select(JobAll)))


def test_chord_body_receives_header_results_in_forked_child(backend):
    chord(group(one.s(), one.s()), total.s()).apply_async()
    worker = WorkerProcess(queues=["default"], backend=backend)
    for _ in range(3):
        run_next(backend, worker)
    body = next(j for j in _all_jobs() if j.task_path.endswith("total"))
    assert body.status == "success", body.error


def test_bound_task_sees_its_retry_count_in_forked_child(backend):
    needs_one_retry.enqueue()
    worker = WorkerProcess(queues=["default"], backend=backend)
    run_next(backend, worker)
    time.sleep(0.5)  # retry backoff
    run_next(backend, worker)
    retry = next(j for j in _all_jobs() if j.retry_count == 1)
    assert retry.status == "success", retry.error
