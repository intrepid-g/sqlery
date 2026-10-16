"""AsyncResult / apply_async / serialize_result (standalone backend, SQLite)."""

from datetime import datetime, timedelta, timezone

import pytest

from sqlery.core.result import (
    AsyncResult,
    TaskFailedError,
    apply_async_job,
    deserialize_result,
    serialize_result,
)
from sqlery.core.worker import JobExecutor
from sqlery.fastapi_sqlery import database
from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend


@pytest.fixture
def backend(tmp_path):
    database.init_database(f"sqlite:///{tmp_path / 'r.db'}")
    return SQLAlchemyBackend()


def _run(backend, job_id):
    job = backend.claim_job_by_id(job_id, "w")
    JobExecutor(backend=backend).execute_job(job)


def test_serialize_roundtrip_and_falsy_results():
    assert serialize_result(None) == ""
    assert serialize_result("hi") == "hi"
    assert serialize_result(0) == "0"
    assert serialize_result(False) == "false"
    assert deserialize_result(serialize_result({"a": [1, 2]})) == {"a": [1, 2]}
    assert deserialize_result(serialize_result(0)) == 0
    assert deserialize_result("plain text") == "plain text"
    assert deserialize_result("") is None


def test_apply_async_success_and_positional_args(backend):
    res = apply_async_job("operator.add", {"_args": [2, 3]}, backend=backend)
    assert isinstance(res, AsyncResult)
    assert res.state == "PENDING" and not res.ready()
    _run(backend, res.id)
    assert res.state == "SUCCESS" and res.successful()
    assert res.get(timeout=1) == 5


def test_falsy_result_is_preserved(backend):
    res = apply_async_job("operator.sub", {"_args": [3, 3]}, backend=backend)
    _run(backend, res.id)
    assert res.get(timeout=1) == 0


def test_failure_propagates_or_returns_error(backend):
    res = apply_async_job("operator.truediv", {"_args": [1, 0]}, backend=backend)
    _run(backend, res.id)
    assert res.state == "FAILURE" and res.failed()
    with pytest.raises(TaskFailedError) as ei:
        res.get(timeout=1)
    assert "division" in str(ei.value) and ei.value.traceback
    assert "division" in res.get(timeout=1, propagate=False)


def test_get_times_out_while_pending(backend):
    res = apply_async_job("operator.add", {"_args": [1, 1]}, backend=backend)
    with pytest.raises(TimeoutError):
        res.get(timeout=0.3, interval=0.05)


def test_countdown_eta_and_expires(backend):
    before = datetime.now(timezone.utc)
    res = apply_async_job("operator.add", {"_args": [1, 1]}, countdown=60, expires=120,
                          backend=backend)
    job = backend.get_job_by_id(res.id)
    sched = job.scheduled_at if job.scheduled_at.tzinfo else job.scheduled_at.replace(tzinfo=timezone.utc)
    assert sched >= before + timedelta(seconds=59)
    assert job.ttl == 120
    # Not claimable until the countdown elapses.
    assert backend.claim_job(["default"], "w") is None
    with pytest.raises(ValueError):
        apply_async_job("operator.add", {}, countdown=1, eta=before, backend=backend)


def test_unknown_id_is_pending_not_error(backend):
    res = AsyncResult(987654, backend=backend)
    assert res.state == "PENDING" and not res.exists()


def test_follows_retry_chain(backend):
    res = apply_async_job("operator.truediv", {"_args": [1, 0]}, max_retries=1, backend=backend)
    _run(backend, res.id)
    # Original was archived and a retry (scheduled in the future) exists: still not ready.
    assert backend.get_job_by_id(res.id).status == "archived"
    assert backend.get_latest_retry(res.id) is not None
    assert res.state == "PENDING" and not res.ready()
