"""DMOD-06 + SMOD-06 — AsyncWorker end-to-end matrix rows.

This module lands the (async, *, sqlite) matrix cells that did not fit the
sync-parametrized ``test_modes.py`` harness (pytest-asyncio requires its
own test surface, and the in-process AsyncWorker + AsyncSession lifecycle
diverges enough from the sync harness that splitting these into a sibling
module is cleaner per PLAN.md).

Four cells in this file:
- ``test_async_e2e_standalone``: SQLAlchemyAsyncBackend on
  ``sqlite+aiosqlite:///:memory:``; enqueues, drives the worker, asserts
  terminal status.
- ``test_async_e2e_django``: DjangoAsyncBackend on the Django test DB;
  same shape adapted to the native-async ORM.
- ``test_async_e2e_standalone_pg`` / ``test_async_e2e_django_pg``: the same
  two cells against real PostgreSQL, via the psycopg3 async driver (no
  aiosqlite/extra deps needed — ``postgresql+psycopg://``). Both carry
  ``@pytest.mark.postgres`` and rely on
  ``tests/integration/conftest.py::pytest_collection_modifyitems`` to skip
  cleanly when ``SQLERY_TEST_PG_URL`` is unset (same convention as every
  other postgres-marked cell in this directory).
"""

from __future__ import annotations

import asyncio
import os

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


def _pg_url() -> str:
    """Return SQLERY_TEST_PG_URL translated to the psycopg3 dialect.

    Same translation as ``tests/test_standalone_lifecycle_partitioned.py::_pg_url``
    — the sync engine used by ``init_database`` also needs ``+psycopg`` since
    psycopg2 is not an installed dependency (project standardizes on psycopg3).
    """
    raw = os.environ["SQLERY_TEST_PG_URL"]
    if raw.startswith("postgresql://") or raw.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg" + raw[raw.index("://"):]
    return raw


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


@pytest_asyncio.fixture
async def _standalone_async_engine_pg():
    """PG-backed async engine via the psycopg3 async driver.

    ``get_async_engine`` auto-translates ``postgresql://`` -> ``postgresql+psycopg://``
    (see ``_to_async_url`` in ``sqlery.fastapi_sqlery.database``) — no aiosqlite or
    other new dependency needed. Only used by ``@pytest.mark.postgres`` tests, which
    are skipped before this fixture ever runs when ``SQLERY_TEST_PG_URL`` is unset.

    Schema is created via the *sync* ``init_database`` (not a raw
    ``SQLModel.metadata.create_all``) because PostgreSQL fresh installs get
    partitioned DDL for ``sqlery_queued_job`` with a composite PK — plain
    ``create_all`` produces a schema the ``sqlery_registry`` FK cannot
    satisfy. Mirrors ``tests/test_standalone_lifecycle_partitioned.py::
    _make_pg_async_backend``. Idempotent, so no explicit drop/cleanup is
    needed between runs (same convention as that file).
    """
    pg_url = _pg_url()
    _db_mod._engine = None
    _db_mod.init_database(pg_url)
    _db_mod.reset_async_engine()
    eng = _db_mod.get_async_engine(pg_url)
    yield eng
    await eng.dispose()
    _db_mod.reset_async_engine()


@pytest.mark.postgres
async def test_async_e2e_standalone_pg(_standalone_async_engine_pg):
    """(async, standalone, postgres) — AsyncWorker drives a job to terminal state on PG."""
    factory = _db_mod.get_async_session_factory()
    async with factory() as s:
        job = QueuedJob(
            task_path=f"{__name__}._e2e_async_job",
            kwargs={"a": 10, "b": 20},
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
        worker_id="async-e2e-standalone-pg", poll_interval=0.01,
    )
    run_task = asyncio.create_task(worker.run(max_jobs=1))
    await asyncio.wait_for(run_task, timeout=10)

    status = await backend.aget_status(job_id)
    assert status == "success", f"expected 'success' but got {status!r}"

    async with factory() as s:
        row = (
            await s.execute(select(QueuedJob).where(QueuedJob.id == job_id))
        ).scalars().first()
    assert row.output == "30", f"expected output '30' but got {row.output!r}"


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


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
@pytest.mark.xfail(
    reason=(
        "Real bug (found by this cell, not introduced by it): "
        "DjangoAsyncBackend._aclaim_job_postgres "
        "(src/sqlery/django_sqlery/async_backend.py:121) calls "
        "`connection.acursor()`, which does not exist on Django's "
        "DatabaseWrapper in any released version (confirmed absent on "
        "Django 6.0.5 here; the module's own docstring assumed it against "
        "5.2.14 but it was never verified against real PG until this test). "
        "A correct fix needs either a sync_to_async thread-offload of "
        "connection.cursor() (forbidden by the project rule documented in "
        "this module's docstring) or a separate native-async psycopg "
        "connection outside Django's ORM connection wrapper — both are "
        "architectural decisions, not a quick-task fix."
    ),
    strict=True,
)
async def test_async_e2e_django_pg():
    """(async, django, postgres) — same cell as test_async_e2e_django, on the PG rail.

    Django's test DB is chosen once per session from ``SQLERY_TEST_PG_URL``
    (``tests/settings.py``), not per test, so this is a distinct
    ``@pytest.mark.postgres`` item rather than a parametrized variant: the
    SQLite unit rail (``-m "not postgres"``) deselects it, and the strict
    postgres rail (``-m postgres``) selects it while the whole session's
    Django DB is already PostgreSQL.
    """
    from sqlery.django_sqlery.async_backend import DjangoAsyncBackend
    from sqlery.django_sqlery.models import QueuedJob as DjangoQueuedJob

    job = await DjangoQueuedJob.objects.acreate(
        task_path=f"{__name__}._e2e_async_job",
        kwargs={"a": 6, "b": 7},
        queue_name="default",
        status="queued",
    )

    backend = DjangoAsyncBackend()
    worker = AsyncWorker(
        backend=backend, queues=["default"],
        worker_id="async-e2e-django-pg", poll_interval=0.01,
    )
    run_task = asyncio.create_task(worker.run(max_jobs=1))
    await asyncio.wait_for(run_task, timeout=10)

    refreshed = await DjangoQueuedJob.objects.aget(id=job.id)
    assert refreshed.status == "success", (
        f"expected 'success' but got {refreshed.status!r}"
    )
