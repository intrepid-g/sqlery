"""Standalone worker heartbeat (#77): signature parity and 62-bit current_job_id."""

import os

import pytest

from tests.pg_url import sqlalchemy_pg_url


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "k")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    return get_backend()


def test_worker_heartbeat_signature_matches_what_the_worker_sends(backend):
    from sqlery.core.worker import WorkerProcess

    worker = WorkerProcess(queues=["default"], backend=backend)
    worker._heartbeat("idle")  # used to log "Heartbeat failed" instead of raising
    assert backend.get_worker_heartbeats(active_only=False), "no worker row was written"


def test_worker_current_job_id_column_is_bigint():
    from sqlalchemy import BigInteger

    from sqlery.core.models import Worker

    assert isinstance(Worker.__table__.c.current_job_id.type, BigInteger)


@pytest.mark.postgres
@pytest.mark.skipif(
    "SQLERY_TEST_PG_URL" not in os.environ, reason="needs PostgreSQL: SQLERY_TEST_PG_URL"
)
def test_busy_heartbeat_accepts_a_standalone_job_id(monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=sqlalchemy_pg_url(os.environ["SQLERY_TEST_PG_URL"]))
    backend = get_backend()
    job = backend.create_job(
        task_path="x.y",
        kwargs={},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
    )
    backend.update_worker_heartbeat(worker_id="w1", status="idle")
    backend.update_worker_heartbeat(
        worker_id="w1", status="busy", current_job_id=job.id, total_busy_seconds=1.5
    )
