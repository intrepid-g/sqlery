"""Regression tests for signature binding, retry delay, and webhook redirect hardening."""

import hashlib

from sqlery.core.signature import generate_signature, make_signed_request_headers, verify_signature
from sqlery.core.worker import compute_retry_delay


def test_legacy_signature_still_verifies():
    sig, ts = generate_signature("s3cret")
    assert verify_signature(sig, ts, "s3cret")


def test_body_bound_signature_rejects_swapped_body():
    body = b'{"job_id": 1}'
    headers = make_signed_request_headers("s3cret", body=body)
    assert headers["X-Signature-Version"] == "2"
    sig, ts = headers["X-Signature"], headers["X-Timestamp"]
    assert verify_signature(sig, ts, "s3cret", body=body)
    assert not verify_signature(sig, ts, "s3cret", body=b'{"job_id": 2}')
    # A v2 signature must not validate as a legacy timestamp-only one.
    assert not verify_signature(sig, ts, "s3cret")
    assert hashlib.sha256(body).hexdigest()  # sanity


def test_retry_delay_defaults_unchanged_and_intervals_win():
    assert compute_retry_delay(2.0, 3) == 16.0
    assert compute_retry_delay(2.0, 5, [5, 10, 60]) == 60.0
    assert compute_retry_delay(2.0, 0, [5, 10, 60]) == 5.0


def _backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'h.db'}")
    return SQLAlchemyBackend()


def _mk(backend, **kw):
    base = dict(
        task_path="os.getcwd", kwargs={}, queue_name="default", priority=0,
        scheduled_at=None, max_retries=0, retry_backoff=1.0, allow_parallel=False,
        timeout_seconds=None,
    )
    base.update(kw)
    return backend.create_job(**base)


def test_claim_job_by_id_is_exclusive(tmp_path):
    b = _backend(tmp_path)
    job = _mk(b)
    first = b.claim_job_by_id(job.id, "w1")
    assert first is not None and first.status == "running"
    assert b.claim_job_by_id(job.id, "w2") is None
    assert b.claim_job_by_id(999999, "w2") is None


def test_standalone_claim_respects_dependencies(tmp_path):
    b = _backend(tmp_path)
    parent = _mk(b, priority=0)
    child = _mk(b, priority=10, dependencies=[parent.id])
    # Child has higher priority but its parent has not succeeded: parent is claimed first.
    claimed = b.claim_job(["default"], "w1")
    assert claimed.id == parent.id
    assert b.claim_job(["default"], "w1") is None  # child still blocked while parent runs
    assert b.get_job_by_id(child.id).status == "queued"
