"""Tests for AsyncWorker drain-with-deadline shutdown (ASYN-05).

Covers:
- Job-wins-before-deadline path: marked ``success`` with real result.
- Deadline-wins path: transient ``shutting_down`` state IS observable from a
  second async session/backend, job ends ``failed`` with the canonical error
  string, and a retry row is enqueued when ``max_retries > 0``.
- E2E SIGTERM path (slow-marked) using ``os.kill(os.getpid(), SIGTERM)``.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import pytest
import pytest_asyncio
from sqlmodel import SQLModel
from sqlalchemy import select

from sqlery.fastapi_sqlery import async_backend as _ab  # registers Lease  # noqa: F401
from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend
from sqlery.fastapi_sqlery import database as _db_mod
from sqlery.core.models import QueuedJob
from sqlery.core.async_worker import AsyncWorker, SHUTDOWN_TIMEOUT_ERROR


pytestmark = pytest.mark.asyncio


# Top-level job callables (importable by task_path).
_fast_done = asyncio.Event()


async def _quick_job() -> str:
    await asyncio.sleep(0.05)
    return "quick-done"


async def _slow_job() -> str:
    await asyncio.sleep(5)
    return "should-not-reach"


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
            task_path=f"{__name__}._quick_job",
            queue_name="default",
            status="queued",
            kwargs={},
        )
        defaults.update(kw)
        async with session_factory() as s:
            job = QueuedJob(**defaults)
            s.add(job)
            await s.commit()
            await s.refresh(job)
            return job

    return _factory


async def test_job_finishes_before_deadline_marks_success(backend, make_job, session_factory):
    """Job wins the race: terminal ``success`` write lands within deadline.

    The transient ``shutting_down`` state may or may not be externally
    observable on this path — the job-wins terminal write can land in the same
    event-loop tick. We do NOT require observing it here.
    """
    job = await make_job(task_path=f"{__name__}._quick_job")

    worker = AsyncWorker(
        backend=backend, queues=["default"],
        poll_interval=0.01, shutdown_deadline_seconds=2.0,
    )

    async def trigger():
        # Wait for the job to be claimed/dispatched, then signal shutdown.
        for _ in range(200):
            if worker._inflight:
                break
            await asyncio.sleep(0.01)
        worker._initiate_shutdown()

    await asyncio.gather(
        worker.run(max_jobs=1),
        trigger(),
    )

    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))).scalars().first()
    assert row.status == "success", f"expected success, got {row.status}"
    assert row.output == "quick-done"


async def test_deadline_wins_observes_transient_state_and_requeues_retry(
    backend, make_job, session_factory
):
    """Deadline wins: transient ``shutting_down`` IS observable; row ends
    ``failed`` with the canonical error string; retry row enqueued when
    ``max_retries > 0``.
    """
    job = await make_job(task_path=f"{__name__}._slow_job", max_retries=2)

    worker = AsyncWorker(
        backend=backend, queues=["default"],
        poll_interval=0.01, shutdown_deadline_seconds=1.0,
    )

    observed_statuses: list[str] = []
    observed_event = asyncio.Event()
    inflight_event = asyncio.Event()

    async def peeker():
        # Peek the row's status until we observe the transient 'shutting_down'
        # state, then stop (so we don't contend with drain's terminal write).
        await inflight_event.wait()
        end_time = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < end_time:
            status = await backend.aget_status(job.id)
            if status is not None:
                observed_statuses.append(status)
                if status == "shutting_down":
                    observed_event.set()
                    return
            await asyncio.sleep(0.005)

    async def trigger():
        for _ in range(200):
            if worker._inflight:
                inflight_event.set()
                break
            await asyncio.sleep(0.01)
        else:
            inflight_event.set()
        worker._initiate_shutdown()

    await asyncio.gather(
        worker.run(max_jobs=1),
        trigger(),
        peeker(),
    )

    # Transient state must have been observed on the deadline-wins path.
    assert "shutting_down" in observed_statuses, (
        f"expected to observe 'shutting_down' transient state; saw: {observed_statuses}"
    )

    # Terminal state should be 'failed' with the canonical error string.
    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))).scalars().first()
        assert row.status == "failed", f"expected failed terminal, got {row.status}"
        assert row.error == SHUTDOWN_TIMEOUT_ERROR

        # Retry row should be enqueued (max_retries=2).
        all_rows = (await s.execute(select(QueuedJob))).scalars().all()
    retries = [r for r in all_rows if r.parent_job_id == job.id]
    assert len(retries) == 1, f"expected exactly one retry row; got {len(retries)}"
    retry = retries[0]
    assert retry.status == "queued"
    assert retry.retry_count == 1
    assert retry.max_retries == 2


async def test_initiate_shutdown_is_idempotent(backend):
    worker = AsyncWorker(backend=backend, queues=["default"])
    assert worker._shutting_down is False
    worker._initiate_shutdown()
    assert worker._shutting_down is True
    worker._initiate_shutdown()  # second call must not raise
    assert worker._shutting_down is True


def test_uses_add_signal_handler_not_signal_signal():
    """Static guard: source must use loop.add_signal_handler."""
    import inspect
    import sqlery.core.async_worker as mod

    src = inspect.getsource(mod)
    # Search for actual call form (open-paren) — docstrings reworded to not match.
    assert "signal.signal(" not in src
    assert "loop.add_signal_handler(" in src


def test_canonical_error_string_present_once():
    """Plan verification: shutdown_timeout error string appears exactly once."""
    import inspect
    import sqlery.core.async_worker as mod

    src = inspect.getsource(mod)
    # Once as the constant definition.
    assert src.count("shutdown_timeout: worker terminated before job finished") == 1


@pytest.mark.slow
async def test_e2e_sigterm_triggers_shutdown(backend, make_job, session_factory):
    """End-to-end: actually send SIGTERM via os.kill to validate the signal
    handler wiring. Marked slow to permit deselection on flaky CI signals.
    """
    job = await make_job(task_path=f"{__name__}._quick_job")
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        poll_interval=0.01, shutdown_deadline_seconds=2.0,
    )

    async def trigger():
        for _ in range(200):
            if worker._inflight:
                break
            await asyncio.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.gather(worker.run(max_jobs=1), trigger())

    async with session_factory() as s:
        row = (await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))).scalars().first()
    # Either way (job-wins or deadline-wins), the row should be terminal.
    assert row.status in ("success", "failed")
