"""Fenced execution attempts (#138): which completions the algorithm accepts or rejects.

Pure decision tests: each test sets up one scenario and asserts what the attempt fence
decides. Every scenario runs against the standalone (SQLAlchemy) backend on SQLite and on
PostgreSQL (``SQLERY_TEST_PG_URL``, skipped without it) and against the Django backend on
whichever database Django is configured for (SQLite, or PostgreSQL when the URL is set).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from tests.pg_url import sqlalchemy_pg_url

PG_URL = os.environ.get("SQLERY_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")

TASK = dict(task_path="operator.add", kwargs={"_args": [1, 2]})


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

    def __init__(self, backend):
        self.backend = backend

    def new_job(self, **kw):
        return self.backend.create_job(**_spec(**kw))

    def claim(self):
        # An ephemeral claimer id (as the Lambda / HTTP-trigger paths use): no Worker row.
        return self.backend.claim_job(["default"], "trigger-test")

    def job(self, job_id):
        return self.backend.get_job_by_id(job_id)

    def row(self, job_id):
        j = self.job(job_id)
        return (j.status, j.version, j.output, j.error, j.termination_reason)


class StandaloneMode(Mode):
    def attempts(self, job_id):
        from sqlmodel import select

        from sqlery.core.models import JobAttempt

        with self.backend._get_session() as session:
            rows = session.exec(
                select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.id)
            ).all()
            return [_as_dict(r) for r in rows]


class DjangoMode(Mode):
    def attempts(self, job_id):
        from sqlery.django_sqlery.models import JobAttempt

        return [_as_dict(r) for r in JobAttempt.objects.filter(job_id=job_id).order_by("id")]


def _as_dict(r):
    return dict(
        id=r.id,
        token=r.token,
        worker_id=r.worker_id,
        worker_pid=r.worker_pid,
        child_pid=r.child_pid,
        open=r.finished_at is None,
        outcome=r.outcome,
    )


@pytest.fixture(
    params=[
        "standalone-sqlite",
        pytest.param("standalone-pg", marks=needs_pg),
        pytest.param("django", marks=pytest.mark.django_db(transaction=True)),
    ]
)
def mode(request, tmp_path):
    if request.param == "django":
        from sqlery.django_sqlery.backend import DjangoBackend

        yield DjangoMode(DjangoBackend())
        return

    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    if request.param == "standalone-pg":
        db_mod.init_database(sqlalchemy_pg_url(PG_URL))
    else:
        db_mod.init_database(f"sqlite:///{tmp_path / 'attempts.db'}")
    engine = db_mod.get_engine()
    try:
        yield StandaloneMode(SQLAlchemyBackend())
    finally:
        if request.param == "standalone-pg":
            with engine.connect() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
                conn.commit()
        engine.dispose()
        db_mod._engine = None


def _claimed(mode):
    mode.new_job()
    job = mode.claim()
    assert job is not None
    from sqlery.core.attempts import attempt_of

    handle = attempt_of(job)
    assert handle is not None
    return job, handle


def _fencing_error():
    from sqlery.compat import JobFencingError

    return JobFencingError


# ---- claim ---------------------------------------------------------------------------------
def test_claim_opens_exactly_one_attempt_carried_by_the_job(mode):
    job, handle = _claimed(mode)
    (attempt,) = mode.attempts(job.id)
    assert (attempt["id"], attempt["token"]) == (handle.id, handle.token)
    assert attempt["open"] and attempt["outcome"] is None
    assert attempt["worker_pid"] == os.getpid()


def test_claim_by_id_opens_an_attempt(mode):
    created = mode.new_job()
    job = mode.backend.claim_job_by_id(created.id, "trigger-test")
    from sqlery.core.attempts import attempt_of

    handle = attempt_of(job)
    assert handle is not None
    (attempt,) = mode.attempts(created.id)
    assert attempt["id"] == handle.id and attempt["open"]


def test_every_claim_gets_a_fresh_token(mode):
    first, h1 = _claimed(mode)
    mode.backend.release_job(first.id)
    again = mode.claim()
    from sqlery.core.attempts import attempt_of

    assert attempt_of(again).token != h1.token


# ---- completion ----------------------------------------------------------------------------
def test_completion_with_current_open_attempt_is_accepted(mode):
    job, handle = _claimed(mode)
    done = mode.backend.mark_job_success(job.id, output="3", attempt=handle)
    assert done.status == "success"
    (attempt,) = mode.attempts(job.id)
    assert not attempt["open"] and attempt["outcome"] == "success"


def test_failed_completion_closes_the_attempt_as_failed(mode):
    job, handle = _claimed(mode)
    done = mode.backend.mark_job_failed(job.id, error="boom", attempt=handle)
    assert done.status == "failed"
    (attempt,) = mode.attempts(job.id)
    assert not attempt["open"] and attempt["outcome"] == "failed"


def test_second_completion_of_the_same_attempt_is_rejected(mode):
    job, handle = _claimed(mode)
    mode.backend.mark_job_success(job.id, output="3", attempt=handle)
    before = mode.row(job.id)
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_success(job.id, output="again", attempt=handle)
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_failed(job.id, error="late", attempt=handle)
    assert mode.row(job.id) == before


def test_completion_after_the_attempt_was_abandoned_is_rejected_and_row_untouched(mode):
    job, handle = _claimed(mode)
    assert mode.backend.abandon_attempts(job.id) == 1
    before = mode.row(job.id)
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_success(job.id, output="3", attempt=handle)
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_failed(job.id, error="late", attempt=handle)
    assert mode.row(job.id) == before
    (attempt,) = mode.attempts(job.id)
    assert attempt["outcome"] == "abandoned"


def test_completion_after_zombie_recovery_is_rejected_and_recovery_stands(mode):
    job, handle = _claimed(mode)
    assert mode.backend.fail_zombie_job(job.id, "worker died")
    (attempt,) = mode.attempts(job.id)
    assert not attempt["open"] and attempt["outcome"] == "abandoned"
    after_recovery = mode.row(job.id)
    assert after_recovery[0] == "failed" and after_recovery[4] == "zombie_job"
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_success(job.id, output="3", attempt=handle)
    assert mode.row(job.id) == after_recovery


def test_version_fence_still_rejects_when_the_attempt_is_open(mode):
    job, handle = _claimed(mode)
    before = mode.row(job.id)
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_success(
            job.id, output="3", expected_version=job.version + 99, attempt=handle
        )
    assert mode.row(job.id) == before
    (attempt,) = mode.attempts(job.id)
    assert attempt["open"], "a version-fenced rejection must not consume the attempt"


# ---- recovery ------------------------------------------------------------------------------
def test_requeue_closes_the_open_attempt_and_fences_the_old_holder(mode):
    from sqlery.core.attempts import attempt_of

    job, old = _claimed(mode)
    mode.backend.release_job(job.id)
    (closed,) = mode.attempts(job.id)
    assert not closed["open"] and closed["outcome"] == "abandoned"
    assert mode.job(job.id).status == "queued"

    reclaimed = mode.claim()
    new = attempt_of(reclaimed)
    assert new.id != old.id
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_success(job.id, output="stale", attempt=old)
    assert mode.job(job.id).status == "running"
    assert mode.backend.mark_job_success(job.id, output="3", attempt=new).status == "success"
    assert [a["outcome"] for a in mode.attempts(job.id)] == ["abandoned", "success"]


def test_claim_abandons_a_leftover_open_attempt_of_the_queued_job(mode):
    from sqlery.core.attempts import attempt_of

    job, old = _claimed(mode)
    # A requeue that did not close the attempt (e.g. a raw status reset).
    if isinstance(mode, DjangoMode):
        from sqlery.django_sqlery.models import QueuedJob

        QueuedJob.objects.filter(id=job.id).update(status="queued")
    else:
        from sqlalchemy import update

        from sqlery.core.models import QueuedJob

        with mode.backend._get_session() as session:
            session.exec(update(QueuedJob).where(QueuedJob.id == job.id).values(status="queued"))
            session.commit()
    # A raw status reset bypasses the ready-queue bookkeeping (#139); the sweep restores the
    # entry the job lost.
    mode.backend.maintain_ready_queue()
    new = attempt_of(mode.claim())
    opens = [a for a in mode.attempts(job.id) if a["open"]]
    assert [a["id"] for a in opens] == [new.id]
    with pytest.raises(_fencing_error()):
        mode.backend.mark_job_success(job.id, output="stale", attempt=old)


# ---- readers -------------------------------------------------------------------------------
def test_child_pid_is_written_to_the_attempt_not_the_job_row(mode):
    job, handle = _claimed(mode)
    mode.backend.update_job_child_pid(job.id, 4242, attempt=handle)
    (attempt,) = mode.attempts(job.id)
    assert attempt["child_pid"] == 4242
    assert mode.job(job.id).child_pid in (None, 0)


def test_superseded_worker_cannot_stamp_a_newer_attempt(mode):
    job, old = _claimed(mode)
    mode.backend.release_job(job.id)
    mode.claim()
    mode.backend.update_job_child_pid(job.id, 111, attempt=old)
    assert [a["child_pid"] for a in mode.attempts(job.id)] == [None, None]


def test_liveness_reads_the_claiming_pid_from_the_open_attempt(mode):
    job, _ = _claimed(mode)
    assert mode.job(job.id).worker_pid in (None, 0)
    (record,) = [r for r in mode.backend.get_running_jobs_for_liveness() if r.job_id == job.id]
    assert record.worker_pid == os.getpid()


# ---- the executor carries the claim's attempt ------------------------------------------------
def test_executor_completes_the_claimed_job_through_its_attempt(mode):
    from sqlery.core.worker import JobExecutor

    job, _ = _claimed(mode)
    done = JobExecutor(backend=mode.backend).execute_job(job)
    assert done.status == "success"
    (attempt,) = mode.attempts(job.id)
    assert attempt["outcome"] == "success"


_HOOK: dict = {}


def _task_that_gets_recovered():
    """A task during whose run the daemon declares its worker dead."""
    _HOOK["recover"]()


def test_executor_superseded_by_recovery_records_nothing(mode):
    from sqlery.core.worker import JobExecutor

    mode.new_job(task_path=f"{__name__}._task_that_gets_recovered", kwargs={})
    job = mode.claim()
    _HOOK["recover"] = lambda: mode.backend.fail_zombie_job(job.id, "worker died")
    try:
        JobExecutor(backend=mode.backend).execute_job(job)
    finally:
        _HOOK.clear()
    row = mode.row(job.id)
    assert row[0] == "failed" and row[4] == "zombie_job"
    assert [a["outcome"] for a in mode.attempts(job.id)] == ["abandoned"]


# ---- start-up guard ---------------------------------------------------------------------------
def test_worker_refuses_to_start_without_the_attempt_table():
    from sqlery.core.attempts import AttemptTableMissingError, require_attempt_table

    missing = SimpleNamespace(attempt_table_exists=lambda: False)
    present = SimpleNamespace(attempt_table_exists=lambda: True)
    with pytest.raises(AttemptTableMissingError, match="migrat"):
        require_attempt_table(missing)
    require_attempt_table(present)


def test_standalone_backend_reports_a_missing_attempt_table(tmp_path):
    from sqlalchemy import text

    from sqlery.core.attempts import AttemptTableMissingError, require_attempt_table
    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    db_mod.init_database(f"sqlite:///{tmp_path / 'old.db'}")
    try:
        backend = SQLAlchemyBackend()
        require_attempt_table(backend)
        with db_mod.get_engine().connect() as conn:
            conn.execute(text("DROP TABLE sqlery_job_attempt"))
            conn.commit()
        with pytest.raises(AttemptTableMissingError):
            require_attempt_table(backend)
    finally:
        db_mod.get_engine().dispose()
        db_mod._engine = None


# ---- async worker path (standalone) ------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("dialect", ["sqlite", pytest.param("pg", marks=needs_pg)])
async def test_async_completion_is_fenced_by_the_claim_attempt(dialect):
    from sqlalchemy import select, text
    from sqlmodel import SQLModel

    from sqlery.core.attempts import attempt_of
    from sqlery.core.models import JobAll, QueuedJob
    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.async_backend import SQLAlchemyAsyncBackend

    url = "sqlite+aiosqlite:///:memory:" if dialect == "sqlite" else sqlalchemy_pg_url(PG_URL)
    db_mod.reset_async_engine()
    engine = db_mod.get_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    try:
        backend = SQLAlchemyAsyncBackend()
        factory = db_mod.get_async_session_factory()
        async with factory() as s:
            s.add(QueuedJob(task_path="tests.helpers.noop", queue_name="default"))
            await s.commit()
        job = await backend.aclaim_job(["default"], "w1")
        first = attempt_of(job)
        assert first is not None

        # Recovery declares the worker dead (abandoning the attempt); the late async
        # completion is rejected and the job row keeps the recovery outcome.
        assert await backend.afail_zombie_job(job.id, "worker died")
        async with factory() as s:
            recovered = (await s.execute(select(JobAll.status, JobAll.output))).one()  # in history
        with pytest.raises(_fencing_error()):
            await backend.amark_success(job.id, "late", attempt=first)
        async with factory() as s:
            after = (await s.execute(select(JobAll.status, JobAll.output))).one()
        assert recovered[0] == "failed" and after == recovered
    finally:
        if dialect == "pg":
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        await engine.dispose()
        db_mod.reset_async_engine()
