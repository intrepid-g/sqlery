"""Quarantined review findings from PR 27 (c-review-with-demo).

Each test proves one already-filed issue. All tests are committed
``@pytest.mark.skip``'d so the suite stays green; unskip to re-verify.
"""

from __future__ import annotations

import os
import threading

import pytest


# ---------------------------------------------------------------------------
# 1. https://github.com/intrepid-g/sqlery/issues/29
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/29 — "
        "standalone expire_ttl_jobs() reports expired jobs but never commits "
        "the status change (QueuedJob.mark_failed mutates the detached instance "
        "only; SQLAlchemyBackend.get_expired_ttl_jobs returns rows from a closed "
        "session; claim_job has no TTL predicate)"
    )
)
def test_standalone_expire_ttl_jobs_persists(tmp_path, monkeypatch):
    """expire_ttl_jobs() must persist the failed status in standalone mode."""
    from datetime import datetime, timedelta, UTC

    db_path = tmp_path / "ttl_persist_test.sqlite3"
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")

    import sqlery.compat as compat_mod

    compat_mod._backend = None
    compat_mod._config = None

    from sqlery.compat import initialize, get_backend

    initialize(database_url=f"sqlite:///{db_path}", enable_daemon=False)
    backend = get_backend()

    job = backend.create_job(
        task_path="tests.crft_pr27.dummy",
        kwargs={},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
        ttl=1,
    )
    job_id = job.id

    from sqlery.core.models import QueuedJob
    from sqlmodel import select

    with backend._get_session() as session:
        db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
        db_job.created_at = datetime.now(UTC) - timedelta(seconds=60)
        session.add(db_job)
        session.commit()

    from sqlery.core.claiming import expire_ttl_jobs

    expired_count = expire_ttl_jobs(backend)
    assert expired_count == 1

    with backend._get_session() as session:
        reread = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
        assert reread.status == "failed", (
            f"expire_ttl_jobs reported 1 expired job but the persisted status is "
            f"{reread.status!r} — mark_failed() never committed"
        )

    assert backend.claim_job(["default"], "w1") is None, (
        "claim_job() returned the TTL-expired job for execution"
    )


# ---------------------------------------------------------------------------
# 2. https://github.com/intrepid-g/sqlery/issues/33
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/33 — "
        "TTL filter in DjangoBackend.get_claimable_jobs runs in Python AFTER "
        "the SQL LIMIT, so expired rows fill the LIMIT window and starve "
        "claimable jobs behind them"
    )
)
@pytest.mark.django_db
def test_expired_head_of_queue_does_not_starve_claimable_job():
    """Expired jobs at the head of the queue must not hide a claimable job."""
    from datetime import timedelta

    from django.utils import timezone

    from sqlery.models import QueuedJob
    from sqlery.django_sqlery.backend import DjangoBackend

    def _create(**kwargs):
        defaults = {
            "task_path": "tests.crft_pr27.dummy",
            "queue_name": "default",
            "priority": 0,
            "status": "queued",
        }
        defaults.update(kwargs)
        return QueuedJob.objects.create(**defaults)

    past = timezone.now() - timedelta(seconds=60)

    job_a = _create(ttl=1)
    QueuedJob.objects.filter(id=job_a.id).update(created_at=past)
    job_b = _create(ttl=1)
    QueuedJob.objects.filter(id=job_b.id).update(created_at=past)
    job_c = _create(ttl=None)

    # Confirm ordering actually puts the expired A/B ahead of claimable C —
    # priority is equal (0) for all three, so the tiebreak is created_at asc.
    ordered_ids = list(
        QueuedJob.objects.filter(status="queued").order_by("-priority", "created_at").values_list(
            "id", flat=True
        )
    )
    assert ordered_ids.index(job_a.id) < ordered_ids.index(job_c.id)
    assert ordered_ids.index(job_b.id) < ordered_ids.index(job_c.id)

    backend = DjangoBackend()
    claimable = backend.get_claimable_jobs(queues=["default"], limit=2)

    assert any(j.id == job_c.id for j in claimable), (
        f"claimable job {job_c.id} was starved behind expired head-of-queue "
        f"jobs; get_claimable_jobs(limit=2) returned {[j.id for j in claimable]!r}"
    )


