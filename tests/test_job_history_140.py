# ruff: noqa: F811
"""Insert-only job history (#140): where a job lives, and what the algorithms decide about it.

Pure decision tests: each one sets up one scenario ("in scenario X the algorithm decides Y")
and asserts the decision, never an internal call. Every scenario runs against the standalone
(SQLAlchemy) backend on SQLite and on PostgreSQL (``SQLERY_TEST_PG_URL``, skipped without it)
and against the Django backend on whichever database Django is configured for (SQLite, or
PostgreSQL when the URL is set). The async completion paths run the scenarios that concern them.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_ready_queue_139 import (  # noqa: F401  (fixtures and the scenario vocabulary)
    DjangoMode,
    StandaloneMode,
    async_mode,
    mode,
    needs_pg,
)


# ---- vocabulary: where jobs are -------------------------------------------------------------
def live_status(m, job_id):
    """Status of the job in the live table, or None when it is not there."""
    if isinstance(m, StandaloneMode):
        from sqlalchemy import select

        from sqlery.core.models import QueuedJob

        with m.backend._get_session() as s:
            return s.execute(select(QueuedJob.status).where(QueuedJob.id == job_id)).scalar()
    from sqlery.django_sqlery.models import QueuedJob

    return QueuedJob.objects.filter(id=job_id).values_list("status", flat=True).first()


def history_row(m, job_id):
    """``(status, output, error, termination_reason)`` of the job in history, or None."""
    if isinstance(m, StandaloneMode):
        from sqlalchemy import select

        from sqlery.core.models import JobHistory

        with m.backend._get_session() as s:
            row = s.execute(
                select(
                    JobHistory.status,
                    JobHistory.output,
                    JobHistory.error,
                    JobHistory.termination_reason,
                ).where(JobHistory.id == job_id)
            ).first()
            return tuple(row) if row else None
    from sqlery.django_sqlery.models import JobHistory

    return (
        JobHistory.objects.filter(id=job_id)
        .values_list("status", "output", "error", "termination_reason")
        .first()
    )


def history_ids(m):
    if isinstance(m, StandaloneMode):
        from sqlalchemy import select

        from sqlery.core.models import JobHistory

        with m.backend._get_session() as s:
            return set(s.execute(select(JobHistory.id)).scalars().all())
    from sqlery.django_sqlery.models import JobHistory

    return set(JobHistory.objects.values_list("id", flat=True))


def live_ids(m):
    if isinstance(m, StandaloneMode):
        from sqlalchemy import select

        from sqlery.core.models import QueuedJob

        with m.backend._get_session() as s:
            return set(s.execute(select(QueuedJob.id)).scalars().all())
    from sqlery.django_sqlery.models import QueuedJob

    return set(QueuedJob.objects.values_list("id", flat=True))


def sql_scalar(m, sql):
    if isinstance(m, StandaloneMode):
        from sqlalchemy import text

        with m.backend._get_session() as s:
            return s.execute(text(sql)).scalar()
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def sql_exec(m, sql):
    if isinstance(m, StandaloneMode):
        from sqlalchemy import text

        with m.backend._get_session() as s:
            s.execute(text(sql))
            s.commit()
        return
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(sql)


def attempts_of(m, job_id):
    return m.attempts(job_id)


def finish_ok(m, output="3"):
    from sqlery.core.attempts import attempt_of

    job = m.claim()
    m.backend.mark_job_success(job.id, output=output, attempt=attempt_of(job))
    return job


# ---- the terminal transition ------------------------------------------------------------------
def test_success_moves_the_job_out_of_the_live_table_into_history(mode):
    job = mode.new_job()
    claimed = mode.claim()
    assert live_status(mode, job.id) == "running" and history_row(mode, job.id) is None
    done = mode.finish(claimed, ok=True)
    assert done.status == "success" and done.output == "3"
    assert live_status(mode, job.id) is None
    assert history_row(mode, job.id)[:2] == ("success", "3")
    assert mode.ready() == set()


def test_final_failure_moves_the_job_into_history_with_its_error(mode):
    job = mode.new_job()
    claimed = mode.claim()
    mode.finish(claimed, ok=False)
    assert live_status(mode, job.id) is None
    status, _output, error, _reason = history_row(mode, job.id)
    assert (status, error) == ("failed", "boom")


def test_the_attempt_is_closed_in_the_same_transaction_as_the_move(mode):
    job = mode.new_job()
    mode.finish(mode.claim(), ok=True)
    attempts = attempts_of(mode, job.id)
    assert len(attempts) == 1 and attempts[0].outcome == "success"
    assert attempts[0].finished_at is not None


def test_cancelling_a_queued_job_moves_it_into_history_as_failed(mode):
    job = mode.new_job()
    assert mode.backend.cancel_job(job.id) is True
    assert live_status(mode, job.id) is None and mode.ready() == set()
    status, _o, error, _r = history_row(mode, job.id)
    assert (status, error) == ("failed", "Cancelled by user")
    assert mode.claim() is None
    assert mode.backend.cancel_job(job.id) is False  # it is final


def test_a_stale_completion_after_a_requeue_writes_no_history(mode):
    from sqlery.compat import JobFencingError
    from sqlery.core.attempts import attempt_of

    job = mode.new_job()
    first = mode.claim()
    stale = attempt_of(first)
    mode.backend.release_job(job.id)  # recovery put the job back (abandoning the attempt)
    with pytest.raises(JobFencingError):
        mode.backend.mark_job_success(job.id, output="late", attempt=stale)
    assert history_row(mode, job.id) is None
    assert live_status(mode, job.id) == "queued"
    second = mode.claim()  # the job still runs, once
    assert second.id == job.id
    mode.finish(second, ok=True)
    assert history_row(mode, job.id)[:2] == ("success", "3")


def test_a_completion_by_a_superseded_version_writes_no_history(mode):
    from sqlery.compat import JobFencingError

    job = mode.new_job()
    claimed = mode.claim()
    seen_version = claimed.version
    mode.backend.release_job(job.id)
    mode.claim()  # claimed again: the version moved on
    with pytest.raises(JobFencingError):
        mode.backend.mark_job_success(job.id, output="late", expected_version=seen_version)
    assert history_row(mode, job.id) is None


def test_a_late_completion_of_a_job_already_in_history_changes_nothing(mode):
    from sqlery.compat import JobFencingError
    from sqlery.core.attempts import attempt_of

    job = mode.new_job()
    claimed = mode.claim()
    mode.finish(claimed, ok=True)
    before = history_row(mode, job.id)
    with pytest.raises(JobFencingError):
        mode.backend.mark_job_failed(job.id, error="late", attempt=attempt_of(claimed))
    with pytest.raises(JobFencingError):
        mode.backend.mark_job_success(job.id, output="again", expected_version=claimed.version)
    assert history_row(mode, job.id) == before
    # An unfenced caller is ignored without error: the outcome stays.
    mode.backend.mark_job_success(job.id, output="again")
    assert history_row(mode, job.id) == before


# ---- reads ------------------------------------------------------------------------------------
def test_get_job_by_id_finds_a_job_in_history(mode):
    job = mode.new_job()
    mode.finish(mode.claim(), ok=True)
    found = mode.backend.get_job_by_id(job.id)
    assert found is not None and found.status == "success" and found.output == "3"
    assert mode.backend.get_job_by_id(job.id + 1) is None


def test_lists_counts_and_stats_cover_live_and_finished_jobs(mode):
    done = mode.new_job()
    failed = mode.new_job(priority=-1)
    waiting = mode.new_job(priority=-2)
    mode.finish(mode.claim(), ok=True)
    mode.finish(mode.claim(), ok=False)
    assert mode.backend.count_jobs() == 3
    assert mode.backend.count_jobs(status="success") == 1
    assert mode.backend.count_jobs(status="failed") == 1
    assert mode.backend.count_jobs(status="queued") == 1
    assert {j.id for j in mode.backend.get_jobs(status="success")} == {done.id}
    assert {j.id for j in mode.backend.get_jobs()} == {done.id, failed.id, waiting.id}
    stats = mode.backend.get_queue_stats()
    assert (stats["queued"], stats["success"], stats["failed"]) == (1, 1, 1)
    assert mode.backend.get_database_stats()["total_jobs"] == 3


def test_a_dependency_on_a_succeeded_job_in_history_is_met(mode):
    parent = mode.new_job()
    mode.finish(mode.claim(), ok=True)
    assert live_status(mode, parent.id) is None  # the parent is in history
    child = mode.new_job(dependencies=[parent.id])
    claimed = mode.claim()
    assert claimed is not None and claimed.id == child.id


def test_a_dependency_on_a_failed_job_in_history_fails_the_dependent(mode):
    parent = mode.new_job()
    mode.finish(mode.claim(), ok=False)
    child = mode.new_job(dependencies=[parent.id])
    assert mode.claim() is None
    status, _o, error, reason = history_row(mode, child.id)
    assert status == "failed" and reason == "dependency_failed"
    assert str(parent.id) in error


def test_a_dependent_waiting_on_a_running_job_is_released_when_it_moves_to_history(mode):
    parent = mode.new_job()
    child = mode.new_job(dependencies=[parent.id], priority=-1)
    running = mode.claim()
    assert running.id == parent.id and mode.claim() is None
    mode.finish(running, ok=True)
    released = mode.claim()
    assert released is not None and released.id == child.id


def test_a_dependency_on_a_job_that_never_existed_fails_the_dependent(mode):
    child = mode.new_job(dependencies=[987654321])
    assert mode.claim() is None
    assert history_row(mode, child.id)[0] == "failed"


# ---- retries ----------------------------------------------------------------------------------
def test_a_failure_awaiting_its_retry_stays_live_until_the_retry_exists(mode):
    from sqlery.core.attempts import attempt_of
    from sqlery.core.retry import pending_reason
    from sqlery.core.worker import JobExecutor

    job = mode.new_job(max_retries=1, retry_backoff=0.1)
    first = mode.claim()
    failed = mode.backend.mark_job_failed(
        job.id,
        error="boom",
        termination_reason=pending_reason("exception"),
        attempt=attempt_of(first),
    )
    # The chain is not complete yet: the job is marked failed in the live table.
    assert live_status(mode, job.id) == "failed" and history_row(mode, job.id) is None

    retry = JobExecutor(mode.backend)._retry_job(failed)
    # The retry exists: the original is final and left the live table as 'archived'.
    assert live_status(mode, job.id) is None
    assert history_row(mode, job.id)[0] == "archived"
    assert history_row(mode, job.id)[3] != pending_reason("exception")  # the marker is gone
    assert mode.backend.get_latest_retry(job.id).id == retry.id
    assert live_status(mode, retry.id) == "queued"


def test_a_retry_chain_resolves_through_history(mode):
    from sqlery.core.attempts import attempt_of
    from sqlery.core.result import AsyncResult
    from sqlery.core.retry import pending_reason
    from sqlery.core.worker import JobExecutor

    job = mode.new_job(max_retries=1, retry_backoff=0.1)
    first = mode.claim()
    failed = mode.backend.mark_job_failed(
        job.id, error="boom", termination_reason=pending_reason(), attempt=attempt_of(first)
    )
    retry = JobExecutor(mode.backend)._retry_job(failed)
    assert AsyncResult(job.id, backend=mode.backend).state == "PENDING"  # the retry is queued
    time.sleep(0.4)
    second = mode.claim()
    assert second.id == retry.id
    mode.finish(second, ok=True)
    # Both attempts are in history; asking about the first reports the newest attempt.
    assert {job.id, retry.id} <= history_ids(mode)
    result = AsyncResult(job.id, backend=mode.backend)
    assert (result.state, result.output) == ("SUCCESS", "3")
    assert mode.backend.get_latest_retry(retry.id) is None


def test_a_failure_with_no_retry_owed_becomes_final_when_the_marker_is_settled(mode):
    from sqlery.core.attempts import attempt_of
    from sqlery.core.retry import pending_reason

    job = mode.new_job()
    first = mode.claim()
    mode.backend.mark_job_failed(
        job.id,
        error="boom",
        termination_reason=pending_reason("timeout"),
        attempt=attempt_of(first),
    )
    assert live_status(mode, job.id) == "failed"
    mode.backend.settle_retry_pending(job.id)  # the retry budget turned out to be spent
    assert live_status(mode, job.id) is None
    status, _o, _e, reason = history_row(mode, job.id)
    assert (status, reason) == ("failed", "timeout")


def test_retrying_failed_jobs_creates_one_fresh_job_per_failure(mode):
    job = mode.new_job()
    mode.finish(mode.claim(), ok=False)
    assert mode.backend.retry_failed_jobs() == 1
    retry = mode.backend.get_latest_retry(job.id)
    assert live_status(mode, retry.id) == "queued" and retry.retry_count == 0
    assert history_row(mode, job.id)[0] == "failed"  # history is never rewritten
    assert mode.backend.retry_failed_jobs() == 0  # a failure is retried once


# ---- the settle sweep ---------------------------------------------------------------------------
def test_the_sweep_moves_a_stray_finished_row_into_history_but_spares_a_pending_retry(
    mode, monkeypatch
):
    from sqlery.core.retry import pending_reason

    stray = mode.new_job()
    pending = mode.new_job(priority=-1)
    young = mode.new_job(priority=-2)
    first, second = mode.claim(), mode.claim()
    assert {first.id, second.id} == {stray.id, pending.id}
    # Third-party code finished the jobs by raw SQL, long ago; one is awaiting its retry.
    long_ago = "2020-01-01 00:00:00"
    raw_sql = (
        "UPDATE sqlery_queued_job SET status = '{s}', finished_at = '{t}'{extra} WHERE id = {i}"
    )
    sql_exec(mode, raw_sql.format(s="success", t=long_ago, extra="", i=stray.id))
    sql_exec(
        mode,
        raw_sql.format(
            s="failed",
            t=long_ago,
            extra=f", termination_reason = '{pending_reason()}'",
            i=pending.id,
        ),
    )
    swept = mode.backend.maintain_ready_queue()
    assert swept["settled"] == 1
    assert live_status(mode, stray.id) is None and history_row(mode, stray.id)[0] == "success"
    assert live_status(mode, pending.id) == "failed"  # still owed a retry
    assert live_status(mode, young.id) == "queued"


def test_the_sweep_leaves_a_row_that_finished_moments_ago(mode):
    job = mode.new_job()
    mode.claim()
    sql_exec(
        mode,
        f"UPDATE sqlery_queued_job SET status = 'success' WHERE id = {job.id}",
    )
    # (no finished_at yet: raw SQL set the status only) -- and a recent one:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    sql_exec(mode, f"UPDATE sqlery_queued_job SET finished_at = '{now}' WHERE id = {job.id}")
    assert mode.backend.maintain_ready_queue()["settled"] == 0
    assert live_status(mode, job.id) == "success"  # inside the grace period: not touched


# ---- retention ------------------------------------------------------------------------------
def _age_history(m, job_id, days):
    old = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S+00")
    if isinstance(m, StandaloneMode) and not m.pg:
        old = old.replace("+00", "")
    sql_exec(m, f"UPDATE sqlery_job_history SET finished_at = '{old}' WHERE id = {job_id}")


def test_retention_prunes_old_history_and_its_attempts_but_never_live_jobs(mode):
    if mode.pg:
        pytest.skip("PostgreSQL history is partitioned: see the partition reclaim scenario")
    old_done = mode.new_job()
    recent_done = mode.new_job(priority=-1)
    running = mode.new_job(priority=-2)
    queued = mode.new_job(priority=-3)
    for _ in range(2):
        mode.finish(mode.claim(), ok=True)
    mode.claim()  # `running` is claimed and left running
    sql_exec(
        mode,
        "UPDATE sqlery_queued_job SET created_at = '2019-01-01 00:00:00' "
        f"WHERE id IN ({queued.id}, {running.id})",
    )
    _age_history(mode, old_done.id, days=40)

    result = mode.backend.cleanup_jobs(max_age_days=30)
    assert result["deleted"] == 1
    assert history_row(mode, old_done.id) is None
    assert attempts_of(mode, old_done.id) == []  # its finished attempt went with it
    assert history_row(mode, recent_done.id) is not None
    assert len(attempts_of(mode, recent_done.id)) == 1
    assert live_status(mode, queued.id) == "queued"  # live work is never retention's business
    assert live_status(mode, running.id) == "running"
    assert len(attempts_of(mode, running.id)) == 1  # an open attempt is never pruned


def test_keeping_the_newest_n_prunes_older_history_and_their_attempts(mode):
    if mode.pg:
        pytest.skip("PostgreSQL history is partitioned: retention reclaims whole partitions")
    jobs = [mode.new_job(priority=-i) for i in range(4)]
    for _ in jobs:
        mode.finish(mode.claim(), ok=True)
    for age, job in zip((4, 3, 2, 1), jobs):
        _age_history(mode, job.id, days=age)
    result = mode.backend.cleanup_jobs_by_count(keep_count=2)
    assert (result["kept"], result["deleted"]) == (2, 2)
    assert history_ids(mode) == {jobs[2].id, jobs[3].id}
    assert attempts_of(mode, jobs[0].id) == [] and len(attempts_of(mode, jobs[3].id)) == 1


@needs_pg
def test_partition_reclaim_drops_old_history_and_attempt_partitions_but_not_live_jobs(mode):
    if not mode.pg:
        pytest.skip("this run's database is not PostgreSQL")
    old = mode.new_job()
    recent = mode.new_job(priority=-1)
    queued = mode.new_job(priority=-2)
    for _ in range(2):
        mode.finish(mode.claim(), ok=True)
    assert (
        sql_scalar(mode, "SELECT relkind FROM pg_class WHERE relname = 'sqlery_job_history'") == "p"
    )
    assert (
        sql_scalar(mode, "SELECT relkind FROM pg_class WHERE relname = 'sqlery_job_attempt'") == "p"
    )
    # A day long past retention gets its own history and attempt partitions; the old job moves.
    for table, column in (
        ("sqlery_job_history", "finished_at"),
        ("sqlery_job_attempt", "claimed_at"),
    ):
        sql_exec(
            mode,
            f"CREATE TABLE {table}_20200101 PARTITION OF {table} "
            f"FOR VALUES FROM ('2020-01-01 00:00:00+00') TO ('2020-01-02 00:00:00+00')",
        )
    sql_exec(
        mode,
        "UPDATE sqlery_job_history SET finished_at = '2020-01-01 12:00:00+00' "
        f"WHERE id = {old.id}",
    )
    sql_exec(
        mode,
        "UPDATE sqlery_job_attempt SET claimed_at = '2020-01-01 11:00:00+00' "
        f"WHERE job_id = {old.id}",
    )

    result = mode.backend.cleanup_jobs()
    assert result["reclaimed_via_partition_drop"] is True
    assert result["dropped_by_table"]["sqlery_job_history"] == 1
    assert result["dropped_by_table"]["sqlery_job_attempt"] == 1
    assert sql_scalar(mode, "SELECT to_regclass('sqlery_job_history_20200101')") is None
    assert sql_scalar(mode, "SELECT to_regclass('sqlery_job_attempt_20200101')") is None
    assert history_row(mode, old.id) is None and attempts_of(mode, old.id) == []
    assert history_row(mode, recent.id) is not None and len(attempts_of(mode, recent.id)) == 1
    assert live_status(mode, queued.id) == "queued"


@needs_pg
def test_partition_reclaim_keeps_an_attempt_partition_that_has_an_open_attempt(mode):
    if not mode.pg:
        pytest.skip("this run's database is not PostgreSQL")
    running = mode.new_job()
    mode.claim()
    sql_exec(
        mode,
        "CREATE TABLE sqlery_job_attempt_20200101 PARTITION OF sqlery_job_attempt "
        "FOR VALUES FROM ('2020-01-01 00:00:00+00') TO ('2020-01-02 00:00:00+00')",
    )
    sql_exec(
        mode,
        "UPDATE sqlery_job_attempt SET claimed_at = '2020-01-01 11:00:00+00' "
        f"WHERE job_id = {running.id}",
    )
    result = mode.backend.cleanup_jobs()
    assert result["dropped_by_table"]["sqlery_job_attempt"] == 0
    assert sql_scalar(mode, "SELECT to_regclass('sqlery_job_attempt_20200101')") is not None
    assert len(attempts_of(mode, running.id)) == 1


# ---- the data migration ---------------------------------------------------------------------
def _columns(m):
    if isinstance(m, StandaloneMode):
        from sqlery.core.models import JOB_COLUMNS

        return list(JOB_COLUMNS)
    from sqlery.django_sqlery.history import live_columns

    return live_columns()


def _runner(m):
    if isinstance(m, StandaloneMode):
        from sqlalchemy import text

        def run(sql):
            with m.backend._get_session() as s:
                n = s.execute(text(sql)).rowcount
                s.commit()
                return n

        return run
    from django.db import connection

    def run(sql):
        with connection.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount

    return run


def test_the_data_migration_moves_finished_rows_in_batches_and_back(mode):
    from sqlery.core import history

    ids = {}
    for name in ("done1", "done2", "done3", "failed", "pending", "queued", "running"):
        ids[name] = mode.new_job(priority=-len(ids)).id
    # Rows as an installation that predates the history would have them: finished in place.
    sql_exec(mode, "UPDATE sqlery_job_ready SET queue_name = queue_name")  # (ready rows irrelevant)
    stamp = "2024-05-05 05:05:05"
    for name, status in (
        ("done1", "success"),
        ("done2", "success"),
        ("done3", "archived"),
        ("failed", "failed"),
        ("pending", "failed"),
        ("running", "running"),
    ):
        extra = ""
        if name == "pending":
            extra = ", termination_reason = 'retry_pending'"
        if name != "running":
            extra += f", finished_at = '{stamp}'" if name != "done2" else ""  # done2: NULL
        sql_exec(
            mode, f"UPDATE sqlery_queued_job SET status = '{status}'{extra} WHERE id = {ids[name]}"
        )

    run, columns = _runner(mode), _columns(mode)
    moved = history.move_terminal_rows(run, columns, batch=2)
    assert moved == 4  # in three batches of at most two
    assert history_ids(mode) == {ids[n] for n in ("done1", "done2", "done3", "failed")}
    assert live_ids(mode) == {ids[n] for n in ("pending", "queued", "running")}
    # a row that never recorded its finish time gets one (the partition key cannot be NULL)
    assert history_row(mode, ids["done2"]) is not None
    assert history.move_terminal_rows(run, columns, batch=2) == 0  # idempotent

    restored = history.restore_terminal_rows(run, columns, batch=3)
    assert restored == 4 and history_ids(mode) == set()
    assert live_ids(mode) == set(ids.values())


# ---- async completion ------------------------------------------------------------------------
def test_async_completion_moves_the_job_into_history(async_mode):
    from sqlery.core.attempts import attempt_of

    m = async_mode
    ok = m.new_job()
    bad = m.new_job(priority=-1)
    first = asyncio.run(m.abackend.aclaim_job(["default"], "async-test"))
    second = asyncio.run(m.abackend.aclaim_job(["default"], "async-test"))
    asyncio.run(m.abackend.amark_success(first.id, "7", attempt=attempt_of(first)))
    asyncio.run(m.abackend.amark_failed(second.id, "boom", attempt=attempt_of(second)))
    assert live_ids(m) == set() and history_ids(m) == {ok.id, bad.id}
    assert history_row(m, ok.id)[:2] == ("success", "7")
    assert history_row(m, bad.id)[0] == "failed"
    assert asyncio.run(m.abackend.aget_status(ok.id)) == "success"
    assert asyncio.run(m.abackend.aget_job(bad.id)).error == "boom"


def test_async_failure_with_a_retry_owed_stays_live_until_the_retry_is_queued(async_mode):
    from sqlery.core.attempts import attempt_of

    m = async_mode
    job = m.new_job(max_retries=1, retry_backoff=0.1)
    claimed = asyncio.run(m.abackend.aclaim_job(["default"], "async-test"))
    asyncio.run(m.abackend.amark_failed(job.id, "boom", attempt=attempt_of(claimed)))
    assert live_status(m, job.id) == "failed"  # not final: its retry is owed
    asyncio.run(m.abackend.arequeue_retry(m.job(job.id)))
    assert live_status(m, job.id) is None and history_row(m, job.id)[0] == "archived"
    assert m.backend.get_latest_retry(job.id).parent_job_id == job.id


def test_async_stale_completion_after_the_move_is_rejected(async_mode):
    from sqlery.compat import JobFencingError
    from sqlery.core.attempts import attempt_of

    m = async_mode
    job = m.new_job()
    claimed = asyncio.run(m.abackend.aclaim_job(["default"], "async-test"))
    asyncio.run(m.abackend.amark_success(job.id, "7", attempt=attempt_of(claimed)))
    with pytest.raises(JobFencingError):
        asyncio.run(m.abackend.amark_failed(job.id, "late", attempt=attempt_of(claimed)))
    assert history_row(m, job.id)[:2] == ("success", "7")
