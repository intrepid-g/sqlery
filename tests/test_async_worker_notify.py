"""#117 (b): AsyncWorker optional LISTEN/NOTIFY wake-up (PG tests need SQLERY_TEST_PG_URL)."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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
    eng = _db_mod.get_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'n.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    _ran.clear()
    yield SQLAlchemyAsyncBackend()
    await eng.dispose()
    _db_mod.reset_async_engine()


async def _enqueue(delay=None) -> None:
    factory = _db_mod.get_async_session_factory()
    async with factory() as s:
        s.add(QueuedJob(task_path=f"{__name__}._job", queue_name="default", status="queued",
                        kwargs={"x": 1}))
        await s.commit()


def _worker(backend, poll=30.0):
    return AsyncWorker(backend=backend, queues=["default"], poll_interval=poll)


_PG = os.environ.get("SQLERY_TEST_PG_URL")


def _notify_config(flag=True):
    from sqlery.compat import get_config as real

    return lambda key, default=None: flag if key == "SQLERY_PG_NOTIFY" else real(key, default)


async def test_notify_flag_off_starts_no_listener(backend):
    w = _worker(backend)
    with patch("sqlery.compat.get_config", _notify_config(False)):
        w._start_listener()
    assert w._notify_event is None and w._listen_task is None


async def test_notify_on_but_not_postgres_falls_back_to_polling(backend):
    w = _worker(backend)
    with patch("sqlery.compat.get_config", _notify_config(True)), patch(
        "sqlery.core.pg_notify.resolve_pg_dsn", return_value=None
    ):
        w._start_listener()
    assert w._notify_event is None and w._listen_task is None


def test_resolve_pg_dsn_strips_driver_suffix():
    from sqlery.core.pg_notify import resolve_pg_dsn

    def cfg(key, default=None):
        return "postgresql+psycopg://u:p@h:5432/db" if key == "DATABASE_URL" else default

    with patch("sqlery.compat.get_config", cfg):
        assert resolve_pg_dsn() == "postgresql://u:p@h:5432/db"

    def sqlite(key, default=None):
        return "sqlite:///x.db" if key == "DATABASE_URL" else default

    with patch("sqlery.compat.get_config", sqlite):
        assert resolve_pg_dsn() is None


async def test_unreachable_postgres_listener_is_tolerated(backend):
    pytest.importorskip("psycopg")
    w = _worker(backend, poll=0.2)
    with patch("sqlery.compat.get_config", _notify_config(True)), patch(
        "sqlery.core.pg_notify.resolve_pg_dsn",
        return_value="postgresql://nobody@127.0.0.1:1/none?connect_timeout=1",
    ):
        await _enqueue(None)
        await asyncio.wait_for(w.run(max_jobs=1), timeout=15)
    assert _ran == [1]
    assert w._listen_task is None


@pytest.mark.skipif(not _PG, reason="SQLERY_TEST_PG_URL not set")
async def test_notify_wakes_idle_worker_before_poll_interval(backend):
    import psycopg

    from sqlery.core.pg_notify import sanitize_queue_name_to_channel

    w = _worker(backend, poll=30.0)
    with patch("sqlery.compat.get_config", _notify_config(True)), patch(
        "sqlery.core.pg_notify.resolve_pg_dsn", return_value=_PG
    ):
        task = asyncio.create_task(w.run(max_jobs=1))
        await asyncio.sleep(1.0)  # worker is idle and LISTENing
        await _enqueue(None)
        t0 = time.monotonic()

        def notify():
            with psycopg.connect(_PG, autocommit=True) as c:
                c.execute("SELECT pg_notify(%s, '')", [sanitize_queue_name_to_channel("default")])

        await asyncio.to_thread(notify)
        await asyncio.wait_for(task, timeout=10)
    assert time.monotonic() - t0 < 5
    assert _ran == [1]
