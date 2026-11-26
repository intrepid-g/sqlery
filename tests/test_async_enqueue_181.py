"""#181: ``await @async_job.enqueue()`` must create a job a worker then runs.

Both modes, SQLite. Standalone goes through ``sqlery.compat.get_backend()`` (no
``AsyncQueue.configure``); Django does the same on the Django test DB.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

import sqlery.compat as compat
from sqlery import async_job
from sqlery.core.async_worker import AsyncWorker
from sqlery.fastapi_sqlery import database as _db_mod

pytestmark = pytest.mark.asyncio


@async_job
async def add_181(a: int, b: int = 0) -> int:
    await asyncio.sleep(0)
    return a + b


async def _run_one(backend) -> None:
    worker = AsyncWorker(backend=backend, queues=["default"], poll_interval=0.01)
    await asyncio.wait_for(worker.run(max_jobs=1), timeout=10)


@pytest_asyncio.fixture
async def standalone(tmp_path, monkeypatch):
    from sqlery.fastapi_sqlery import async_backend as _ab  # noqa: F401
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    path = tmp_path / "e.db"
    _db_mod.reset_async_engine()
    _db_mod.init_database(f"sqlite:///{path}")
    _db_mod.get_async_engine(f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr(compat, "_backend", SQLAlchemyBackend())
    yield
    await _db_mod.get_async_engine(f"sqlite+aiosqlite:///{path}").dispose()
    _db_mod.reset_async_engine()


async def test_standalone_enqueue_then_worker_runs_job(standalone):
    from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend

    job = await add_181.enqueue(2, b=3)
    backend = SQLAlchemyAsyncBackend()
    assert await backend.aget_status(job.id) == "queued"

    await _run_one(backend)

    assert await backend.aget_status(job.id) == "success"
    assert (await backend.aget_job(job.id)).output == "5"


@pytest.mark.parametrize("use_async_backend", [False, True])
async def test_standalone_async_queue_reads_jobs(standalone, use_async_backend, monkeypatch):
    from sqlery.async_queue import AsyncQueue
    from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend

    monkeypatch.setattr(AsyncQueue, "_default_backend", None)
    backend = SQLAlchemyAsyncBackend() if use_async_backend else None
    queue = AsyncQueue(backend=backend)
    job = await queue.enqueue(add_181, 2, b=3)

    assert (await queue.get_job(job.id)).status == "queued"
    assert await queue.get_job(job.id + 1) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("use_async_backend", [False, True])
async def test_django_async_queue_reads_jobs(monkeypatch, use_async_backend):
    from sqlery.async_queue import AsyncQueue
    from sqlery.django_sqlery.async_backend import DjangoAsyncBackend

    monkeypatch.setattr(AsyncQueue, "_default_backend", None)
    backend = DjangoAsyncBackend() if use_async_backend else None
    queue = AsyncQueue(backend=backend)
    job = await queue.enqueue(add_181, 2, b=3)

    assert (await queue.get_job(job.id)).status == "queued"
    assert await queue.get_job(job.id + 1) is None


@pytest.mark.django_db(transaction=True)
async def test_django_enqueue_then_worker_runs_job():
    from sqlery.django_sqlery.async_backend import DjangoAsyncBackend
    from sqlery.django_sqlery.models import JobAll

    job = await add_181.delay(4, b=5)

    await _run_one(DjangoAsyncBackend())

    row = await JobAll.objects.aget(id=job.id)
    assert row.status == "success"
    assert row.output == "9"


@pytest.mark.django_db(transaction=True)
async def test_django_enqueue_with_configured_async_backend_creates_job(monkeypatch):
    """Configuring the async backend (as the issue did) must not break enqueue."""
    from sqlery.async_queue import AsyncQueue
    from sqlery.django_sqlery.async_backend import DjangoAsyncBackend

    monkeypatch.setattr(AsyncQueue, "_default_backend", DjangoAsyncBackend())
    job = await add_181.enqueue(1, b=1)
    assert await DjangoAsyncBackend().aget_status(job.id) == "queued"
