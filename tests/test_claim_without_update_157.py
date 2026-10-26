"""Claim without an UPDATE of the job row (#157): what the queue decides when a job runs.

Pure decision tests: each one sets up one scenario and asserts the decision, never an internal
call. A job is running iff it has an open attempt; a claim deletes the ready entry and inserts
the attempt and writes nothing to the job row. Every scenario runs against the standalone
backend (SQLite, and PostgreSQL with ``SQLERY_TEST_PG_URL``) and the Django backend (SQLite, or
PostgreSQL when the URL is set).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_ready_queue_139 import (  # noqa: F401  (fixtures are used by name)
    DjangoMode,
    StandaloneMode,
    mode,
    needs_pg,
)

WORKER = "worker_node157_4242"


# ---- helpers: raw SQL against whichever database the mode runs on ---------------------------
def fetch(m, sql, job_id):
    """Rows of ``sql`` (with ``{id}`` replaced by the job id), as tuples."""
    sql = sql.format(id=int(job_id))
    if isinstance(m, StandaloneMode):
        from sqlalchemy import text

        with m.backend._get_session() as session:
            return [tuple(r) for r in session.execute(text(sql)).all()]
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]


def execute(m, sql):
    if isinstance(m, StandaloneMode):
        from sqlalchemy import text

        with m.backend._get_session() as session:
            session.execute(text(sql))
            session.commit()
        return
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(sql)


def stored_row(m, job_id):
    """The live row exactly as stored (every column; on PostgreSQL also its tuple identity)."""
    extra = ", xmin::text, ctid::text" if m.pg else ""
    (row,) = fetch(m, f"SELECT *{extra} FROM sqlery_queued_job WHERE id = {{id}}", job_id)
    return row


def seen_by_readers(m, job_id):
    """``(status, started_at, worker_id)`` as the union view (what every reader of "any job"
    uses) shows the job."""
    (row,) = fetch(
        m, "SELECT status, started_at, worker_id FROM sqlery_job_all WHERE id = {id}", job_id
    )
    return row


def age_jobs(m, job_ids, delta):
    """Make jobs (and their ready entries) look ``delta`` older (created_at is the partition
    key, so it is rewritten in place)."""
    old = datetime.now(UTC) - delta
    if isinstance(m, StandaloneMode):
        from sqlalchemy import update

        from sqlery.core.models import JobReady, QueuedJob

        with m.backend._get_session() as session:
            session.execute(update(QueuedJob).where(QueuedJob.id.in_(job_ids)).values(created_at=old))
            session.execute(update(JobReady).where(JobReady.job_id.in_(job_ids)).values(created_at=old))
            session.commit()
        return
    from sqlery.django_sqlery.models import JobReady, QueuedJob

    QueuedJob.objects.filter(id__in=job_ids).update(created_at=old)
    JobReady.objects.filter(job_id__in=job_ids).update(created_at=old)


def register_worker(m):
    m.backend.update_worker_heartbeat(WORKER, "idle")
    return WORKER


def claim_as_worker(m, queue="default"):
    return m.backend.claim_job([queue], register_worker(m))


def in_history(m, job_id):
    return fetch(m, "SELECT status, started_at, worker_id FROM sqlery_job_history WHERE id = {id}", job_id)


# ---- the claim does not write the job row ----------------------------------------------------
def test_claim_leaves_the_job_row_untouched_and_readers_see_it_running(mode):
    job = mode.new_job()
    before = stored_row(mode, job.id)

    claimed = claim_as_worker(mode)
    assert claimed is not None and claimed.id == job.id

    # No UPDATE happened: every column, the version and (PostgreSQL) the tuple are as before.
    assert stored_row(mode, job.id) == before
    status, started_at, worker_id = seen_by_readers(mode, job.id)
    assert status == "running"
    assert started_at is not None
    assert worker_id is not None
    # ... and so do the public readers and the job object the worker holds.
    assert mode.status(job.id) == "running"
    assert claimed.status == "running" and claimed.started_at is not None
    assert mode.backend.get_queue_stats()["running"] == 1
    assert mode.backend.get_queue_stats()["queued"] == 0
    assert [j.id for j in mode.backend.get_jobs(status="running")] == [job.id]
    assert [j.id for j in mode.backend.get_running_jobs()] == [job.id]


def test_claim_by_id_and_candidate_claim_also_leave_the_row_untouched(mode):
    first = mode.new_job()
    second = mode.new_job()
    before = {j.id: stored_row(mode, j.id) for j in (first, second)}
    assert mode.backend.claim_job_by_id(first.id, "trigger-test").id == first.id
    assert mode.claim().id == second.id
    assert {j.id: stored_row(mode, j.id) for j in (first, second)} == before


def test_completion_records_when_it_started_and_who_ran_it_in_history(mode):
    job = mode.new_job()
    claimed = claim_as_worker(mode)
    (_, started_at, worker_id) = seen_by_readers(mode, job.id)
    mode.finish(claimed, ok=True)
    ((status, hist_started, hist_worker),) = in_history(mode, job.id)
    assert status == "success"
    assert hist_started is not None and hist_started == started_at
    assert hist_worker == worker_id


# ---- fencing ---------------------------------------------------------------------------------
def test_two_claimers_of_the_same_candidate_exactly_one_wins(mode):
    job = mode.new_job()
    if isinstance(mode, DjangoMode):
        from django.db import transaction

        with transaction.atomic():
            stale = mode.backend.get_claimable_jobs(["default"], limit=1)[0]
    else:
        stale = mode.backend.get_claimable_jobs(["default"], limit=1)[0]
    winner = mode.claim()
    assert winner.id == job.id
    assert mode.backend.atomic_claim_job(stale, None) is False
    assert len(mode.attempts(job.id)) == 1


def test_a_duplicate_ready_entry_cannot_make_a_running_job_run_twice(mode):
    job = mode.new_job()
    assert mode.claim().id == job.id
    # Whatever put an entry back (a lost update, a rotation drain, a raw insert)...
    execute(
        mode,
        "INSERT INTO sqlery_job_ready (slot, job_id, queue_name, priority, created_at) "
        f"SELECT 0, id, queue_name, priority, created_at FROM sqlery_queued_job WHERE id = {job.id}",
    )
    assert mode.ready() == {job.id}
    assert mode.claim() is None  # ... the claim sees the open attempt, drops it, runs nothing
    assert mode.ready() == set()
    assert len(mode.attempts(job.id)) == 1


@needs_pg
def test_concurrent_claimers_on_postgresql_exactly_one_wins(mode):
    if not mode.pg:
        pytest.skip("real concurrency needs PostgreSQL")
    job = mode.new_job()
    barrier = threading.Barrier(4)
    results = [None] * 4

    def run(i):
        try:
            barrier.wait(timeout=10)
            results[i] = mode.claim()
        except Exception as exc:  # noqa: BLE001  # a crash is a failed decision, not a hang
            results[i] = exc
        finally:
            if isinstance(mode, DjangoMode):
                from django.db import connection

                connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    winners = [r for r in results if r is not None and not isinstance(r, Exception)]
    assert [w.id for w in winners] == [job.id]
    assert not [r for r in results if isinstance(r, Exception)]
    assert len(mode.attempts(job.id)) == 1


def test_a_stale_completion_after_a_requeue_is_rejected_and_the_new_run_wins(mode):
    from sqlery.compat import JobFencingError
    from sqlery.core.attempts import attempt_of

    job = mode.new_job()
    old = attempt_of(mode.claim())
    mode.backend.release_job(job.id)
    new = attempt_of(mode.claim())
    assert new.id != old.id
    with pytest.raises(JobFencingError):
        mode.backend.mark_job_success(job.id, output="late", attempt=old)
    assert mode.status(job.id) == "running"
    assert mode.backend.mark_job_success(job.id, output="3", attempt=new).status == "success"


def test_a_completion_after_the_job_row_is_gone_is_rejected(mode):
    from sqlery.compat import JobFencingError
    from sqlery.core.attempts import attempt_of

    job = mode.new_job()
    handle = attempt_of(mode.claim())
    assert mode.backend.fail_zombie_job(job.id, "recovered")
    with pytest.raises(JobFencingError):
        mode.backend.mark_job_success(job.id, output="late", attempt=handle)


# ---- readers derive running from the attempt -------------------------------------------------
def test_tag_concurrency_counts_running_jobs(mode):
    mode.limit_tag("api", 1)
    first = mode.new_job(tags=["api"])
    second = mode.new_job(tags=["api"])
    assert mode.backend.count_running_with_tag("api") == 0
    assert mode.claim().id == first.id
    assert mode.backend.count_running_with_tag("api") == 1
    assert mode.claim() is None  # the limit holds: the second waits in the ready queue
    assert mode.ready() == {second.id}
    mode.finish(mode.backend.get_job_by_id(first.id), ok=True)
    assert mode.backend.count_running_with_tag("api") == 0
    assert mode.claim().id == second.id


def test_allow_parallel_false_queue_serializes(mode):
    from sqlery.core.worker import JobExecutor

    executor = JobExecutor(backend=mode.backend)
    first = mode.new_job()
    second = mode.new_job()
    a = mode.claim()
    assert a.id == first.id and executor.can_execute_job(a)
    b = mode.claim()  # the claim itself does not serialize ...
    assert b.id == second.id
    assert not executor.can_execute_job(b)  # ... the worker's check does, from the attempts
    mode.backend.release_job(b.id)  # so it puts the job back
    assert mode.ready() == {second.id}
    assert mode.backend.has_running_jobs_in_queue("default", exclude_job_id=second.id)
    mode.finish(a, ok=True)
    again = mode.claim()
    assert again.id == second.id and executor.can_execute_job(again)


def test_a_parallel_queue_does_not_serialize(mode):
    from sqlery.core.worker import JobExecutor

    executor = JobExecutor(backend=mode.backend)
    mode.new_job(allow_parallel=True)
    mode.new_job(allow_parallel=True)
    a, b = mode.claim(), mode.claim()
    assert executor.can_execute_job(a) and executor.can_execute_job(b)


def test_dependents_wait_while_their_dependency_runs(mode):
    parent = mode.new_job()
    child = mode.new_job(dependencies=[parent.id])
    assert mode.claim().id == parent.id  # the child is judged and blocked
    assert mode.claim() is None
    assert mode.blocked() == {child.id}
    mode.finish(mode.backend.get_job_by_id(parent.id), ok=True)
    assert mode.claim().id == child.id


# ---- the ready-queue sweep ---------------------------------------------------------------------
def test_the_sweep_does_not_resurrect_a_running_job(mode):
    job = mode.new_job()
    assert mode.claim().id == job.id
    result = mode.backend.maintain_ready_queue()
    assert result["restored"] == 0
    assert mode.ready() == set()
    assert mode.claim() is None
    assert mode.status(job.id) == "running"


def test_the_sweep_still_restores_a_waiting_job_that_lost_its_entry(mode):
    job = mode.new_job()
    mode.drop_ready_entry(job.id)
    assert mode.backend.maintain_ready_queue()["restored"] == 1
    assert mode.ready() == {job.id}


# ---- recovery ----------------------------------------------------------------------------------
def test_zombie_recovery_requeues_exactly_once(mode):
    job = mode.new_job()
    assert mode.claim().id == job.id
    mode.backend.release_job(job.id)
    mode.backend.release_job(job.id)  # a repeated recovery has nothing left to release
    assert mode.ready() == {job.id}
    attempts = mode.attempts(job.id)
    assert len(attempts) == 1 and attempts[0].outcome == "abandoned"
    assert mode.status(job.id) == "queued"
    assert mode.claim().id == job.id  # it runs, once
    assert mode.claim() is None


def test_zombie_sweep_fails_a_dead_workers_job_once(mode):
    job = mode.new_job()
    assert mode.claim().id == job.id
    assert mode.backend.fail_zombie_job(job.id, "worker died") is True
    assert mode.backend.fail_zombie_job(job.id, "worker died") is False
    ((status, _s, _w),) = in_history(mode, job.id)
    assert status == "failed"
    assert mode.backend.get_running_jobs() == []
    assert mode.ready() == set()


def test_a_requeue_never_touches_a_job_that_is_not_running(mode):
    job = mode.new_job()
    mode.backend.release_job(job.id)  # waiting, never claimed: nothing to release
    assert mode.ready() == {job.id}
    assert mode.attempts(job.id) == []


# ---- TTL ---------------------------------------------------------------------------------------
def test_ttl_expiry_never_expires_a_running_job(mode):
    from sqlery.core.claiming import expire_ttl_jobs

    running = mode.new_job(ttl=1)
    waiting = mode.new_job(ttl=1)
    assert mode.claim().id == running.id
    age_jobs(mode, [running.id, waiting.id], timedelta(hours=1))
    assert expire_ttl_jobs(mode.backend) == 1  # only the waiting job
    assert mode.status(running.id) == "running"
    assert in_history(mode, running.id) == []
    ((status, _s, _w),) = in_history(mode, waiting.id)
    assert status == "failed"


def test_expiring_a_candidate_that_was_claimed_since_is_a_lost_race(mode):
    running = mode.new_job(ttl=1)
    stale = mode.backend.get_job_by_id(running.id)  # read while waiting
    assert mode.claim().id == running.id
    expire = getattr(mode.backend, "expire_queued_job", None)
    assert expire is not None
    assert expire(stale, "Job expired", "expired") is False
    assert mode.status(running.id) == "running"


# ---- cancel / revoke ---------------------------------------------------------------------------
def test_cancelling_a_running_job_is_a_revoke_not_a_cancel(mode):
    from sqlery.core.result import AsyncResult, revoke

    job = mode.new_job()
    assert mode.claim().id == job.id
    assert mode.backend.cancel_job(job.id) is False  # running jobs are not cancelled ...
    assert mode.status(job.id) == "running"
    assert mode.backend.is_job_revoke_requested(job.id) is False
    assert revoke(job.id, terminate=True, backend=mode.backend) is True  # ... they are revoked
    assert mode.backend.is_job_revoke_requested(job.id) is True
    assert AsyncResult(job.id, backend=mode.backend).state == "STARTED"
    assert mode.status(job.id) == "running"


def test_cancelling_a_waiting_job_still_works_and_a_claim_cannot_follow(mode):
    job = mode.new_job()
    assert mode.backend.cancel_job(job.id) is True
    assert mode.claim() is None
    assert mode.backend.request_job_revoke(job.id) is False


# ---- public status semantics are unchanged ----------------------------------------------------
def test_public_status_goes_queued_running_success(mode):
    from sqlery.core.result import AsyncResult

    job = mode.new_job()
    result = AsyncResult(job.id, backend=mode.backend)
    assert result.state == "PENDING" and mode.status(job.id) == "queued"
    claimed = mode.claim()
    assert result.state == "STARTED" and mode.status(job.id) == "running"
    mode.finish(claimed, ok=True)
    assert result.state == "SUCCESS" and mode.status(job.id) == "success"


# ---- migration of in-flight data ---------------------------------------------------------------
def test_rows_stored_as_running_become_queued_rows_with_an_open_attempt(mode):
    """The data migration: the older claim stored status='running'; after it the job reads the
    same (running, same start) but is a queued row with one open attempt."""
    from sqlery.core import history

    job = mode.new_job()
    execute(mode, f"DELETE FROM sqlery_job_ready WHERE job_id = {job.id}")
    execute(
        mode,
        "UPDATE sqlery_queued_job SET status = 'running', started_at = created_at, "
        f"worker_pid = 4242 WHERE id = {job.id}",
    )
    dialect = "postgresql" if mode.pg else "sqlite"
    for statement in history.normalize_running_sql(dialect):
        execute(mode, statement)
    ((stored,),) = fetch(mode, "SELECT status FROM sqlery_queued_job WHERE id = {id}", job.id)
    assert stored == "queued"
    assert seen_by_readers(mode, job.id)[0] == "running"
    (attempt,) = mode.attempts(job.id)
    assert attempt.finished_at is None and attempt.worker_pid == 4242
    for statement in history.normalize_running_sql(dialect):  # idempotent
        execute(mode, statement)
    assert len(mode.attempts(job.id)) == 1
    # and the reverse writes the old representation back
    for statement in history.denormalize_running_sql():
        execute(mode, statement)
    ((stored,),) = fetch(mode, "SELECT status FROM sqlery_queued_job WHERE id = {id}", job.id)
    assert stored == "running"
