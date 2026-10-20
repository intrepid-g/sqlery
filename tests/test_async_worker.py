"""Tests for sqlery.core.async_worker.AsyncWorker (ASYN-04).

Covers happy paths (async + sync job), failure path with traceback,
retry-on-failure with max_retries > 0, and heartbeat updates between polls.
Uses SQLAlchemyAsyncBackend on a temporary ``sqlite+aiosqlite`` file.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlmodel import SQLModel
from sqlalchemy import select

from sqlery.fastapi_sqlery import async_backend as _ab  # registers Lease on metadata  # noqa: F401
from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend
from sqlery.fastapi_sqlery import database as _db_mod
from sqlery.core.models import QueuedJob, Worker
from sqlery.core.async_worker import AsyncWorker


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Job functions used by the worker (top-level so importable)
# ---------------------------------------------------------------------------

_call_log: list[str] = []


async def _async_job_ok(x: int = 0) -> int:
    _call_log.append(f"async_ok:{x}")
    await asyncio.sleep(0)
    return x * 2


def _sync_job_ok(x: int = 0) -> int:
    _call_log.append(f"sync_ok:{x}")
    return x + 1


async def _async_job_boom() -> None:
    raise RuntimeError("kaboom")


async def _async_nap(seconds: float = 0.3) -> str:
    await asyncio.sleep(seconds)
    return "done"


async def _plus_one(previous: int) -> int:
    return previous + 1


_bound_seen: list[Any] = []


async def _bound_task(ctx, x: int = 0) -> int:
    _bound_seen.append(ctx)
    return x


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(tmp_path):
    # File-backed SQLite: concurrent sessions each need their own connection.
    _db_mod.reset_async_engine()
    eng = _db_mod.get_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()
    _db_mod.reset_async_engine()


@pytest_asyncio.fixture
async def session_factory(engine):
    return _db_mod.get_async_session_factory()


@pytest_asyncio.fixture
async def backend(engine):
    return SQLAlchemyAsyncBackend()


@pytest_asyncio.fixture
async def make_job(session_factory):
    async def _factory(**kw) -> QueuedJob:
        defaults: dict[str, Any] = dict(
            task_path=f"{__name__}._async_job_ok",
            queue_name="default",
            status="queued",
            kwargs={"x": 1},
        )
        defaults.update(kw)
        async with session_factory() as s:
            job = QueuedJob(**defaults)
            s.add(job)
            await s.commit()
            await s.refresh(job)
            return job

    return _factory


@pytest_asyncio.fixture(autouse=True)
def _reset_log():
    _call_log.clear()
    yield
    _call_log.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_constructor_defaults_and_overrides(backend):
    w = AsyncWorker(backend=backend, queues=["default"])
    assert w.queues == ["default"]
    assert w.backend is backend
    assert w.worker_id  # auto-generated, non-empty
    assert w.poll_interval == 1.0
    assert w.shutdown_deadline_seconds == 60

    w2 = AsyncWorker(
        backend=backend, queues=["q"], worker_id="wkr-x",
        poll_interval=0.05, shutdown_deadline_seconds=2,
    )
    assert w2.worker_id == "wkr-x"
    assert w2.poll_interval == 0.05
    assert w2.shutdown_deadline_seconds == 2


async def test_async_job_happy_path(backend, make_job, session_factory):
    job = await make_job(
        task_path=f"{__name__}._async_job_ok",
        kwargs={"x": 5},
    )
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="wkr-async", poll_interval=0.01,
    )

    # Run worker for a single useful cycle by stopping after one job.
    run_task = asyncio.create_task(worker.run(max_jobs=1))
    await asyncio.wait_for(run_task, timeout=5)

    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))).scalars().first()
    assert row.status == "success"
    assert row.output == "10"
    assert "async_ok:5" in _call_log


async def test_sync_job_runs_in_executor(backend, make_job, session_factory):
    job = await make_job(
        task_path=f"{__name__}._sync_job_ok",
        kwargs={"x": 41},
    )
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="wkr-sync", poll_interval=0.01,
    )
    await asyncio.wait_for(worker.run(max_jobs=1), timeout=5)

    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))).scalars().first()
    assert row.status == "success"
    assert row.output == "42"
    assert "sync_ok:41" in _call_log


async def test_failure_records_error_and_traceback(backend, make_job, session_factory):
    job = await make_job(
        task_path=f"{__name__}._async_job_boom",
        kwargs={},
    )
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="wkr-fail", poll_interval=0.01,
    )
    await asyncio.wait_for(worker.run(max_jobs=1), timeout=5)

    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))).scalars().first()
    assert row.status == "failed"
    assert "kaboom" in row.error
    assert "RuntimeError" in row.traceback


async def test_failure_with_max_retries_requeues(backend, make_job, session_factory):
    job = await make_job(
        task_path=f"{__name__}._async_job_boom",
        kwargs={},
        max_retries=2,
    )
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="wkr-retry", poll_interval=0.01,
    )
    await asyncio.wait_for(worker.run(max_jobs=1), timeout=5)

    async with session_factory() as s:
        rows = (await s.execute(select(QueuedJob))).scalars().all()
    statuses = sorted(r.status for r in rows)
    # Original failed (or archived), and a new retry row queued.
    assert "queued" in statuses, statuses
    retry = [r for r in rows if r.id != job.id][0]
    assert retry.retry_count == 1
    assert retry.parent_job_id == job.id
    assert retry.max_retries == 2


async def test_retry_keeps_job_spec_and_archives_failed_attempt(
    backend, make_job, session_factory
):
    job = await make_job(
        task_path=f"{__name__}._async_job_boom", kwargs={}, max_retries=1,
        job_name="my-job", failure_ttl=60, result_ttl=60, tags=["t"], meta={"k": "v"},
        retry_intervals=[0.1],
    )
    worker = AsyncWorker(
        backend=backend, queues=["default"], worker_id="wkr-spec", poll_interval=0.01
    )
    await asyncio.wait_for(worker.run(max_jobs=1), timeout=5)

    async with session_factory() as s:
        rows = (await s.execute(select(QueuedJob))).scalars().all()
    first = next(r for r in rows if r.id == job.id)
    retry = next(r for r in rows if r.parent_job_id == job.id)
    assert (retry.job_name, retry.failure_ttl, retry.result_ttl) == ("my-job:retry:1", 60, 60)
    assert (retry.tags, retry.meta, retry.retry_intervals) == (["t"], {"k": "v"}, [0.1])
    assert first.status == "archived"


async def test_heartbeat_updates_between_polls(backend, session_factory):
    # Spy on aupdate_heartbeat to confirm it's called once per poll cycle.
    import uuid

    worker_id = uuid.uuid4()
    await backend.aregister_worker(worker_id, {"node_id": "n", "pid": 1, "queues": ["default"]})

    call_count = {"n": 0}
    real = backend.aupdate_heartbeat

    async def spy(wid):
        call_count["n"] += 1
        return await real(wid)

    backend.aupdate_heartbeat = spy  # type: ignore[method-assign]

    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id=worker_id, poll_interval=0.01,
    )
    await asyncio.wait_for(worker.run(max_polls=3), timeout=5)
    assert call_count["n"] == 3


def test_no_signal_dot_signal_in_source():
    import inspect
    import sqlery.core.async_worker as mod
    src = inspect.getsource(mod)
    assert "signal.signal(" not in src, "Must use loop.add_signal_handler, not signal.signal()"


async def test_jobs_run_concurrently(backend, make_job, session_factory):
    """Four jobs that each await 0.3 s overlap on one worker instead of queueing up."""
    import time

    for _ in range(4):
        await make_job(task_path=f"{__name__}._async_nap", kwargs={"seconds": 0.3})
    worker = AsyncWorker(backend=backend, queues=["default"], poll_interval=0.01)
    started = time.monotonic()
    await asyncio.wait_for(worker.run(max_jobs=4), timeout=10)
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"jobs ran one after another ({elapsed:.2f}s)"
    async with session_factory() as s:
        rows = (await s.execute(select(QueuedJob))).scalars().all()
    assert [r.status for r in rows] == ["success"] * 4


async def test_concurrency_limit_is_respected(backend, make_job):
    peak = 0
    current = 0

    class Probe(AsyncWorker):
        async def _execute_job(self, job):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.1)
            await super()._execute_job(job)
            current -= 1

    for _ in range(6):
        await make_job(task_path=f"{__name__}._async_nap", kwargs={"seconds": 0})
    worker = Probe(backend=backend, queues=["default"], poll_interval=0.01, concurrency=2)
    await asyncio.wait_for(worker.run(max_jobs=6), timeout=10)
    assert peak == 2


def test_concurrency_must_be_positive():
    with pytest.raises(ValueError):
        AsyncWorker(backend=object(), concurrency=0)


async def test_chain_step_receives_upstream_result(
    backend, make_job, session_factory, monkeypatch
):
    class _Result:
        def __init__(self, job_id):
            self.result = 1

    monkeypatch.setattr("sqlery.core.result.AsyncResult", _Result)
    upstream = await make_job(
        task_path=f"{__name__}._async_nap", status="success", output="1", kwargs={}
    )
    step = await make_job(
        task_path=f"{__name__}._plus_one",
        kwargs={},
        meta={"canvas": {"pass_result": "single"}},
        dependencies=[upstream.id],
    )
    worker = AsyncWorker(backend=backend, queues=["default"], poll_interval=0.01)
    await asyncio.wait_for(worker.run(max_jobs=1), timeout=5)
    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == step.id))).scalars().first()
    assert (row.status, row.output) == ("success", "2"), row.error


async def test_bind_task_gets_context_and_signals_fire(backend, make_job, session_factory):
    from sqlery.core.job import job as job_decorator
    from sqlery.core.retry import TaskContext
    from sqlery.core.signals import task_postrun, task_prerun

    globals()["bound_job"] = job_decorator(bind=True)(_bound_task)
    seen: list[str] = []

    def pre(sender, **kw):
        seen.append("pre")

    def post(sender, **kw):
        seen.append(kw["state"])

    task_prerun.connect(pre)
    task_postrun.connect(post)
    try:
        row = await make_job(task_path=f"{__name__}.bound_job", kwargs={"x": 7})
        worker = AsyncWorker(backend=backend, queues=["default"], poll_interval=0.01)
        await asyncio.wait_for(worker.run(max_jobs=1), timeout=5)
    finally:
        task_prerun.disconnect(pre)
        task_postrun.disconnect(post)
    async with session_factory() as s:
        done = (await s.execute(select(QueuedJob).where(QueuedJob.id == row.id))).scalars().first()
    assert (done.status, done.output) == ("success", "7"), done.error
    assert isinstance(_bound_seen[-1], TaskContext)
    assert seen == ["pre", "SUCCESS"]
