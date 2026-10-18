"""PostgreSQL coverage for paths added in 0.25.0 that SQLite cannot exercise.

SQLite is single-writer, so races never show up there: these tests hammer the real
PostgreSQL code paths (advisory locks, compare-and-set claims on the partitioned job table,
dependency and retry lookups) from several concurrent connections.

Select with ``-m postgres`` (CI step "Run @pytest.mark.postgres suite"); skipped when
``SQLERY_TEST_PG_URL`` is unset.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from sqlery.core.job import job
from sqlery.core.worker import JobExecutor
from tests.pg_url import sqlalchemy_pg_url

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.environ.get("SQLERY_TEST_PG_URL"),
                       reason="SQLERY_TEST_PG_URL not set"),
]

THREADS = 8


# ---- tasks (module level so import_task resolves them) --------------------------------------
@job
def pg_add(a, b):
    return a + b


@job
def pg_total(numbers):
    return sum(numbers)


# ---- standalone (SQLAlchemy) ------------------------------------------------------------------
@pytest.fixture
def pg_backend(monkeypatch):
    from sqlalchemy import text

    import sqlery.compat as compat
    from sqlery.core import models as _core_models  # noqa: F401
    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    db_mod.init_database(sqlalchemy_pg_url(os.environ["SQLERY_TEST_PG_URL"]))
    engine = db_mod.get_engine()
    monkeypatch.setattr(db_mod, "_engine", engine, raising=False)
    backend = SQLAlchemyBackend()
    monkeypatch.setattr(compat, "_backend", backend, raising=False)
    try:
        yield backend
    finally:
        try:
            with engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
                conn.commit()
        finally:
            engine.dispose()
            db_mod._engine = None


def _mk(backend, **kw):
    base = dict(task_path="operator.add", kwargs={"_args": [1, 2]}, queue_name="default",
                priority=0, scheduled_at=None, max_retries=0, retry_backoff=1.0,
                allow_parallel=False, timeout_seconds=None)
    base.update(kw)
    return backend.create_job(**base)


def _config(monkeypatch, **values):
    import sqlery.compat as compat

    real = compat.get_config
    monkeypatch.setattr(compat, "get_config", lambda n, d=None: values.get(n, real(n, d)))


def _drain(backend, limit=50):
    ex = JobExecutor(backend=backend)
    for _ in range(limit):
        j = backend.claim_job(["default"], "w")
        if j is None:
            return
        ex.execute_job(j)


@pytest.mark.standalone_pg
class TestStandalonePostgres:
    def test_claim_job_by_id_has_exactly_one_winner(self, pg_backend):
        j = _mk(pg_backend)
        with ThreadPoolExecutor(THREADS) as pool:
            results = list(pool.map(lambda i: pg_backend.claim_job_by_id(j.id, f"w{i}"),
                                    range(THREADS)))
        winners = [r for r in results if r is not None]
        assert len(winners) == 1 and winners[0].status == "running"

    def test_tag_concurrency_limit_holds_under_concurrent_claims(self, pg_backend, monkeypatch):
        _config(monkeypatch, TAG_CONCURRENCY_LIMITS={"api": 2}, TAG_RATE_LIMITS={})
        for _ in range(THREADS + 2):
            _mk(pg_backend, tags=["api"])
        with ThreadPoolExecutor(THREADS) as pool:
            claimed = [r for r in pool.map(lambda i: pg_backend.claim_job(["default"], f"w{i}"),
                                           range(THREADS)) if r is not None]
        # The advisory lock serialises check + claim: never more than the limit.
        assert len(claimed) == 2
        assert pg_backend.count_running_with_tag("api") == 2

    def test_tag_rate_limit_counts_recent_starts(self, pg_backend, monkeypatch):
        _config(monkeypatch, TAG_CONCURRENCY_LIMITS={}, TAG_RATE_LIMITS={"slow": "2/m"})
        for _ in range(5):
            _mk(pg_backend, tags=["slow"])
        got = [pg_backend.claim_job(["default"], "w") for _ in range(5)]
        assert len([g for g in got if g is not None]) == 2

    def test_dependency_orders_execution(self, pg_backend):
        parent = _mk(pg_backend, priority=0)
        child = _mk(pg_backend, priority=10, dependencies=[parent.id])
        assert pg_backend.claim_job(["default"], "w").id == parent.id
        assert pg_backend.claim_job(["default"], "w") is None      # parent still running
        assert pg_backend.get_job_by_id(child.id).status == "queued"

    def test_dependency_is_judged_by_newest_retry_attempt(self, pg_backend):
        parent = _mk(pg_backend, max_retries=1)
        child = _mk(pg_backend, dependencies=[parent.id])
        claimed = pg_backend.claim_job_by_id(parent.id, "w")
        pg_backend.mark_job_failed(claimed.id, error="boom")
        retry = _mk(pg_backend, parent_job_id=parent.id, retry_count=1)
        # parent failed but a retry exists: the dependent neither fails nor runs yet
        got = pg_backend.claim_job(["default"], "w")
        assert got.id == retry.id
        assert pg_backend.get_job_by_id(child.id).status == "queued"
        pg_backend.mark_job_success(retry.id, output="ok")
        assert pg_backend.get_latest_retry(parent.id).id == retry.id
        assert pg_backend.claim_job(["default"], "w").id == child.id

    def test_revoke_flag_and_latest_retry(self, pg_backend):
        j = _mk(pg_backend)
        assert pg_backend.request_job_revoke(j.id) is False         # queued: nothing to flag
        pg_backend.claim_job_by_id(j.id, "w")
        assert pg_backend.is_job_revoke_requested(j.id) is False
        assert pg_backend.request_job_revoke(j.id) is True
        assert pg_backend.is_job_revoke_requested(j.id) is True

    def test_lost_retry_is_recovered_exactly_once(self, pg_backend):
        from unittest.mock import patch

        from sqlery.core.retry import is_retry_pending

        j = _mk(pg_backend, task_path="operator.truediv", kwargs={"_args": [1, 0]}, max_retries=3)
        claimed = pg_backend.claim_job_by_id(j.id, "w")
        ex = JobExecutor(backend=pg_backend)
        with patch.object(JobExecutor, "_retry_job", side_effect=SystemExit("worker killed")):
            with pytest.raises(SystemExit):
                ex.execute_job(claimed)
        row = pg_backend.get_job_by_id(j.id)
        assert row.status == "failed" and is_retry_pending(row.termination_reason)
        assert pg_backend.get_latest_retry(j.id) is None
        assert ex.recover_lost_retries(grace_seconds=0) == 1
        assert ex.recover_lost_retries(grace_seconds=0) == 0
        assert pg_backend.get_latest_retry(j.id).parent_job_id == j.id

    def test_apply_async_and_chain_end_to_end(self, pg_backend):
        from sqlery import chain, chord, group
        from sqlery.core.result import apply_async_job

        single = apply_async_job(pg_add.task_path, {"_args": [20, 22]}, backend=pg_backend)
        flow = (pg_add.s(1, 2) | pg_add.s(10)).apply_async()
        fan = chord(group(pg_add.s(i, 1) for i in range(3)), pg_total.s()).apply_async()
        _drain(pg_backend)
        assert single.get(timeout=1) == 42
        assert flow.get(timeout=1) == 13
        assert fan.get(timeout=1) == 6 and fan.parent.get(timeout=1) == [1, 2, 3]
        assert isinstance(chain(pg_add.s(1, 1), pg_add.s(1)), chain)


# ---- Django ORM -------------------------------------------------------------------------------
@pytest.fixture
def django_pg():
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("Django DB is not PostgreSQL in this run")


def _dj_job(**kw):
    from sqlery.models import QueuedJob

    kw.setdefault("task_path", "tests.tasks.dummy_task")
    kw.setdefault("queue_name", "default")
    kw.setdefault("status", "queued")
    return QueuedJob.objects.create(**kw)


@pytest.mark.django_db(transaction=True)
class TestDjangoPostgres:
    def test_claim_job_by_id_has_exactly_one_winner(self, django_pg):
        from django.db import connection

        from sqlery.compat import get_backend

        j = _dj_job()
        backend = get_backend()

        def attempt(i):
            try:
                return backend.claim_job_by_id(j.id, f"w{i}")
            finally:
                connection.close()

        with ThreadPoolExecutor(THREADS) as pool:
            results = list(pool.map(attempt, range(THREADS)))
        assert len([r for r in results if r is not None]) == 1

    def test_tag_limit_is_enforced_by_the_shared_claim_path(self, django_pg, settings):
        from sqlery.compat import get_backend
        from sqlery.core.claiming import claim_next_job_with_queue_priority

        settings.DJANGO_SQL_JOBS = {"TAG_CONCURRENCY_LIMITS": {"api": 1}}
        for _ in range(3):
            _dj_job(tags=["api"])
        other = _dj_job(priority=-5)
        backend = get_backend()
        from django.db import transaction

        with transaction.atomic():      # as DjangoBackend.claim_job does around the claim
            first = claim_next_job_with_queue_priority(None, backend, queues=["default"])
        with transaction.atomic():
            second = claim_next_job_with_queue_priority(None, backend, queues=["default"])
        assert first.tags == ["api"] and second.id == other.id      # limited jobs skipped
        assert backend.count_running_with_tag("api") == 1

    def test_dependency_follows_retry_child(self, django_pg):
        parent = _dj_job(status="failed", max_retries=1)
        child = _dj_job(dependencies=[parent.id])
        retry = _dj_job(parent_job_id=parent.id, retry_count=1)
        assert child.check_dependencies_met() == (False, [])        # retry pending
        retry.status = "success"
        retry.save(update_fields=["status"])
        assert child.check_dependencies_met() == (True, [])

    def test_lost_retry_is_recovered_exactly_once(self, django_pg):
        from unittest.mock import patch

        from sqlery.core.retry import is_retry_pending
        from sqlery.executor import TaskExecutor
        from sqlery.models import QueuedJob

        j = _dj_job(task_path="tests.test_retry_recovery.always_fails", max_retries=3)
        ex = TaskExecutor()
        with patch.object(TaskExecutor, "_retry_job", side_effect=SystemExit("worker killed")):
            with pytest.raises(SystemExit):
                ex.execute_job(j)
        j.refresh_from_db()
        assert j.status == "failed" and is_retry_pending(j.termination_reason)
        assert ex.recover_lost_retries(grace_seconds=0) == 1
        assert ex.recover_lost_retries(grace_seconds=0) == 0
        assert QueuedJob.objects.filter(parent_job_id=j.id).count() == 1

    def test_revoke_flag(self, django_pg):
        from sqlery.compat import get_backend

        backend = get_backend()
        j = _dj_job(status="running")
        assert backend.request_job_revoke(j.id) is True
        assert backend.is_job_revoke_requested(j.id) is True
        assert backend.request_job_revoke(_dj_job().id) is False
