"""Ready queue (#139): which job the claim algorithm decides to run.

Pure decision tests: each one sets up one scenario ("in scenario X the algorithm decides Y")
and asserts the decision, never an internal call. Every scenario runs against the standalone
(SQLAlchemy) backend on SQLite and on PostgreSQL (``SQLERY_TEST_PG_URL``, skipped without it)
and against the Django backend on whichever database Django is configured for (SQLite, or
PostgreSQL when the URL is set). The async claim paths run the scenarios that concern them.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.pg_url import sqlalchemy_pg_url

PG_URL = os.environ.get("SQLERY_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")

TASK = dict(task_path="operator.add", kwargs={"_args": [1, 2]})
BLOCKED = 30  # more than any candidate page the claim reads at once


def _spec(**kw):
    base = dict(
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
    )
    base.update(TASK)
    base.update(kw)
    return base


class Mode:
    """One integration mode: the same scenario vocabulary over either backend."""

    pg = False

    def __init__(self, backend):
        self.backend = backend

    # -- scenario vocabulary --------------------------------------------------------------
    def new_job(self, **kw):
        return self.backend.create_job(**_spec(**kw))

    def claim(self, queue="default"):
        # An ephemeral claimer id (as the Lambda / HTTP-trigger paths use): no Worker row.
        return self.backend.claim_job([queue], "trigger-test")

    def finish(self, job, ok=True):
        from sqlery.core.attempts import attempt_of

        handle = attempt_of(job)
        if ok:
            return self.backend.mark_job_success(job.id, output="3", attempt=handle)
        return self.backend.mark_job_failed(job.id, error="boom", attempt=handle)

    def job(self, job_id):
        return self.backend.get_job_by_id(job_id)

    def status(self, job_id):
        return self.job(job_id).status

    def limit_tag(self, tag, limit):
        raise NotImplementedError

    def count_dependency_checks(self, monkeypatch) -> list:
        raise NotImplementedError

    def set_future_schedule(self, job_id, when):
        raise NotImplementedError

    def drop_ready_entry(self, job_id):
        raise NotImplementedError

    def raw_status(self, job_id, status):
        raise NotImplementedError


class StandaloneMode(Mode):
    def __init__(self, backend, pg, monkeypatch):
        super().__init__(backend)
        self.pg = pg
        self._monkeypatch = monkeypatch

    def _ids(self, model, column):
        from sqlalchemy import select

        with self.backend._get_session() as session:
            return set(session.execute(select(getattr(model, column))).scalars().all())

    def ready(self):
        from sqlery.core.models import JobReady

        return self._ids(JobReady, "job_id")

    def blocked(self):
        from sqlery.core.models import JobBlocked

        return self._ids(JobBlocked, "job_id")

    def attempts(self, job_id):
        from sqlalchemy import select

        from sqlery.core.models import JobAttempt

        with self.backend._get_session() as session:
            return list(
                session.execute(select(JobAttempt).where(JobAttempt.job_id == job_id)).scalars()
            )

    def limit_tag(self, tag, limit):
        import sqlery.compat as compat

        values = {"TAG_CONCURRENCY_LIMITS": {tag: limit}, "TAG_RATE_LIMITS": {}}
        real = compat.get_config
        self._monkeypatch.setattr(compat, "get_config", lambda n, d=None: values.get(n, real(n, d)))

    def count_dependency_checks(self, monkeypatch):
        from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

        calls = []
        real = SQLAlchemyBackend._judge_dependencies

        def counting(session, deps):
            calls.append(list(deps))
            return real(session, deps)

        monkeypatch.setattr(SQLAlchemyBackend, "_judge_dependencies", staticmethod(counting))
        return calls

    def set_future_schedule(self, job_id, when):
        from sqlalchemy import select

        from sqlery.core.models import QueuedJob

        with self.backend._get_session() as session:
            job = session.execute(select(QueuedJob).where(QueuedJob.id == job_id)).scalars().one()
            job.scheduled_at = when
            session.add(job)
            session.commit()

    def drop_ready_entry(self, job_id):
        from sqlalchemy import delete

        from sqlery.core.models import JobReady

        with self.backend._get_session() as session:
            session.execute(delete(JobReady).where(JobReady.job_id == job_id))
            session.commit()

    def raw_status(self, job_id, status):
        from sqlalchemy import update

        from sqlery.core.models import QueuedJob

        with self.backend._get_session() as session:
            session.execute(update(QueuedJob).where(QueuedJob.id == job_id).values(status=status))
            session.commit()

    def ready_is_partitioned(self):
        from sqlalchemy import text

        with self.backend._get_session() as session:
            return _partition_layout(session.connection(), text)

    def promote_staged(self):
        from sqlalchemy import text

        from sqlery.core.scheduler import promote_due_scheduled_jobs

        with self.backend._get_session() as session:
            # The promotion INSERT names webhook_events, which the standalone DDL lacks (a
            # defect independent of #139): give this test database the column.
            session.execute(
                text(
                    "ALTER TABLE sqlery_queued_job "
                    "ADD COLUMN IF NOT EXISTS webhook_events JSON NOT NULL DEFAULT '[]'::json"
                )
            )
            session.execute(text("UPDATE sqlery_scheduled_job SET scheduled_at = now()"))
            session.commit()
        with self.backend.raw_cursor() as cur:
            return promote_due_scheduled_jobs(cur)


class DjangoMode(Mode):
    def __init__(self, backend, pg, settings):
        super().__init__(backend)
        self.pg = pg
        self._settings = settings

    def ready(self):
        from sqlery.django_sqlery.models import JobReady

        return set(JobReady.objects.values_list("job_id", flat=True))

    def blocked(self):
        from sqlery.django_sqlery.models import JobBlocked

        return set(JobBlocked.objects.values_list("job_id", flat=True))

    def attempts(self, job_id):
        from sqlery.django_sqlery.models import JobAttempt

        return list(JobAttempt.objects.filter(job_id=job_id))

    def limit_tag(self, tag, limit):
        self._settings.DJANGO_SQL_JOBS = {"TAG_CONCURRENCY_LIMITS": {tag: limit}}

    def count_dependency_checks(self, monkeypatch):
        from sqlery.django_sqlery.models import QueuedJob

        calls = []
        real = QueuedJob.check_dependencies_met

        def counting(job):
            calls.append(list(job.dependencies))
            return real(job)

        monkeypatch.setattr(QueuedJob, "check_dependencies_met", counting)
        return calls

    def set_future_schedule(self, job_id, when):
        from sqlery.django_sqlery.models import QueuedJob

        job = QueuedJob.objects.get(id=job_id)
        job.scheduled_at = when
        job.save(update_fields=["scheduled_at"])

    def drop_ready_entry(self, job_id):
        from sqlery.django_sqlery.models import JobReady

        JobReady.objects.filter(job_id=job_id).delete()

    def raw_status(self, job_id, status):
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("UPDATE sqlery_queued_job SET status = %s WHERE id = %s", [status, job_id])

    def ready_is_partitioned(self):
        from django.db import connection

        from sqlalchemy import text  # noqa: F401  (only used for the shared helper's signature)

        class _Conn:
            def execute(self, statement, params=None):
                with connection.cursor() as cur:
                    cur.execute(str(statement), params or [])
                    return _Result(cur.fetchall())

        return _partition_layout(_Conn(), str)

    def promote_staged(self):
        from django.db import connection

        from sqlery.core.scheduler import promote_due_scheduled_jobs

        with connection.cursor() as cur:
            # The promotion INSERT omits two NOT NULL webhook columns of the Django schema (a
            # defect independent of #139): give this test database their defaults.
            cur.execute("ALTER TABLE sqlery_queued_job ALTER COLUMN webhook_retries SET DEFAULT 0")
            cur.execute(
                "ALTER TABLE sqlery_queued_job ALTER COLUMN webhook_max_retries SET DEFAULT 3"
            )
            cur.execute("UPDATE sqlery_scheduled_job SET scheduled_at = now()")
        with self.backend.raw_cursor() as cur:
            return promote_due_scheduled_jobs(cur)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows


def _partition_layout(conn, wrap):
    """(relkind of the ready table, sorted partition names, primary key columns)."""
    kind = conn.execute(
        wrap("SELECT relkind FROM pg_class WHERE relname = 'sqlery_job_ready'")
    ).fetchall()[0][0]
    parts = sorted(
        r[0]
        for r in conn.execute(
            wrap(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'sqlery_job_ready'::regclass"
            )
        ).fetchall()
    )
    pk = sorted(
        r[0]
        for r in conn.execute(
            wrap(
                "SELECT a.attname FROM pg_index x JOIN pg_attribute a "
                "ON a.attrelid = x.indrelid AND a.attnum = ANY(x.indkey) "
                "WHERE x.indrelid = 'sqlery_job_ready'::regclass AND x.indisprimary"
            )
        ).fetchall()
    )
    return kind, parts, pk


@pytest.fixture(
    params=[
        "standalone-sqlite",
        pytest.param("standalone-pg", marks=needs_pg),
        pytest.param("django", marks=pytest.mark.django_db(transaction=True)),
    ]
)
def mode(request, tmp_path, monkeypatch):
    if request.param == "django":
        from django.db import connection

        from sqlery.django_sqlery.backend import DjangoBackend

        settings = request.getfixturevalue("settings")
        yield DjangoMode(DjangoBackend(), connection.vendor == "postgresql", settings)
        return

    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    pg = request.param == "standalone-pg"
    if pg:
        db_mod.init_database(sqlalchemy_pg_url(PG_URL))
    else:
        db_mod.init_database(f"sqlite:///{tmp_path / 'ready.db'}")
    engine = db_mod.get_engine()
    try:
        yield StandaloneMode(SQLAlchemyBackend(), pg, monkeypatch)
    finally:
        if pg:
            with engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
                conn.commit()
        engine.dispose()
        db_mod._engine = None


# ---- enqueue and claim ---------------------------------------------------------------------
def test_enqueued_job_is_claimable(mode):
    job = mode.new_job()
    assert mode.ready() == {job.id}
    claimed = mode.claim()
    assert claimed is not None and claimed.id == job.id
    assert mode.status(job.id) == "running"


def test_claim_removes_the_entry_and_opens_an_attempt(mode):
    job = mode.new_job()
    claimed = mode.claim()
    assert claimed.id == job.id
    assert mode.ready() == set() and mode.blocked() == set()
    opened = mode.attempts(job.id)
    assert len(opened) == 1 and opened[0].finished_at is None
    assert mode.claim() is None  # nothing left: the same job is not claimed twice


def test_claim_order_is_priority_then_age_within_the_requested_queue(mode):
    low = mode.new_job(priority=0)
    high = mode.new_job(priority=5)
    other_queue = mode.new_job(priority=99, queue_name="other")
    older = mode.new_job(priority=5)
    order = [mode.claim().id, mode.claim().id, mode.claim().id]
    assert order == [high.id, older.id, low.id]
    assert mode.claim() is None
    assert mode.claim("other").id == other_queue.id


def test_job_scheduled_in_the_future_is_not_claimable_until_due(mode):
    due_soon = datetime.now(UTC) + timedelta(seconds=1)
    job = mode.new_job(scheduled_at=due_soon)
    assert mode.claim() is None
    assert mode.ready() == {job.id}  # waiting in the ready table, not lost
    delay = mode.backend.next_due_delay(["default"])
    assert delay is not None and 0 < delay <= 1.0
    time.sleep(1.1)
    assert mode.claim().id == job.id
    assert mode.backend.next_due_delay(["default"]) is None


def test_rescheduling_a_queued_job_moves_its_entry(mode):
    job = mode.new_job(scheduled_at=datetime.now(UTC) + timedelta(hours=1))
    assert mode.claim() is None
    mode.set_future_schedule(job.id, None)  # "enqueue now"
    assert mode.claim().id == job.id


def test_claim_by_id_takes_the_entry_and_a_second_claim_gets_nothing(mode):
    job = mode.new_job()
    other = mode.new_job()
    claimed = mode.backend.claim_job_by_id(job.id, "trigger-test")
    assert claimed is not None and claimed.id == job.id
    assert mode.ready() == {other.id}
    assert mode.backend.claim_job_by_id(job.id, "trigger-test") is None


# ---- dependencies --------------------------------------------------------------------------
def test_dependency_blocked_candidate_moves_to_the_blocked_set_and_is_not_examined_again(
    mode, monkeypatch
):
    parent = mode.new_job()
    parent_running = mode.claim()
    assert parent_running.id == parent.id
    child = mode.new_job(dependencies=[parent.id])
    checks = mode.count_dependency_checks(monkeypatch)

    assert mode.claim() is None
    assert mode.blocked() == {child.id} and mode.ready() == set()
    examined = len(checks)
    assert examined >= 1  # judged when the claim met it (and once more after the move)

    for _ in range(3):
        assert mode.claim() is None
    assert len(checks) == examined, "a blocked job is not re-examined by later claims"


def test_dependency_succeeding_makes_the_dependent_claimable(mode):
    parent = mode.new_job()
    child = mode.new_job(dependencies=[parent.id])
    first = mode.claim()
    assert first.id == parent.id
    assert mode.claim() is None and mode.blocked() == {child.id}

    mode.finish(first, ok=True)

    assert mode.blocked() == set() and mode.ready() == {child.id}
    assert mode.claim().id == child.id


def test_dependency_failing_fails_the_dependent_as_before(mode):
    parent = mode.new_job()
    child = mode.new_job(dependencies=[parent.id])
    first = mode.claim()
    assert mode.claim() is None and mode.blocked() == {child.id}

    mode.finish(first, ok=False)

    failed = mode.job(child.id)
    assert failed.status == "failed" and failed.termination_reason == "dependency_failed"
    assert mode.ready() == set() and mode.blocked() == set()
    assert mode.claim() is None


def test_dependency_cancelled_fails_the_dependent(mode):
    parent = mode.new_job()
    child = mode.new_job(dependencies=[parent.id])
    assert mode.claim().id == parent.id
    assert mode.claim() is None and mode.blocked() == {child.id}
    # (a running job cannot be cancelled: use a second, still-queued dependency)
    queued_parent = mode.new_job()
    waiting = mode.new_job(dependencies=[queued_parent.id], priority=-5)
    assert mode.backend.cancel_job(queued_parent.id)
    assert mode.claim() is None
    assert mode.status(waiting.id) == "failed"
    assert mode.blocked() == {child.id}  # still waits for its running dependency


def test_dependency_that_is_gone_fails_the_dependent(mode):
    parent = mode.new_job(job_name="replace-me")
    child = mode.new_job(dependencies=[parent.id])
    assert mode.claim().id == parent.id  # parent runs; child blocked behind it
    assert mode.claim() is None and mode.blocked() == {child.id}
    mode.new_job(job_name="replace-me")  # the named job is replaced: the old one is deleted
    assert mode.status(child.id) == "failed"
    assert mode.blocked() == set()


def test_many_blocked_candidates_ahead_do_not_hide_a_runnable_job(mode):
    parent = mode.new_job(priority=-1)
    assert mode.claim().id == parent.id
    blocked_ids = {mode.new_job(priority=10, dependencies=[parent.id]).id for _ in range(BLOCKED)}
    runnable = mode.new_job(priority=0)

    claimed = mode.claim()

    assert claimed is not None and claimed.id == runnable.id
    assert mode.blocked() == blocked_ids and mode.ready() == set()


def test_all_candidates_blocked_returns_nothing(mode):
    parent = mode.new_job()
    assert mode.claim().id == parent.id
    for _ in range(BLOCKED):
        mode.new_job(dependencies=[parent.id])
    assert mode.claim() is None


# ---- tags ----------------------------------------------------------------------------------
def test_tag_limited_candidate_stays_claimable(mode):
    mode.limit_tag("api", 1)
    running = mode.new_job(tags=["api"])
    first = mode.claim()
    assert first.id == running.id
    waiting = mode.new_job(tags=["api"])

    assert mode.claim() is None
    assert mode.ready() == {waiting.id} and mode.blocked() == set()

    mode.finish(first, ok=True)
    assert mode.claim().id == waiting.id


def test_tag_limited_jobs_ahead_do_not_hide_a_runnable_job(mode):
    mode.limit_tag("api", 1)
    running = mode.new_job(tags=["api"])
    assert mode.claim().id == running.id
    limited = {mode.new_job(priority=10, tags=["api"]).id for _ in range(BLOCKED)}
    runnable = mode.new_job(priority=0)
    assert mode.claim().id == runnable.id
    assert mode.ready() == limited  # all still claimable once the tag frees up


# ---- unclaimable ---------------------------------------------------------------------------
def test_cancelled_job_is_not_claimable(mode):
    job = mode.new_job()
    keep = mode.new_job(priority=-1)
    assert mode.backend.cancel_job(job.id)
    assert mode.ready() == {keep.id}
    assert mode.claim().id == keep.id
    assert mode.status(job.id) == "failed"


def test_replaced_named_job_is_not_claimable(mode):
    old = mode.new_job(job_name="nightly")
    new = mode.new_job(job_name="nightly")
    assert mode.ready() == {new.id}
    assert mode.claim().id == new.id
    assert mode.job(old.id) is None or mode.job(old.id).status != "queued"


def test_expired_job_is_not_claimed_and_leaves_the_queue(mode):
    job = mode.new_job(ttl=1)
    keep = mode.new_job(priority=-1)
    time.sleep(1.2)
    claimed = mode.claim()
    assert claimed.id == keep.id
    assert mode.status(job.id) == "failed" and mode.ready() == set()


def test_stale_entry_of_a_job_that_is_no_longer_queued_is_dropped_by_the_claim(mode):
    ghost = mode.new_job(priority=10)
    runnable = mode.new_job(priority=0)
    mode.raw_status(ghost.id, "failed")  # bypasses the bookkeeping: the entry stays
    assert mode.ready() == {ghost.id, runnable.id}
    assert mode.claim().id == runnable.id
    assert mode.ready() == set()
    assert mode.status(ghost.id) == "failed"


# ---- retry and recovery --------------------------------------------------------------------
def test_retry_is_claimable_at_its_scheduled_time(mode):
    from sqlery.core.worker import JobExecutor

    job = mode.new_job(max_retries=1, retry_backoff=1.0)
    first = mode.claim()
    failed = mode.finish(first, ok=False)
    retry = JobExecutor(mode.backend)._retry_job(failed)

    assert mode.ready() == {retry.id}
    assert mode.claim() is None  # backoff not over yet
    time.sleep(1.2)
    claimed = mode.claim()
    assert claimed is not None and claimed.id == retry.id
    assert claimed.parent_job_id == job.id


def test_recovered_zombie_is_claimable_again_after_a_requeue(mode):
    job = mode.new_job()
    first = mode.claim()
    assert mode.claim() is None

    mode.backend.release_job(first.id)  # the requeue of a job its worker abandoned

    assert mode.ready() == {job.id}
    again = mode.claim()
    assert again is not None and again.id == job.id
    attempts = mode.attempts(job.id)
    assert [a.outcome for a in attempts if a.finished_at is not None] == ["abandoned"]
    assert len([a for a in attempts if a.finished_at is None]) == 1


def test_zombie_failed_by_recovery_stays_failed_and_unclaimable(mode):
    job = mode.new_job()
    first = mode.claim()
    assert mode.backend.fail_zombie_job(first.id, "worker died")
    assert mode.ready() == set()
    assert mode.claim() is None
    assert mode.status(job.id) == "failed"


# ---- repair --------------------------------------------------------------------------------
def test_queued_job_that_lost_its_entry_is_restored_by_the_sweep(mode):
    job = mode.new_job()
    mode.drop_ready_entry(job.id)
    assert mode.claim() is None  # nothing to find
    mode.backend.maintain_ready_queue()
    assert mode.ready() == {job.id}
    assert mode.claim().id == job.id


def test_scheduler_leader_restores_a_lost_entry_on_its_tick(mode):
    from sqlery.core.scheduler import Scheduler

    job = mode.new_job()
    mode.drop_ready_entry(job.id)
    Scheduler(mode.backend).run_due_tasks(["default"])
    assert mode.ready() == {job.id}
    assert mode.claim().id == job.id


def test_sweep_does_not_duplicate_or_touch_existing_entries(mode):
    parent = mode.new_job(priority=5)
    waiting = mode.new_job(dependencies=[parent.id], priority=1)
    plain = mode.new_job(priority=0)
    assert mode.claim().id == parent.id
    assert mode.claim().id == plain.id  # `waiting` was met on the way and blocked
    assert mode.blocked() == {waiting.id}
    before = (mode.ready(), mode.blocked())
    mode.backend.maintain_ready_queue()
    mode.backend.maintain_ready_queue()
    assert (mode.ready(), mode.blocked()) == before


def test_sweep_releases_a_blocked_job_whose_dependency_finished_unnoticed(mode):
    parent = mode.new_job()
    child = mode.new_job(dependencies=[parent.id])
    assert mode.claim().id == parent.id
    assert mode.claim() is None and mode.blocked() == {child.id}
    mode.raw_status(parent.id, "success")  # finished without anyone telling the queue
    assert mode.claim() is None
    mode.backend.maintain_ready_queue()
    assert mode.claim().id == child.id


def test_sweep_drops_entries_of_jobs_that_are_no_longer_queued(mode):
    ghost = mode.new_job(priority=3)
    live = mode.new_job(priority=1)
    mode.raw_status(ghost.id, "failed")  # bypasses the bookkeeping: the entry stays
    assert mode.ready() == {ghost.id, live.id}
    mode.backend.maintain_ready_queue()
    assert mode.ready() == {live.id}


def test_retried_failed_job_is_claimable_again(mode):
    job = mode.new_job()
    mode.finish(mode.claim(), ok=False)
    assert mode.claim() is None
    assert mode.backend.retry_failed_jobs() == 1
    assert mode.ready() == {job.id}
    assert mode.claim().id == job.id


# ---- schema and start-up -------------------------------------------------------------------
def test_ready_tables_exist_and_workers_start(mode):
    from sqlery.core.ready import require_ready_tables

    assert mode.backend.ready_tables_exist() is True
    require_ready_tables(mode.backend)


def test_worker_refuses_to_start_without_the_ready_tables():
    from sqlery.core.ready import ReadyTablesMissingError, require_ready_tables

    class Missing:
        def ready_tables_exist(self):
            return False

    with pytest.raises(ReadyTablesMissingError, match="migrat"):
        require_ready_tables(Missing())


@needs_pg
def test_ready_table_is_list_partitioned_by_slot_on_postgres(mode):
    if not mode.pg:
        pytest.skip("this run's database is not PostgreSQL")
    kind, partitions, pk = mode.ready_is_partitioned()
    assert kind == "p"
    assert partitions == ["sqlery_job_ready_0", "sqlery_job_ready_1", "sqlery_job_ready_2"]
    assert pk == ["job_id", "slot"]


@needs_pg
def test_promoted_staged_job_is_claimable(mode):
    if not mode.pg:
        pytest.skip("this run's database is not PostgreSQL")
    staged = mode.new_job(scheduled_at=datetime.now(UTC) + timedelta(days=3))
    assert mode.ready() == set()  # staged jobs are not claimable
    assert mode.claim() is None
    assert mode.promote_staged() == 1
    assert mode.ready() == {staged.id}
    assert mode.claim().id == staged.id


# ---- standalone only: retry chains (the Django model fails dependents of a failed job
# immediately, with or without a retry; that cascade is unchanged) ---------------------------
def test_dependent_waits_for_the_retry_of_a_failed_dependency(mode):
    if not isinstance(mode, StandaloneMode):
        pytest.skip("Django fails dependents of a failed job immediately (unchanged)")
    from sqlery.core.attempts import attempt_of
    from sqlery.core.retry import pending_reason
    from sqlery.core.worker import JobExecutor

    parent = mode.new_job(max_retries=1, retry_backoff=0.2)
    child = mode.new_job(dependencies=[parent.id], priority=-1)
    first = mode.claim()
    assert first.id == parent.id and mode.claim() is None and mode.blocked() == {child.id}

    # The worker fails the job tagged "retry pending", then creates the retry.
    failed = mode.backend.mark_job_failed(
        first.id,
        error="boom",
        termination_reason=pending_reason("exception"),
        attempt=attempt_of(first),
    )
    assert mode.status(child.id) == "queued", "the retry is owed: the dependent keeps waiting"
    retry = JobExecutor(mode.backend)._retry_job(failed)
    assert mode.blocked() == {child.id}

    time.sleep(0.3)
    second = mode.claim()
    assert second.id == retry.id
    mode.finish(second, ok=True)
    assert mode.claim().id == child.id


# ---- concurrency ---------------------------------------------------------------------------
@needs_pg
def test_concurrent_claimers_get_distinct_jobs(mode):
    if not isinstance(mode, StandaloneMode) or not mode.pg:
        pytest.skip("threaded claim race runs on standalone PostgreSQL")
    ids = {mode.new_job().id for _ in range(8)}
    got, lock = [], threading.Lock()

    def run():
        job = mode.claim()
        with lock:
            got.append(job.id if job else None)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == sorted(ids)


# ---- async claim paths ---------------------------------------------------------------------
@pytest.fixture(params=["standalone-sqlite", pytest.param("standalone-pg", marks=needs_pg)])
def async_mode(request, tmp_path, monkeypatch):
    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    db_mod.reset_async_engine()
    pg = request.param == "standalone-pg"
    url = sqlalchemy_pg_url(PG_URL) if pg else f"sqlite:///{tmp_path / 'ready_async.db'}"
    db_mod.init_database(url)
    engine = db_mod.get_engine()
    db_mod.get_async_engine(url)
    try:
        sync_mode = StandaloneMode(SQLAlchemyBackend(), pg, monkeypatch)
        sync_mode.abackend = SQLAlchemyAsyncBackend()
        yield sync_mode
    finally:
        asyncio.run(db_mod.get_async_engine().dispose())
        db_mod.reset_async_engine()
        if pg:
            with engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
                conn.commit()
        engine.dispose()
        db_mod._engine = None


def _aclaim(m):
    async def go():
        return await m.abackend.aclaim_job(["default"], "async-test")

    return asyncio.run(go())


def test_async_claim_takes_the_entry_in_claim_order(async_mode):
    m = async_mode
    low = m.new_job(priority=0)
    high = m.new_job(priority=5)
    first = _aclaim(m)
    assert first.id == high.id and first.status == "running"
    assert m.ready() == {low.id}
    assert _aclaim(m).id == low.id
    assert _aclaim(m) is None


def test_async_claim_skips_cancelled_and_stale_entries(async_mode):
    m = async_mode
    cancelled = m.new_job(priority=10)
    ghost = m.new_job(priority=9)
    runnable = m.new_job(priority=0)
    assert m.backend.cancel_job(cancelled.id)
    m.raw_status(ghost.id, "failed")
    assert _aclaim(m).id == runnable.id
    assert m.ready() == set()


def test_async_next_due_delay_reads_the_ready_queue(async_mode):
    m = async_mode
    assert asyncio.run(m.abackend.anext_due_delay(["default"])) is None
    m.new_job(scheduled_at=datetime.now(UTC) + timedelta(seconds=30))
    delay = asyncio.run(m.abackend.anext_due_delay(["default"]))
    assert delay is not None and 0 < delay <= 30


def test_async_retry_requeue_is_claimable_at_its_scheduled_time(async_mode):
    m = async_mode
    job = m.new_job(max_retries=1, retry_backoff=1.0)
    claimed = _aclaim(m)
    from sqlery.core.attempts import attempt_of

    async def fail_and_requeue():
        await m.abackend.amark_failed(claimed.id, error="boom", attempt=attempt_of(claimed))
        await m.abackend.arequeue_retry(m.job(claimed.id))

    asyncio.run(fail_and_requeue())
    assert _aclaim(m) is None  # backoff not over yet
    time.sleep(1.2)
    retried = _aclaim(m)
    assert retried is not None and retried.parent_job_id == job.id


def test_async_completion_releases_the_dependent(async_mode):
    m = async_mode
    parent = m.new_job()
    child = m.new_job(dependencies=[parent.id])
    claimed = _aclaim(m)
    assert claimed.id == parent.id
    assert m.claim() is None and m.blocked() == {child.id}  # the sync claim blocks it
    from sqlery.core.attempts import attempt_of

    asyncio.run(m.abackend.amark_success(parent.id, "3", attempt=attempt_of(claimed)))
    assert m.blocked() == set() and m.ready() == {child.id}
    assert _aclaim(m).id == child.id