# ---------------------------------------------------------------------------
# 3. https://github.com/intrepid-g/sqlery/issues/34
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/34 — "
        "SQLAlchemyBackend.get_claimable_jobs accepts priority_weights but "
        "ignores it, unlike DjangoBackend's CASE-based queue-priority ordering"
    )
)
def test_priority_weights_ordering_matches_across_backends(tmp_path, monkeypatch):
    """priority_weights must reorder queues on the SQLAlchemy backend too."""
    import time

    db_path = tmp_path / "priority_weights_test.sqlite3"
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")

    import sqlery.compat as compat_mod

    compat_mod._backend = None
    compat_mod._config = None

    from sqlery.compat import initialize, get_backend

    initialize(database_url=f"sqlite:///{db_path}", enable_daemon=False)
    backend = get_backend()

    def _create(queue_name):
        return backend.create_job(
            task_path="tests.crft_pr27.dummy",
            kwargs={},
            queue_name=queue_name,
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=False,
            timeout_seconds=None,
        )

    _create("low")
    time.sleep(1.1)  # created_at has second-level resolution in sqlite storage
    _create("high")

    claimable = backend.get_claimable_jobs(
        queues=["low", "high"],
        priority_weights={"high": 10, "low": 0},
        limit=2,
    )
    queue_order = [j.queue_name for j in claimable]

    assert queue_order == ["high", "low"], (
        f"expected priority_weights to put 'high' first, got {queue_order!r}"
    )


# ---------------------------------------------------------------------------
# 4. https://github.com/intrepid-g/sqlery/issues/32
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/32 — "
        "_queue_is_stuck treats ANY finished job (even ones the zombie reaper "
        "failed) as evidence of liveness, masking a no-active-workers banner"
    )
)
@pytest.mark.django_db
def test_zombie_reaped_job_does_not_mask_dead_workers():
    """A zombie-reaped job must not count as 'recently completed' liveness."""
    from datetime import timedelta

    from django.utils import timezone

    from sqlery.models import QueuedJob
    from sqlery.django_sqlery.views import _compute_health_warnings

    now = timezone.now()

    # One old queued job past the grace period, zero active Worker rows.
    stuck = QueuedJob.objects.create(task_path="tests.crft_pr27.dummy", status="queued")
    QueuedJob.objects.filter(pk=stuck.pk).update(
        created_at=now - timedelta(seconds=300)
    )

    # One job the zombie reaper failed a few seconds ago — not real progress.
    zombie = QueuedJob.objects.create(task_path="tests.crft_pr27.dummy", status="failed")
    QueuedJob.objects.filter(pk=zombie.pk).update(
        finished_at=now - timedelta(seconds=10),
        termination_reason="zombie_job",
    )

    warnings = [w for w in _compute_health_warnings(now) if "no active workers" in w["msg"]]

    assert warnings, (
        "expected a 'no active workers' warning; the zombie-reaped job's "
        "finished_at masked the dead-worker condition"
    )


