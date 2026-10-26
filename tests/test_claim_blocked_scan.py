"""Blocked candidates never hide runnable work, and tag locks never block a claim.

Pure decision tests: given one scenario, assert which job the claim algorithm picks.
Covers the standalone (SQLAlchemy) ``claim_job`` and the Django shared claiming path
(``claim_next_job_with_queue_priority``). The lock-contention cases need PostgreSQL
(``SQLERY_TEST_PG_URL``) and are skipped without it.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from tests.pg_url import sqlalchemy_pg_url

BLOCKED = 30  # more than any fixed candidate window
PG_URL = os.environ.get("SQLERY_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")


# ---- standalone (SQLAlchemy) -------------------------------------------------------------
@pytest.fixture(params=["sqlite", pytest.param("pg", marks=needs_pg)])
def backend(request, tmp_path, monkeypatch):
    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    if request.param == "pg":
        db_mod.init_database(sqlalchemy_pg_url(PG_URL))
    else:
        db_mod.init_database(f"sqlite:///{tmp_path / 'scan.db'}")
    engine = db_mod.get_engine()
    try:
        yield SQLAlchemyBackend()
    finally:
        if request.param == "pg":
            with engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
                conn.commit()
        engine.dispose()
        db_mod._engine = None


def _limits(monkeypatch, conc=None):
    import sqlery.compat as compat

    values = {"TAG_CONCURRENCY_LIMITS": conc or {}, "TAG_RATE_LIMITS": {}}
    real = compat.get_config
    monkeypatch.setattr(compat, "get_config", lambda n, d=None: values.get(n, real(n, d)))


def _mk(backend, **kw):
    base = dict(
        task_path="operator.add",
        kwargs={"_args": [1, 2]},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
    )
    base.update(kw)
    return backend.create_job(**base)


def test_standalone_dependency_blocked_jobs_do_not_hide_runnable_job(backend, monkeypatch):
    _limits(monkeypatch)
    parent = _mk(backend, priority=-1)
    backend.claim_job_by_id(parent.id, "w")  # running: dependents stay blocked
    for _ in range(BLOCKED):
        _mk(backend, priority=10, dependencies=[parent.id])
    runnable = _mk(backend, priority=0)
    claimed = backend.claim_job(["default"], "w")
    assert claimed is not None and claimed.id == runnable.id


def test_standalone_tag_limited_jobs_do_not_hide_runnable_job(backend, monkeypatch):
    _limits(monkeypatch, {"api": 1})
    running = _mk(backend, tags=["api"])
    backend.claim_job_by_id(running.id, "w")
    for _ in range(BLOCKED):
        _mk(backend, priority=10, tags=["api"])
    runnable = _mk(backend, priority=0)
    claimed = backend.claim_job(["default"], "w")
    assert claimed is not None and claimed.id == runnable.id


def test_standalone_all_candidates_blocked_returns_none(backend, monkeypatch):
    _limits(monkeypatch, {"api": 1})
    running = _mk(backend, tags=["api"])
    backend.claim_job_by_id(running.id, "w")
    for _ in range(BLOCKED):
        _mk(backend, tags=["api"])
    assert backend.claim_job(["default"], "w") is None


@needs_pg
@pytest.mark.parametrize("backend", ["pg"], indirect=True)
def test_standalone_busy_tag_lock_skips_candidate_without_waiting(backend, monkeypatch):
    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database as db_mod

    _limits(monkeypatch, {"api": 5})
    _mk(backend, priority=10, tags=["api"])
    runnable = _mk(backend, priority=0)
    holder = db_mod.get_engine().connect()
    try:
        holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('api'))"))  # held until commit
        started = time.monotonic()
        claimed = backend.claim_job(["default"], "w")
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert claimed is not None and claimed.id == runnable.id
    assert elapsed < 5


# ---- Django (shared claiming path) --------------------------------------------------------
def _dj_job(**kw):
    from sqlery.models import QueuedJob

    kw.setdefault("task_path", "tests.tasks.dummy_task")
    kw.setdefault("queue_name", "default")
    kw.setdefault("status", "queued")
    return QueuedJob.objects.create(**kw)


def _dj_claim():
    from django.db import transaction

    from sqlery.core.claiming import claim_next_job_with_queue_priority
    from sqlery.django_sqlery.backend import DjangoBackend

    with transaction.atomic():  # as DjangoBackend.claim_job wraps the claim
        return claim_next_job_with_queue_priority(None, DjangoBackend(), queues=["default"])


@pytest.mark.django_db(transaction=True)
def test_django_dependency_blocked_jobs_do_not_hide_runnable_job():
    parent = _dj_job(status="running", priority=-1)
    for _ in range(BLOCKED):
        _dj_job(priority=10, dependencies=[parent.id])
    runnable = _dj_job(priority=0)
    claimed = _dj_claim()
    assert claimed is not None and claimed.id == runnable.id


@pytest.mark.django_db(transaction=True)
def test_django_tag_limited_jobs_do_not_hide_runnable_job(settings):
    settings.DJANGO_SQL_JOBS = {"TAG_CONCURRENCY_LIMITS": {"api": 1}}
    _dj_job(status="running", tags=["api"])
    for _ in range(BLOCKED):
        _dj_job(priority=10, tags=["api"])
    runnable = _dj_job(priority=0)
    claimed = _dj_claim()
    assert claimed is not None and claimed.id == runnable.id


@pytest.mark.django_db(transaction=True)
def test_django_all_candidates_blocked_returns_none(settings):
    settings.DJANGO_SQL_JOBS = {"TAG_CONCURRENCY_LIMITS": {"api": 1}}
    _dj_job(status="running", tags=["api"])
    for _ in range(BLOCKED):
        _dj_job(tags=["api"])
    assert _dj_claim() is None


@pytest.mark.django_db(transaction=True)
def test_django_busy_tag_lock_skips_candidate_without_waiting(settings):
    from django.db import connection, transaction

    from sqlery.django_sqlery.models import TagLock

    if connection.vendor != "postgresql":
        pytest.skip("Django DB is not PostgreSQL in this run")
    settings.DJANGO_SQL_JOBS = {"TAG_CONCURRENCY_LIMITS": {"api": 5}}
    TagLock.objects.create(tag="api")
    _dj_job(priority=10, tags=["api"])
    runnable = _dj_job(priority=0)
    locked, release = threading.Event(), threading.Event()

    def hold():
        try:
            with transaction.atomic():
                list(TagLock.objects.select_for_update().filter(tag="api"))
                locked.set()
                release.wait(15)
        finally:
            connection.close()

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert locked.wait(10)
        started = time.monotonic()
        claimed = _dj_claim()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        t.join()
    assert claimed is not None and claimed.id == runnable.id
    assert elapsed < 5
