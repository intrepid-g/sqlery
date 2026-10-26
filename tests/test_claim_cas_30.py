"""Claim compare-and-set (#30): no claim path can hand one job to two claimers.

Pure decision tests: each scenario sets up one situation and asserts what the claim decides
(who gets the job, how many attempts exist, whether the ready entry is gone), never an
internal call. The claim of a candidate is decided by deleting its ready entry (rowcount 1 is
the mutex) and by a status/version-fenced update of the job row; a loser gets nothing and
opens no attempt. Scenarios run on the standalone backend (SQLite and PostgreSQL) and the
Django backend; the threaded races need real concurrent connections and run on PostgreSQL.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from __future__ import annotations

import threading

import pytest

from tests.test_ready_queue_139 import (  # noqa: F401  (fixtures are used by name)
    DjangoMode,
    StandaloneMode,
    async_mode,
    mode,
    needs_pg,
)


def _candidates(m, limit=1):
    """The next claimable jobs as a claimer sees them before it claims one."""
    if isinstance(m, DjangoMode):
        from django.db import transaction

        with transaction.atomic():  # PostgreSQL locks the entries inside this block
            return m.backend.get_claimable_jobs(["default"], limit=limit)
    return m.backend.get_claimable_jobs(["default"], limit=limit)


def _real_concurrency(m):
    return m.pg


def _race(m, calls):
    """Run each callable in its own thread, released together; return their results."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, call):
        try:
            barrier.wait(timeout=10)
            results[i] = call()
        except Exception as exc:  # noqa: BLE001  # a crash is a failed decision, not a hang
            results[i] = exc
        finally:
            if isinstance(m, DjangoMode):
                from django.db import connection

                connection.close()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


# ---- a candidate that was claimed in between -------------------------------------------------
def test_claimer_whose_candidate_was_claimed_in_between_gets_nothing_and_opens_no_attempt(mode):
    job = mode.new_job()
    (stale,) = _candidates(mode)
    winner = mode.claim()
    assert winner is not None and winner.id == job.id
    assert mode.backend.atomic_claim_job(stale, None) is False
    assert len(mode.attempts(job.id)) == 1
    assert mode.status(job.id) == "running"
    assert mode.ready() == set()


def test_claimer_whose_candidate_was_cancelled_by_raw_sql_gets_nothing(mode):
    job = mode.new_job()
    (stale,) = _candidates(mode)
    mode.raw_status(job.id, "failed")  # leaves the ready entry and the version behind
    assert mode.backend.atomic_claim_job(stale, None) is False
    assert mode.attempts(job.id) == []
    assert mode.status(job.id) == "failed"
    assert mode.ready() == set()  # the stale entry is not put back for a job that is not queued


def test_candidate_claimed_by_id_in_between_gets_nothing_and_opens_no_attempt(mode):
    job = mode.new_job()
    (stale,) = _candidates(mode)
    assert mode.backend.claim_job_by_id(job.id, "trigger-test").id == job.id
    assert mode.backend.atomic_claim_job(stale, None) is False
    assert len(mode.attempts(job.id)) == 1


def test_claim_by_id_after_a_claim_gets_nothing_and_opens_no_attempt(mode):
    job = mode.new_job()
    assert mode.claim().id == job.id
    assert mode.backend.claim_job_by_id(job.id, "trigger-test") is None
    assert len(mode.attempts(job.id)) == 1


def test_async_claimer_whose_candidate_was_claimed_in_between_gets_nothing(async_mode):
    import asyncio

    m = async_mode
    job = m.new_job()
    (stale,) = _candidates(m)
    won = asyncio.run(m.abackend.aclaim_job(["default"], "async-test"))
    assert won.id == job.id
    assert m.backend.atomic_claim_job(stale, None) is False
    assert asyncio.run(m.abackend.aclaim_job(["default"], "async-test")) is None
    assert len(m.attempts(job.id)) == 1


# ---- the legacy Django helpers -----------------------------------------------------------------
def _legacy(mode):
    if not isinstance(mode, DjangoMode):
        pytest.skip("db_compat helpers belong to the Django integration")
    from sqlery.django_sqlery import db_compat

    return db_compat


@pytest.mark.parametrize("variant", ["atomic_claim_job_postgres", "atomic_claim_job_sqlite"])
def test_legacy_claim_of_a_job_that_is_no_longer_queued_gets_nothing(mode, variant):
    db_compat = _legacy(mode)
    job = mode.new_job()
    (stale,) = _candidates(mode)
    mode.raw_status(job.id, "failed")  # the version did not move: only the status fences it
    assert getattr(db_compat, variant)(stale, None) is False
    assert mode.status(job.id) == "failed"


def test_legacy_claim_takes_the_ready_entry(mode):
    db_compat = _legacy(mode)
    job = mode.new_job()
    (candidate,) = _candidates(mode)
    assert db_compat.atomic_claim_job(candidate, None) is True
    assert mode.ready() == set()
    assert mode.status(job.id) == "running"
    assert db_compat.atomic_claim_job(candidate, None) is False


def test_legacy_claim_of_a_candidate_claimed_in_between_gets_nothing(mode):
    db_compat = _legacy(mode)
    mode.new_job()
    (stale,) = _candidates(mode)
    assert mode.claim() is not None
    assert db_compat.atomic_claim_job(stale, None) is False


# ---- real concurrency (PostgreSQL) ---------------------------------------------------------------
@pytest.fixture
def concurrent(mode):
    if not _real_concurrency(mode):
        pytest.skip("a threaded claim race needs a database with concurrent writers")
    return mode


def test_two_concurrent_claimers_of_the_same_job_exactly_one_wins(concurrent):
    m = concurrent
    for _ in range(6):
        job = m.new_job()
        (candidate,) = _candidates(m)
        assert candidate.id == job.id
        results = _race(m, [lambda c=candidate: m.backend.atomic_claim_job(c, None)] * 2)
        assert sorted(results, key=str) == [False, True], results
        assert len(m.attempts(job.id)) == 1
        assert m.status(job.id) == "running"
        assert m.ready() == set()


def test_atomic_claim_and_claim_by_id_of_the_same_job_exactly_one_wins(concurrent):
    m = concurrent
    for _ in range(6):
        job = m.new_job()
        (candidate,) = _candidates(m)
        results = _race(
            m,
            [
                lambda c=candidate: m.backend.atomic_claim_job(c, None),
                lambda j=job.id: m.backend.claim_job_by_id(j, "trigger-test"),
            ],
        )
        winners = [r for r in results if r not in (None, False)]
        assert len(winners) == 1, results
        assert not any(isinstance(r, Exception) for r in results), results
        assert len(m.attempts(job.id)) == 1
        assert m.ready() == set()


def test_many_concurrent_claim_job_callers_never_share_a_job(concurrent):
    m = concurrent
    ids = {m.new_job().id for _ in range(4)}
    results = _race(m, [lambda: m.claim() for _ in range(8)])
    got = [r.id for r in results if r is not None and not isinstance(r, Exception)]
    assert not any(isinstance(r, Exception) for r in results), results
    # (a Django claimer locks the whole candidate page, so others may find nothing: only
    # "never the same job twice" is decided here)
    assert got and len(got) == len(set(got)) and set(got) <= ids
    for job_id in got:
        assert len(m.attempts(job_id)) == 1
