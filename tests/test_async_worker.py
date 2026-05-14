"""Tests for sqlery.core.async_worker.AsyncWorker (ASYN-04).

Covers happy paths (async + sync job), failure path with traceback,
retry-on-failure with max_retries > 0, and heartbeat updates between polls.
Uses SQLAlchemyAsyncBackend on ``sqlite+aiosqlite:///:memory:``.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine():
    _db_mod.reset_async_engine()
    eng = _db_mod.get_async_engine("sqlite+aiosqlite:///:memory:")
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
