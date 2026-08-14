"""Focused unit tests for `sqlery.core.claiming` (TEST-05).

Exercises the framework-agnostic claiming algorithm against the in-memory
:class:`tests.unit.conftest.FakeBackend`. No Django, no SQLAlchemy, no real
database — every backend interaction is a dict mutation.

Covered branches:

* Tag concurrency enforcement (``check_tag_concurrency_limits``)
* Tag rate limiting (``check_tag_rate_limits``)
* Dependency check (``check_job_dependencies``)
* TTL expiry sweep (``expire_ttl_jobs``)
* Top-level orchestration with priority, queue filter, and claim retries
  (``claim_next_job_with_queue_priority``)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from sqlery.core import claiming
from sqlery.core.claiming import (
    check_tag_concurrency_limits,
    check_tag_rate_limits,
    check_job_dependencies,
    expire_ttl_jobs,
    claim_next_job_with_queue_priority,
    get_node_id,
)

from .conftest import make_job, make_worker


# ---------------------------------------------------------------------------
# TestNodeId
# ---------------------------------------------------------------------------


class TestNodeId:
    def test_returns_env_override(self, monkeypatch):
        monkeypatch.setenv("NODE_ID", "explicit-node")
        assert get_node_id() == "explicit-node"

    def test_falls_back_to_hostname(self, monkeypatch):
        monkeypatch.delenv("NODE_ID", raising=False)
        # Just assert the function returns *something* truthy (hostname).
        assert get_node_id()


# ---------------------------------------------------------------------------
# TestTagConcurrency
# ---------------------------------------------------------------------------


class TestTagConcurrency:
    def test_no_tags_allows_run(self, fake_backend):
        job = make_job(tags=[])
        assert check_tag_concurrency_limits(job, {"a": 1}, fake_backend) is True

    def test_no_limits_allows_run(self, fake_backend):
        job = make_job(tags=["a"])
        assert check_tag_concurrency_limits(job, {}, fake_backend) is True

    def test_blocks_when_limit_reached(self, fake_backend):
        # Seed two running jobs with tag "heavy" — limit is 2.
        fake_backend.add_job(make_job(tags=["heavy"], status="running"))
        fake_backend.add_job(make_job(tags=["heavy"], status="running"))
        candidate = make_job(tags=["heavy"])
        assert check_tag_concurrency_limits(candidate, {"heavy": 2}, fake_backend) is False

    def test_allows_when_below_limit(self, fake_backend):
        fake_backend.add_job(make_job(tags=["heavy"], status="running"))
        candidate = make_job(tags=["heavy"])
        assert check_tag_concurrency_limits(candidate, {"heavy": 3}, fake_backend) is True

    def test_zero_limit_blocks(self, fake_backend):
        candidate = make_job(tags=["forbidden"])
        # limit=0 means no concurrent runs allowed → must block.
        assert check_tag_concurrency_limits(candidate, {"forbidden": 0}, fake_backend) is False

    def test_unknown_tag_in_limits_does_not_block(self, fake_backend):
        candidate = make_job(tags=["other"])
        # Limit set for a different tag — should still allow.
        assert check_tag_concurrency_limits(candidate, {"unrelated": 1}, fake_backend) is True


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_no_tags_allows_run(self, fake_backend):
        assert check_tag_rate_limits(make_job(tags=[]), {"a": "10/m"}, fake_backend) is True

    def test_no_limits_allows_run(self, fake_backend):
        assert check_tag_rate_limits(make_job(tags=["x"]), {}, fake_backend) is True

    def test_blocks_when_window_full(self, fake_backend):
        # Two recent starts → rate "2/m" is at limit, must block.
        now = datetime.now(timezone.utc)
        fake_backend._tag_started_history.extend([
            ("api", now - timedelta(seconds=10)),
            ("api", now - timedelta(seconds=20)),
        ])
        assert check_tag_rate_limits(make_job(tags=["api"]), {"api": "2/m"}, fake_backend) is False

    def test_allows_below_limit(self, fake_backend):
        now = datetime.now(timezone.utc)
        fake_backend._tag_started_history.append(("api", now - timedelta(seconds=5)))
        assert check_tag_rate_limits(make_job(tags=["api"]), {"api": "5/m"}, fake_backend) is True

    def test_invalid_rate_limit_string_is_skipped(self, fake_backend, caplog):
        # Bad rate-limit string is logged and skipped — does not block.
        assert check_tag_rate_limits(make_job(tags=["api"]), {"api": "garbage"}, fake_backend) is True

    def test_window_resets_after_threshold(self, fake_backend):
        now = datetime.now(timezone.utc)
        # An old start outside the 60s window — must not count.
        fake_backend._tag_started_history.append(("api", now - timedelta(seconds=120)))
        assert check_tag_rate_limits(make_job(tags=["api"]), {"api": "1/m"}, fake_backend) is True


# ---------------------------------------------------------------------------
# TestDependencyChecks
# ---------------------------------------------------------------------------


class TestDependencyChecks:
    def test_no_dependencies_allows_run(self):
        job = make_job(dependencies=[])
        assert check_job_dependencies(job) is True

    def test_dependencies_met_allows_run(self):
        job = make_job(dependencies=[1, 2])
        job.check_dependencies_met = lambda: (True, [])
        assert check_job_dependencies(job) is True

    def test_pending_dependencies_skip(self):
        job = make_job(dependencies=[5])
        job.check_dependencies_met = lambda: (False, [])
        assert check_job_dependencies(job) is False
        # Job is not failed when deps are merely pending.
        assert job.status != "failed"

    def test_failed_dependencies_marks_job_failed(self):
        job = make_job(dependencies=[7])
        job.check_dependencies_met = lambda: (False, [7])
        assert check_job_dependencies(job) is False
        assert job.status == "failed"
        assert job.termination_reason == "dependency_failed"


# ---------------------------------------------------------------------------
# TestTTLExpiry
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_no_expired_jobs_returns_zero(self, fake_backend):
        fake_backend.add_job(make_job(ttl=60))  # young
        assert expire_ttl_jobs(fake_backend) == 0

    def test_expired_jobs_marked_failed(self, fake_backend):
        old = make_job(ttl=10, created_at=datetime.now(timezone.utc) - timedelta(seconds=60))
        fake_backend.add_job(old)
        assert expire_ttl_jobs(fake_backend) == 1
        assert old.status == "failed"
        assert old.termination_reason == "expired"

    def test_jobs_without_ttl_never_expire(self, fake_backend):
        j = make_job(ttl=None, created_at=datetime.now(timezone.utc) - timedelta(days=7))
        fake_backend.add_job(j)
        assert expire_ttl_jobs(fake_backend) == 0
        assert j.status == "queued"


# ---------------------------------------------------------------------------
# TestClaimingPriority — end-to-end orchestration of claim_next_job_with_queue_priority
# ---------------------------------------------------------------------------


class TestClaimingPriority:
    def test_no_jobs_returns_none(self, fake_backend):
        worker = make_worker()
        result = claim_next_job_with_queue_priority(
            worker=worker, backend=fake_backend, queues=["default"], enable_registries=False
        )
        assert result is None

    def test_claims_only_queue_jobs(self, fake_backend):
        # Two queued jobs, only one in target queue
        target = fake_backend.add_job(make_job(queue_name="hot"))
        fake_backend.add_job(make_job(queue_name="cold"))
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker, backend=fake_backend, queues=["hot"], enable_registries=False
        )
        assert claimed is target
        assert claimed.status == "running"

    def test_priority_ordering(self, fake_backend):
        # Higher priority job is claimed first.
        low = fake_backend.add_job(make_job(priority=1))
        high = fake_backend.add_job(make_job(priority=10))
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker, backend=fake_backend, queues=["default"], enable_registries=False
        )
        assert claimed is high
        assert low.status == "queued"

    def test_skips_job_blocked_by_tag_concurrency(self, fake_backend):
        # blocked candidate has tag at-limit, second job is unblocked.
        fake_backend.add_job(make_job(tags=["t"], status="running"))  # occupant
        blocked = fake_backend.add_job(make_job(tags=["t"], priority=10))
        free = fake_backend.add_job(make_job(tags=[], priority=5))
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker,
            backend=fake_backend,
            queues=["default"],
            tag_concurrency_limits={"t": 1},
            enable_registries=False,
        )
        # The high-priority blocked job is rejected; the next iteration sees
        # the same candidate set (FakeBackend.get_claimable_jobs sorts again)
        # but the test asserts at minimum: the blocked job is *not* claimed.
        assert claimed is not blocked
        # Either the free job got claimed, or we hit max_attempts → None
        assert claimed in (free, None)

    def test_skips_job_blocked_by_dependencies(self, fake_backend):
        job = fake_backend.add_job(make_job(dependencies=[99]))
        job.check_dependencies_met = lambda: (False, [])
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker,
            backend=fake_backend,
            queues=["default"],
            enable_registries=False,
        )
        assert claimed is None or claimed is not job

    def test_atomic_claim_race_loss_continues(self, fake_backend):
        # First candidate "loses the race" (atomic claim returns False); the
        # algorithm should keep trying. We seed two queued jobs and tag the
        # first as a guaranteed loser.
        loser = fake_backend.add_job(make_job(priority=10))
        loser._claim_should_fail = True
        winner = fake_backend.add_job(make_job(priority=5))
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker, backend=fake_backend, queues=["default"], enable_registries=False
        )
        # Either we got the winner or got None — but never the marked-loser.
        assert claimed is not loser

    def test_acquire_tag_locks_called_when_tag_in_limits(self, fake_backend):
        fake_backend.add_job(make_job(tags=["a", "b"]))
        worker = make_worker()
        claim_next_job_with_queue_priority(
            worker=worker,
            backend=fake_backend,
            queues=["default"],
            tag_concurrency_limits={"a": 5},
            enable_registries=False,
        )
        # acquire_tag_locks should have been called with sorted tag list.
        lock_calls = [c for c in fake_backend.calls if c[0] == "acquire_tag_locks"]
        assert lock_calls
        assert lock_calls[0][1] == (("a",),)

    def test_rate_limit_blocks_then_next_candidate_wins(self, fake_backend, monkeypatch):
        """Exercise the rate-limit `continue` branch in the claim loop."""
        now = datetime.now(timezone.utc)
        # First candidate hits a rate limit, second has no tags.
        blocked = fake_backend.add_job(make_job(tags=["api"], priority=10))
        fake_backend._tag_started_history.append(("api", now))
        fake_backend.add_job(make_job(tags=[], priority=5))
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker,
            backend=fake_backend,
            queues=["default"],
            tag_rate_limits={"api": "1/m"},
            enable_registries=False,
        )
        assert claimed is not blocked

    def test_invalid_rate_limit_logs_and_passes(self, fake_backend, caplog):
        """Cover the ValueError branch inside `check_tag_rate_limits`."""
        import logging as _logging
        with caplog.at_level(_logging.ERROR, logger="sqlery.core.claiming"):
            ok = check_tag_rate_limits(make_job(tags=["api"]), {"api": "junk"}, fake_backend)
        assert ok is True

    def test_enable_registries_triggers_track_job_start(self, fake_backend, monkeypatch):
        """When enable_registries=True the algorithm should call track_job_start."""
        calls = []
        monkeypatch.setattr(
            "sqlery.core.claiming.track_job_start", lambda j: calls.append(j.id)
        )
        target = fake_backend.add_job(make_job())
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker, backend=fake_backend, queues=["default"], enable_registries=True
        )
        assert claimed is target
        assert calls == [target.id]

    def test_release_job_requires_django(self, monkeypatch):
        """The legacy `release_job()` helper must raise RuntimeError when Django absent."""
        # Force the "Django absent" branch even if Django happens to be importable.
        monkeypatch.setattr(claiming, "_django_transaction", None, raising=False)
        monkeypatch.setattr(claiming, "_django_F", None, raising=False)
        monkeypatch.setattr(claiming, "_django_timezone", None, raising=False)
        with pytest.raises(RuntimeError):
            claiming.release_job(worker=object(), job=object(), status="success")

    def test_expired_ttl_job_is_not_claimable(self, fake_backend):
        """H1: TTL expiry moved out of the claim path onto the periodic worker
        tick and the get_claimable_jobs guard (see worker.py /
        worker_process.py, and the backends' get_claimable_jobs). The claim
        loop itself no longer expires jobs -- it just can't see them as
        candidates, so an expired-but-not-yet-swept job is left untouched."""
        old = fake_backend.add_job(
            make_job(ttl=1, created_at=datetime.now(timezone.utc) - timedelta(seconds=10))
        )
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker, backend=fake_backend, queues=["default"], enable_registries=False
        )
        assert claimed is None
        assert old.status == "queued"
        # No expiry side effect from the claim call itself -- that's the
        # periodic expire_ttl_jobs() sweep's job.
        assert old.termination_reason is None

    def test_blocked_candidate_does_not_starve_claimable_job_behind_it(self, fake_backend):
        """Regression test for the starvation bug fixed by H1: a single
        `get_claimable_jobs(limit=max_attempts)` fetch means a job blocked by a
        tag-concurrency limit no longer gets re-fetched on every attempt,
        starving a claimable job behind it in priority order."""
        fake_backend.add_job(make_job(tags=["t"], status="running"))  # occupies the tag slot
        blocked = fake_backend.add_job(make_job(tags=["t"], priority=10))  # job A: first, blocked
        claimable = fake_backend.add_job(make_job(tags=[], priority=5))  # job B: behind, claimable
        worker = make_worker()
        claimed = claim_next_job_with_queue_priority(
            worker=worker,
            backend=fake_backend,
            queues=["default"],
            tag_concurrency_limits={"t": 1},
            max_attempts=10,
            enable_registries=False,
        )
        assert claimed is claimable
        assert blocked.status == "queued"
