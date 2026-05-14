"""Unit tests for SQLAlchemyAsyncBackend (ASYN-03).

Mirrors the structure of tests/test_django_async_backend.py. Uses
``sqlite+aiosqlite:///:memory:`` for speed; concurrent-claim race test
included to validate the with_for_update / CAS semantics.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, UTC

import pytest
import pytest_asyncio
from sqlmodel import SQLModel

# Importing the async backend also registers the Lease SQLModel on metadata.
from sqlery.fastapi_sqlery import async_backend as _ab  # noqa: F401
from sqlery.fastapi_sqlery.async_backend import (
    Lease,
    SQLAlchemyAsyncBackend,
)
from sqlery.fastapi_sqlery import database as _db_mod
from sqlery.core.models import JobRegistry, QueuedJob, ScheduledTask, Worker


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def engine():
    """Fresh in-memory async engine + tables per test."""
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
    async def _factory(**kw):
        defaults = dict(
            task_path="tests.helpers.noop",
            queue_name="default",
            status="queued",
        )
        defaults.update(kw)
        async with session_factory() as s:
            job = QueuedJob(**defaults)
            s.add(job)
            await s.commit()
            await s.refresh(job)
            return job

    return _factory


# ----- aclaim_job -----------------------------------------------------------


async def test_aclaim_job_returns_none_when_no_rows(backend):
    assert await backend.aclaim_job(["default"], "wkr-1") is None


async def test_aclaim_job_claims_and_marks_running(backend, make_job, session_factory):
    job = await make_job()
    claimed = await backend.aclaim_job(["default"], "wkr-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"

    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))
        fresh = res.scalars().first()
        assert fresh.status == "running"


async def test_aclaim_job_filters_by_queue(backend, make_job):
    await make_job(queue_name="other")
    assert await backend.aclaim_job(["default"], "wkr-1") is None


async def test_aclaim_job_concurrent_only_one_wins(backend, make_job):
    """SKIP LOCKED / CAS contract: two claimers on same row → exactly one winner."""
    await make_job()
    results = await asyncio.gather(
        backend.aclaim_job(["default"], "wkr-A"),
        backend.aclaim_job(["default"], "wkr-B"),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


# ----- amark_* --------------------------------------------------------------


async def test_amark_running(backend, make_job, session_factory):
    job = await make_job()
    await backend.amark_running(job.id, "wkr-1")
    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))
        assert res.scalars().first().status == "running"


async def test_amark_success(backend, make_job, session_factory):
    job = await make_job(status="running")
    await backend.amark_success(job.id, "ok")
    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))
        fresh = res.scalars().first()
        assert fresh.status == "success"
        assert fresh.output == "ok"


async def test_amark_failed(backend, make_job, session_factory):
    job = await make_job(status="running")
    await backend.amark_failed(job.id, "boom", traceback="tb")
    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))
        fresh = res.scalars().first()
        assert fresh.status == "failed"
        assert fresh.error == "boom"
        assert fresh.traceback == "tb"


async def test_amark_shutting_down(backend, make_job, session_factory):
    job = await make_job(status="running")
    await backend.amark_shutting_down(job.id)
    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(QueuedJob).where(QueuedJob.id == job.id))
        assert res.scalars().first().status == "shutting_down"


# ----- aget_status / aget_job ----------------------------------------------


async def test_aget_status(backend, make_job):
    job = await make_job()
    assert await backend.aget_status(job.id) == "queued"
    assert await backend.aget_status(999_999) is None


async def test_aget_job(backend, make_job):
    job = await make_job()
    fetched = await backend.aget_job(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert await backend.aget_job(999_999) is None


# ----- worker registration & heartbeat -------------------------------------


async def test_aregister_and_aunregister_worker(backend, session_factory):
    wid = uuid.uuid4()
    await backend.aregister_worker(
        wid, {"node_id": "node-1", "pid": 1234, "queues": ["default"]}
    )
    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(Worker).where(Worker.id == wid))
        assert res.scalars().first() is not None

    await backend.aunregister_worker(wid)
    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(select(Worker).where(Worker.id == wid))
        assert res.scalars().first() is None


async def test_aupdate_heartbeat(backend, session_factory):
    wid = uuid.uuid4()
    await backend.aregister_worker(
        wid, {"node_id": "node-1", "pid": 1234, "queues": ["default"]}
    )
    async with session_factory() as s:
        from sqlalchemy import select

        before = (
            await s.execute(select(Worker).where(Worker.id == wid))
        ).scalars().first().last_heartbeat

    await asyncio.sleep(0.02)
    await backend.aupdate_heartbeat(wid)

    async with session_factory() as s:
        from sqlalchemy import select

        after = (
            await s.execute(select(Worker).where(Worker.id == wid))
        ).scalars().first().last_heartbeat
    # Strip tzinfo to compare safely (SQLite drops tzinfo on round-trip).
    b = before.replace(tzinfo=None) if before.tzinfo else before
    a = after.replace(tzinfo=None) if after.tzinfo else after
    assert a >= b


# ----- lease ops ------------------------------------------------------------


async def test_aclaim_lease_grants_when_free(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True


async def test_aclaim_lease_blocked_by_live_lease(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True
    assert await backend.aclaim_lease("default", "daemon-2", ttl_seconds=30) is False


async def test_arenew_lease(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True
    assert await backend.arenew_lease("default", "daemon-1") is True


async def test_arelease_lease(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True
    await backend.arelease_lease("default", "daemon-1")
    # After release, a new daemon should be able to claim.
    assert await backend.aclaim_lease("default", "daemon-2", ttl_seconds=30) is True


async def test_aclaim_lease_takes_over_expired(backend, session_factory):
    # Manually plant an expired lease owned by someone else.
    past = datetime.now(UTC) - timedelta(seconds=60)
    async with session_factory() as s:
        s.add(
            Lease(
                queue_name="q1",
                daemon_id="old-daemon",
                node_id="old",
                pid=0,
                acquired_at=past,
                expires_at=past,
            )
        )
        await s.commit()

    assert await backend.aclaim_lease("q1", "new-daemon", ttl_seconds=30) is True


# ----- scheduled tasks ------------------------------------------------------


async def test_aget_due_scheduled_tasks(backend, session_factory):
    async with session_factory() as s:
        task = ScheduledTask(
            name="t1",
            task_path="tests.helpers.noop",
            cron_expression="* * * * *",
            queue_name="default",
            enabled=True,
            next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        s.add(task)
        await s.commit()

    due = await backend.aget_due_scheduled_tasks(datetime.now(UTC))
    assert any(t.name == "t1" for t in due)


# ----- registry ops ---------------------------------------------------------


async def test_aregistry_add_and_remove(backend, make_job, session_factory):
    job = await make_job()
    await backend.aregistry_add("started", job.id)

    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(
            select(JobRegistry)
            .where(JobRegistry.job_id == job.id)
            .where(JobRegistry.registry_type == "started")
            .where(JobRegistry.exited_at.is_(None))
        )
        assert res.scalars().first() is not None

    await backend.aregistry_remove("started", job.id)

    async with session_factory() as s:
        from sqlalchemy import select

        res = await s.execute(
            select(JobRegistry)
            .where(JobRegistry.job_id == job.id)
            .where(JobRegistry.registry_type == "started")
            .where(JobRegistry.exited_at.is_(None))
        )
        assert res.scalars().first() is None


# ----- structural guards ----------------------------------------------------


def test_no_session_exec_in_implementation():
    """SQLModel.exec is sync-only; the async backend must use session.execute()."""
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent
        / "src"
        / "sqlery"
        / "fastapi_sqlery"
        / "async_backend.py"
    )
    text = src.read_text()
    assert "session.exec(" not in text


def test_with_for_update_skip_locked_present():
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).parent.parent
        / "src"
        / "sqlery"
        / "fastapi_sqlery"
        / "async_backend.py"
    )
    text = src.read_text()
    assert re.search(r"with_for_update.*skip_locked", text) is not None


def test_subclasses_async_database_backend():
    from sqlery.compat import AsyncDatabaseBackend

    assert issubclass(SQLAlchemyAsyncBackend, AsyncDatabaseBackend)
