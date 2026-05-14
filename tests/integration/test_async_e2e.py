"""DMOD-06 + SMOD-06 — AsyncWorker end-to-end matrix rows.

This module lands the (async, *, sqlite) matrix cells that did not fit the
sync-parametrized ``test_modes.py`` harness (pytest-asyncio requires its
own test surface, and the in-process AsyncWorker + AsyncSession lifecycle
diverges enough from the sync harness that splitting these into a sibling
module is cleaner per PLAN.md).

Two cells in this file:
- ``test_async_e2e_standalone``: SQLAlchemyAsyncBackend on
  ``sqlite+aiosqlite:///:memory:``; enqueues, drives the worker, asserts
  terminal status.
- ``test_async_e2e_django``: DjangoAsyncBackend on the Django test DB;
  same shape adapted to the native-async ORM.

Postgres variants are intentionally out of scope (marked slow elsewhere).
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlmodel import SQLModel

from sqlery.core.async_worker import AsyncWorker
from sqlery.core.models import QueuedJob
from sqlery.fastapi_sqlery import async_backend as _ab  # registers Lease on metadata  # noqa: F401
from sqlery.fastapi_sqlery import database as _db_mod
from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Top-level job used by both async-E2E tests.
# ---------------------------------------------------------------------------


async def _e2e_async_job(a: int = 0, b: int = 0) -> int:
    """Trivial async job — returns ``a + b``. Top-level so it's importable."""
    await asyncio.sleep(0)
    return a + b


# ---------------------------------------------------------------------------
# Standalone (SMOD-06)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _standalone_async_engine():
    _db_mod.reset_async_engine()
    eng = _db_mod.get_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()
    _db_mod.reset_async_engine()


async def test_async_e2e_standalone(_standalone_async_engine):
    """(async, standalone, sqlite) — AsyncWorker drives a job to terminal state."""
    factory = _db_mod.get_async_session_factory()
    async with factory() as s:
        job = QueuedJob(
            task_path=f"{__name__}._e2e_async_job",
            kwargs={"a": 1, "b": 2},
            queue_name="default",
            status="queued",
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        job_id = job.id

    backend = SQLAlchemyAsyncBackend()
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="async-e2e-standalone", poll_interval=0.01,
    )

    # Run as a background task and stop after one job — exercises the same
    # control flow as a real deployment (poll, claim, dispatch, mark
    # terminal) without the noisy shutdown-deadline race.
    run_task = asyncio.create_task(worker.run(max_jobs=1))
    await asyncio.wait_for(run_task, timeout=10)

    status = await backend.aget_status(job_id)
    assert status == "success", f"expected 'success' but got {status!r}"

    async with factory() as s:
        row = (
            await s.execute(select(QueuedJob).where(QueuedJob.id == job_id))
        ).scalars().first()
    assert row.output == "3", f"expected output '3' but got {row.output!r}"


# ---------------------------------------------------------------------------
# Django (DMOD-06)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_async_e2e_django():
    """(async, django, sqlite) — AsyncWorker on the native-async Django ORM."""
    from sqlery.django_sqlery.async_backend import DjangoAsyncBackend
    from sqlery.django_sqlery.models import QueuedJob as DjangoQueuedJob

    job = await DjangoQueuedJob.objects.acreate(
        task_path=f"{__name__}._e2e_async_job",
        kwargs={"a": 4, "b": 5},
        queue_name="default",
        status="queued",
    )

    backend = DjangoAsyncBackend()
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="async-e2e-django", poll_interval=0.01,
    )
    run_task = asyncio.create_task(worker.run(max_jobs=1))
    await asyncio.wait_for(run_task, timeout=10)

    refreshed = await DjangoQueuedJob.objects.aget(id=job.id)
    assert refreshed.status == "success", (
        f"expected 'success' but got {refreshed.status!r}"
    )
