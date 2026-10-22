"""Regression for #115 (async half): AsyncWorker idle wait is capped at the next due job."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlmodel import SQLModel

from sqlery.core.async_worker import AsyncWorker
from sqlery.core.models import QueuedJob
from sqlery.fastapi_sqlery import async_backend as _ab  # noqa: F401
from sqlery.fastapi_sqlery import database as _db_mod
from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend

pytestmark = pytest.mark.asyncio

_ran: list[int] = []


async def _job(x: int = 0) -> int:
    _ran.append(x)
    return x


@pytest_asyncio.fixture
async def backend(tmp_path):
    _db_mod.reset_async_engine()
    eng = _db_mod.get_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'due.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    _ran.clear()
    yield SQLAlchemyAsyncBackend()
    await eng.dispose()
    _db_mod.reset_async_engine()


async def _enqueue(delay: float | None) -> None:
    factory = _db_mod.get_async_session_factory()
    async with factory() as s:
        s.add(
            QueuedJob(
                task_path=f"{__name__}._job",
                queue_name="default",
                status="queued",
                kwargs={"x": 1},
                scheduled_at=None if delay is None else datetime.now(UTC) + timedelta(seconds=delay),
            )
        )
        await s.commit()


def _worker(backend, poll=30.0):
    return AsyncWorker(backend=backend, queues=["default"], poll_interval=poll)


async def test_empty_queue_keeps_poll_interval(backend):
    assert await backend.anext_due_delay(["default"]) is None
    assert await _worker(backend)._idle_timeout() == 30


async def test_job_due_now_does_not_shorten_the_wait(backend):
    await _enqueue(None)
    assert await backend.anext_due_delay(["default"]) is None


async def test_future_job_shortens_the_wait(backend):
    await _enqueue(2)
    delay = await backend.anext_due_delay(["default"])
    assert delay is not None and 0 < delay <= 2
    assert await _worker(backend)._idle_timeout() <= 2.1


async def test_far_future_job_is_capped_by_poll_interval(backend):
    await _enqueue(3600)
    assert await _worker(backend, poll=5)._idle_timeout() == 5


async def test_backend_error_falls_back_to_poll_interval(backend, monkeypatch):
    async def boom(queues):
        raise RuntimeError("db down")

    monkeypatch.setattr(backend, "anext_due_delay", boom)
    assert await _worker(backend)._idle_timeout() == 30


async def test_default_abc_hook_returns_none():
    from sqlery.compat import AsyncDatabaseBackend

    assert await AsyncDatabaseBackend.anext_due_delay(object(), ["default"]) is None


async def test_idle_worker_runs_delayed_job_before_poll_interval(backend):
    await _enqueue(0.5)
    w = _worker(backend, poll=30.0)
    t0 = time.monotonic()
    await asyncio.wait_for(w.run(max_jobs=1), timeout=10)
    assert time.monotonic() - t0 < 5
    assert _ran == [1]


async def test_idle_wait_wakes_on_shutdown(backend):
    w = _worker(backend, poll=30.0)
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    w._shutting_down = True
    w._shutdown_event.set()
    await asyncio.wait_for(task, timeout=5)
    assert time.monotonic() - t0 < 3


async def test_heartbeat_is_rate_limited(backend, monkeypatch):
    calls: list[float] = []

    async def hb(worker_id):
        calls.append(time.monotonic())

    monkeypatch.setattr(backend, "aupdate_heartbeat", hb)
    w = _worker(backend, poll=0.01)
    w.poll_interval = 0.01
    monkeypatch.setattr(w, "_heartbeat_interval", lambda: 60.0)
    await w.run(max_polls=10)
    assert len(calls) == 1
