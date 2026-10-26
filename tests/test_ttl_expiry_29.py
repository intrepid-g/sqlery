"""TTL expiry (#29): a queued job past its ttl ends up expired, in history and not claimable.

Pure decision tests: each scenario sets up one situation and asserts what ``expire_ttl_jobs``
decides (how many jobs expired, where each job ends up, whether it can still be claimed),
never an internal call. They run against the standalone backend (SQLite and PostgreSQL) and
the Django backend.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from __future__ import annotations

import time

from tests.test_ready_queue_139 import (  # noqa: F401  (fixtures are used by name)
    DjangoMode,
    mode,
)


def _where(m, job_id):
    """Which table holds the job: ``live``, ``history`` or ``None``."""
    found = []
    for label, table in (("live", "sqlery_queued_job"), ("history", "sqlery_job_history")):
        sql = f"SELECT count(*) FROM {table} WHERE id = {int(job_id)}"
        if isinstance(m, DjangoMode):
            from django.db import connection

            with connection.cursor() as cur:
                cur.execute(sql)
                n = cur.fetchone()[0]
        else:
            from sqlalchemy import text

            with m.backend._get_session() as session:
                n = session.execute(text(sql)).scalar()
        if n:
            found.append(label)
    assert len(found) <= 1, f"job {job_id} is in both {found}"
    return found[0] if found else None


def _expire(m):
    from sqlery.core.claiming import expire_ttl_jobs

    return expire_ttl_jobs(m.backend)


def _past_ttl(m, **kw):
    job = m.new_job(ttl=1, **kw)
    time.sleep(1.2)
    return job


def test_queued_job_past_its_ttl_is_expired_and_in_history(mode):
    job = _past_ttl(mode)
    assert _expire(mode) == 1
    final = mode.job(job.id)
    assert final.status == "failed"
    assert final.termination_reason == "expired"
    assert "expired" in (final.error or "").lower()
    assert _where(mode, job.id) == "history"


def test_expired_job_is_not_claimable_and_its_ready_entry_is_gone(mode):
    job = _past_ttl(mode)
    _expire(mode)
    assert mode.ready() == set()
    assert mode.claim() is None
    assert mode.attempts(job.id) == []


def test_expiry_is_persisted_for_a_second_reader(mode):
    job = _past_ttl(mode)
    assert _expire(mode) == 1
    assert _expire(mode) == 0  # nothing left to expire: the first pass wrote it
    assert mode.job(job.id).status == "failed"


def test_job_not_yet_past_its_ttl_is_untouched(mode):
    job = mode.new_job(ttl=600)
    assert _expire(mode) == 0
    assert mode.status(job.id) == "queued"
    assert _where(mode, job.id) == "live"
    assert mode.ready() == {job.id}
    assert mode.claim().id == job.id


def test_job_without_a_ttl_never_expires(mode):
    job = mode.new_job()
    time.sleep(1.2)
    assert _expire(mode) == 0
    assert mode.status(job.id) == "queued"


def test_only_the_expired_job_is_expired(mode):
    expired = _past_ttl(mode)
    fresh = mode.new_job(ttl=600)
    assert _expire(mode) == 1
    assert mode.status(expired.id) == "failed"
    assert mode.status(fresh.id) == "queued"
    assert mode.ready() == {fresh.id}


def test_job_claimed_before_the_expiry_pass_is_not_expired(mode):
    job = _past_ttl(mode)
    stale = mode.backend.get_expired_ttl_jobs()
    assert [j.id for j in stale] == [job.id]
    # Another worker claims it after the expiry candidates were read (ttl is checked when the
    # claim reads the job, so claim it by id, which does not check ttl).
    claimed = mode.backend.claim_job_by_id(job.id, "trigger-test")
    assert claimed is not None

    from sqlery.core import claiming

    real = mode.backend.get_expired_ttl_jobs
    mode.backend.get_expired_ttl_jobs = lambda: stale
    try:
        assert claiming.expire_ttl_jobs(mode.backend) == 0
    finally:
        mode.backend.get_expired_ttl_jobs = real
    assert mode.status(job.id) == "running"
    assert _where(mode, job.id) == "live"
