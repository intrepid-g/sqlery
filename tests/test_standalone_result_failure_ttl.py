"""Standalone mode enforces per-job result_ttl / failure_ttl (#75)."""

from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "k")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    return get_backend()


def _make(backend, **ttl):
    return backend.create_job(
        task_path="x.y",
        kwargs={},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
        **ttl,
    )


def _age(backend, job_id, seconds):
    from sqlalchemy import update

    from sqlery.core.models import JobHistory

    # A finished job is in history (insert-only); retention reads when it finished, so the
    # test moves that timestamp by hand.
    with backend._get_session() as session:
        session.exec(
            update(JobHistory)
            .where(JobHistory.id == job_id)
            .values(finished_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )
        session.commit()


def test_result_ttl_removes_the_finished_job(backend):
    from sqlery.core.cleanup import CleanupManager

    job = _make(backend, result_ttl=1, failure_ttl=1)
    backend.mark_job_success(job.id, output="ok")
    _age(backend, job.id, 5)
    CleanupManager().auto_cleanup()
    assert backend.get_job_by_id(job.id) is None


def test_failure_ttl_removes_the_failed_job(backend):
    from sqlery.core.cleanup import CleanupManager

    job = _make(backend, result_ttl=1, failure_ttl=1)
    backend.mark_job_failed(job.id, error="boom")
    _age(backend, job.id, 5)
    CleanupManager().auto_cleanup()
    assert backend.get_job_by_id(job.id) is None


def test_unexpired_forever_and_unset_ttl_are_kept(backend):
    from sqlery.core.cleanup import CleanupManager

    fresh = _make(backend, result_ttl=3600)
    forever = _make(backend, result_ttl=-1)
    unset = _make(backend)
    for j in (fresh, forever, unset):
        backend.mark_job_success(j.id, output="ok")
    _age(backend, forever.id, 99999)
    _age(backend, unset.id, 99999)
    CleanupManager().auto_cleanup()
    for j in (fresh, forever, unset):
        assert backend.get_job_by_id(j.id) is not None


def test_dry_run_deletes_nothing(backend):
    job = _make(backend, result_ttl=1)
    backend.mark_job_success(job.id, output="ok")
    _age(backend, job.id, 5)
    assert backend.cleanup_per_job_ttl(dry_run=True) == {"would_delete": 1}
    assert backend.get_job_by_id(job.id) is not None
