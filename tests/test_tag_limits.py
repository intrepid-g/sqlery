"""Tag concurrency / rate limits are applied end-to-end in standalone mode."""

import pytest

from sqlery.core.claiming import _config_limits
from sqlery.fastapi_sqlery import database
from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend


@pytest.fixture
def backend(tmp_path, monkeypatch):
    database.init_database(f"sqlite:///{tmp_path / 't.db'}")
    limits = {"TAG_CONCURRENCY_LIMITS": {"api": 1}, "TAG_RATE_LIMITS": {}}
    import sqlery.compat as compat

    real = compat.get_config
    monkeypatch.setattr(compat, "get_config", lambda n, d=None: limits.get(n, real(n, d)))
    b = SQLAlchemyBackend()
    b._limits = limits
    return b


def _mk(b, **kw):
    base = dict(
        task_path="os.getcwd", kwargs={}, queue_name="default", priority=0,
        scheduled_at=None, max_retries=0, retry_backoff=1.0, allow_parallel=False,
        timeout_seconds=None,
    )
    base.update(kw)
    return b.create_job(**base)


def test_config_limits_tolerates_garbage(monkeypatch):
    import sqlery.compat as compat

    monkeypatch.setattr(compat, "get_config", lambda n, d=None: "nope")
    assert _config_limits("TAG_RATE_LIMITS") == {}


def test_concurrency_limit_blocks_tagged_but_not_untagged(backend):
    first = _mk(backend, priority=5, tags=["api"])
    second = _mk(backend, priority=5, tags=["api"])
    plain = _mk(backend, priority=0)
    assert backend.claim_job(["default"], "w").id == first.id
    # second is limited (1 running "api"); the lower-priority untagged job must not starve.
    assert backend.claim_job(["default"], "w").id == plain.id
    assert backend.claim_job(["default"], "w") is None
    assert backend.get_job_by_id(second.id).status == "queued"
    assert backend.count_running_with_tag("api") == 1


def test_rate_limit_blocks_after_window_quota(backend):
    backend._limits["TAG_CONCURRENCY_LIMITS"] = {}
    backend._limits["TAG_RATE_LIMITS"] = {"slow": "1/m"}
    a = _mk(backend, tags=["slow"])
    b = _mk(backend, tags=["slow"])
    got = backend.claim_job(["default"], "w")
    assert got.id == a.id
    assert backend.claim_job(["default"], "w") is None
    assert backend.get_job_by_id(b.id).status == "queued"