# ---------------------------------------------------------------------------
# 5. https://github.com/intrepid-g/sqlery/issues/31 — requires Postgres
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/31 — "
        "core/claiming.py fetches candidates with limit=max_attempts (10) under "
        "FOR UPDATE SKIP LOCKED inside the claim transaction, so a peer worker "
        "sees zero candidates while that transaction is open"
    )
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_worker_sees_candidates_during_peer_claim():
    """A peer connection must still see >=1 candidate while a holder locks
    up to 10 rows via FOR UPDATE SKIP LOCKED with only 4 rows queued."""
    if not os.environ.get("SQLERY_TEST_PG_URL"):
        pytest.skip("SQLERY_TEST_PG_URL not set; PG test skipped")

    from django.db import connections, transaction

    from sqlery.models import QueuedJob
    from sqlery.django_sqlery.backend import DjangoBackend

    for _ in range(4):
        QueuedJob.objects.create(
            task_path="tests.crft_pr27.dummy",
            queue_name="default",
            priority=0,
            status="queued",
        )

    backend = DjangoBackend()
    peer_result: dict = {}
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def holder():
        try:
            with transaction.atomic():
                backend.get_claimable_jobs(queues=["default"], limit=10)
                holder_ready.set()
                # Hold the transaction (and its FOR UPDATE SKIP LOCKED row
                # locks on all 4 rows) open until the peer has queried.
                release_holder.wait(timeout=5)
        finally:
            connections.close_all()

    def peer():
        holder_ready.wait(timeout=5)
        try:
            with transaction.atomic():
                rows = backend.get_claimable_jobs(queues=["default"], limit=10)
                peer_result["rows"] = rows
        finally:
            connections.close_all()
            release_holder.set()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=peer)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert "rows" in peer_result, "peer thread did not complete in time"
    assert len(peer_result["rows"]) >= 1, (
        "peer saw zero candidates while the holder's FOR UPDATE SKIP LOCKED "
        "with limit=10 locked all 4 available rows"
    )


# ---------------------------------------------------------------------------
# 6. https://github.com/intrepid-g/sqlery/issues/30 — requires Postgres
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.skip(
    reason=(
        "quarantined: https://github.com/intrepid-g/sqlery/issues/30 — "
        "SQLAlchemyBackend.atomic_claim_job's skip_locked branch re-reads the "
        "row in a fresh session and guards only with a Python status=='queued' "
        "check, emitting an UPDATE with no status/version predicate — a true "
        "interleave (not a sequential repro) lets two threads both win"
    )
)
def test_sqlalchemy_atomic_claim_is_atomic_under_interleave(monkeypatch):
    """Two threads racing atomic_claim_job on the same job must not both win."""
    if not os.environ.get("SQLERY_TEST_PG_URL"):
        pytest.skip("SQLERY_TEST_PG_URL not set; PG test skipped")

    from tests.pg_url import sqlalchemy_pg_url

    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")

    import sqlery.compat as compat_mod

    compat_mod._backend = None
    compat_mod._config = None

    from sqlalchemy import text

    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.core import models as _core_models  # noqa: F401 — populate metadata

    pg_url = os.environ["SQLERY_TEST_PG_URL"]
    db_mod._engine = None
    db_mod.init_database(sqlalchemy_pg_url(pg_url))

    from sqlery.compat import get_backend

    backend = get_backend()

    job = backend.create_job(
        task_path="tests.crft_pr27.dummy",
        kwargs={},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
    )

    barrier = threading.Barrier(2)
    results: list[bool] = []
    results_lock = threading.Lock()

    from sqlmodel import select
    from sqlery.core.models import QueuedJob as SAQueuedJob

    def race():
        # Mirror atomic_claim_job's own read (get by id) so both threads see
        # status='queued' before the barrier forces them to write concurrently.
        with backend._get_session() as session:
            db_job = session.exec(
                select(SAQueuedJob).where(SAQueuedJob.id == job.id)
            ).first()
        barrier.wait(timeout=5)
        won = backend.atomic_claim_job(db_job, worker=None)
        with results_lock:
            results.append(won)

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    try:
        with backend._get_session() as session:
            session.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
            session.commit()
    except Exception:
        pass

    assert len(results) == 2, "both racing threads must complete"
    assert sum(1 for r in results if r) == 1, (
        f"expected exactly one interleaved atomic_claim_job() call to win, "
        f"got results={results!r}"
    )
