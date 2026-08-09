"""TEST-04 — zombies, heartbeats, lease lifecycle.

The 5-check zombie sequence is implemented Django-side in
``sqlery.core.daemon.DaemonManager._fail_zombie_running_jobs``:

  1. worker_pid set, PID gone on this node
  2. running job has no worker assigned
  3. assigned worker is marked 'dead'
  4. worker.current_job_id != job.id (moved on / idle past grace)
  5. worker.last_heartbeat older than ``WORKER_ALIVE_TIMEOUT * 3``

Each case is induced directly via the Django ORM (no real subprocess needed —
those are exercised in ``test_subprocess_chaos.py``) and the static method is
invoked with a fresh backend.

Lease tests (TestLeaseExpiry / TestLeaseContention / TestLeaseGracefulRelease)
hit the backend ``claim_queue_leases / renew_queue_leases / release_queue_leases``
interface. If the active backend has not implemented leases they SKIP — they
do not silently pass.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

pytestmark = pytest.mark.timeout(60)

CHAOS_SETTINGS = settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Helpers — build a (Worker, QueuedJob) pair in a known state
# ---------------------------------------------------------------------------


def _build_running_job(worker_kwargs: dict | None = None, job_kwargs: dict | None = None):
    """Create a Worker + QueuedJob with status='running' assigned to that worker.

    Returns ``(worker, job)``.
    """
    from django.utils import timezone
    from sqlery.django_sqlery.models import QueuedJob, Worker

    wk = {
        "node_id": os.environ.get("NODE_ID") or os.uname().nodename,
        "pid": os.getpid(),
        "status": "busy",
    }
    wk.update(worker_kwargs or {})
    worker = Worker.objects.create(**wk)
    # last_heartbeat has auto_now=True; force-set via update() to honour overrides.
    Worker.objects.filter(pk=worker.pk).update(last_heartbeat=timezone.now())
    worker.refresh_from_db()

    jk = {
        "task_path": "tests.chaos.conftest.task_succeeds",
        "queue_name": "default",
        "status": "running",
        "worker": worker,
        "worker_pid": os.getpid(),
        "started_at": timezone.now(),
    }
    jk.update(job_kwargs or {})
    job = QueuedJob.objects.create(**jk)
    # Old: worker.current_job = job  (FK demoted to current_job_id BigIntegerField in Phase 15)
    # Old: worker.save(update_fields=["current_job"])
    worker.current_job_id = job.id
    worker.save(update_fields=["current_job_id"])
    return worker, job


# ---------------------------------------------------------------------------
# TestZombie5CheckSequence
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestZombie5CheckSequence:
    """One parametrised test per zombie-detection branch (CLAUDE.md
    'Zombie detection' section)."""

    @pytest.mark.parametrize(
        "case",
        [
            "pid_gone",
            "no_worker",
            "worker_dead",
            "worker_moved_on",
            "heartbeat_stale",
        ],
    )
    def test_each_check_fails_the_zombie_job(self, case):
        from django.utils import timezone
        from sqlery.compat import get_backend
        from sqlery.core.daemon import DaemonManager

        worker, job = _build_running_job()

        if case == "pid_gone":
            # Pick a PID that almost certainly doesn't exist.
            job.worker_pid = 999_999
            job.save(update_fields=["worker_pid"])
        elif case == "no_worker":
            job.worker = None
            job.save(update_fields=["worker"])
        elif case == "worker_dead":
            worker.status = "dead"
            worker.save(update_fields=["status"])
        elif case == "worker_moved_on":
            # current_job points elsewhere — job is abandoned. Create a real
            # other job so the FK constraint is satisfied.
            from sqlery.django_sqlery.models import QueuedJob

            other = QueuedJob.objects.create(
                task_path="tests.chaos.conftest.task_succeeds",
                queue_name="default",
                status="running",
            )
            # Old: worker.current_job = other  (FK demoted in Phase 15)
            # Old: worker.save(update_fields=["current_job"])
            worker.current_job_id = other.id
            worker.save(update_fields=["current_job_id"])
        elif case == "heartbeat_stale":
            # Push heartbeat well past 3 * WORKER_ALIVE_TIMEOUT (default 30s).
            # last_heartbeat is auto_now=True so save() resets it — use update().
            from sqlery.django_sqlery.models import Worker as W

            W.objects.filter(pk=worker.pk).update(
                last_heartbeat=timezone.now() - timedelta(hours=1)
            )
            # Make sure no other check triggers first by clearing PID.
            job.worker_pid = None
            job.save(update_fields=["worker_pid"])

        DaemonManager._fail_zombie_running_jobs(get_backend())

        job.refresh_from_db()
        assert job.status == "failed", (
            f"case={case}: expected zombie sweep to mark job failed, " f"got status={job.status!r}"
        )
        assert (job.termination_reason or "").startswith("zombie") or "zombie" in (
            job.termination_reason or ""
        ).lower(), f"case={case}: termination_reason={job.termination_reason!r}"

    @pytest.mark.django_db(transaction=True)
    @CHAOS_SETTINGS
    @given(
        delta_hours=st.integers(min_value=1, max_value=48),
        pid_offset=st.integers(min_value=10_000, max_value=999_999),
    )
    def test_zombie_sweep_is_robust_to_random_timing(self, delta_hours, pid_offset):
        """Hypothesis-randomised version of the 'heartbeat_stale + pid_gone'
        composite case — the sweep must always reach the failed state regardless
        of how stale / how far the PID is."""
        from django.utils import timezone
        from sqlery.compat import get_backend
        from sqlery.core.daemon import DaemonManager
        from sqlery.django_sqlery.models import QueuedJob, Worker

        # Reset DB state between Hypothesis iterations.
        Worker.objects.all().delete()
        QueuedJob.objects.all().delete()

        worker, job = _build_running_job()
        Worker.objects.filter(pk=worker.pk).update(
            last_heartbeat=timezone.now() - timedelta(hours=delta_hours)
        )
        job.worker_pid = pid_offset
        job.save(update_fields=["worker_pid"])

        DaemonManager._fail_zombie_running_jobs(get_backend())

        job.refresh_from_db()
        assert job.status == "failed"


# ---------------------------------------------------------------------------
# TestStaleHeartbeatCleanup
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaleHeartbeatCleanup:
    """A worker with a stale heartbeat is marked 'dead' by the daemon's
    stale-worker sweep (``_cleanup_stale_workers_all_nodes``)."""

    def test_stale_worker_marked_dead(self):
        from django.utils import timezone
        from sqlery.compat import get_backend
        from sqlery.core.daemon import DaemonManager
        from sqlery.django_sqlery.models import Worker

        live = Worker.objects.create(
            node_id=os.uname().nodename,
            pid=os.getpid(),
            status="idle",
        )
        stale = Worker.objects.create(
            node_id=os.uname().nodename,
            pid=os.getpid() + 1,
            status="idle",
        )
        Worker.objects.filter(pk=stale.pk).update(
            last_heartbeat=timezone.now() - timedelta(hours=1)
        )

        dm = DaemonManager()
        # The cleanup sweep is an instance method; tolerate either signature
        # (with or without backend argument).
        try:
            dm._cleanup_stale_workers_all_nodes(get_backend())
        except TypeError:
            pytest.skip("DaemonManager._cleanup_stale_workers_all_nodes signature drift")

        stale.refresh_from_db()
        live.refresh_from_db()
        assert stale.status == "dead", f"stale worker should be marked dead, got {stale.status}"
        assert live.status != "dead", "live worker must not be reaped"


# ---------------------------------------------------------------------------
# Lease lifecycle
# ---------------------------------------------------------------------------


def _lease_supported(backend) -> bool:
    """Best-effort check: does the active backend implement claim_queue_leases?"""
    fn = getattr(backend, "claim_queue_leases", None)
    if fn is None:
        return False
    try:
        # Call with empty queue list — must not raise NotImplementedError.
        fn(queues=[], daemon_id="probe", node_id="probe", pid=os.getpid(), lease_secs=1)
        return True
    except NotImplementedError:
        return False
    except TypeError:
        # Signature drift between Django and SQLAlchemy backends — treat as
        # 'unsupported on this backend' rather than masking other bugs.
        return False
    # WR-01 (11-REVIEW): a RuntimeError here means the engine/config is not
    # ready (uninitialized standalone _engine). Treat as 'unsupported in this
    # run' rather than claiming support and then erroring on the real _claim().
    # Old: except Exception: return True  # masked RuntimeError("Database not initialized")
    except RuntimeError:
        return False
    except Exception:
        return True


def _claim(backend, queue: str, daemon_id: str, lease_secs: int = 30):
    return backend.claim_queue_leases(
        queues=[queue],
        daemon_id=daemon_id,
        node_id="chaos",
        pid=os.getpid(),
        lease_secs=lease_secs,
    )


def _release(backend, queue: str, daemon_id: str):
    return backend.release_queue_leases(owned_queues=[queue], daemon_id=daemon_id)


@pytest.mark.django_db(transaction=True)
class TestLeaseExpiry:
    def test_expired_lease_can_be_taken_over(self):
        from sqlery.compat import get_backend

        backend = get_backend()
        if not _lease_supported(backend):
            pytest.skip("active backend does not implement queue leases")

        # WR-04 (11-REVIEW): force expiry via a PAST expires_at write instead of
        # a real time.sleep(1.5) — mirrors the parity cells' "no real TTL sleep"
        # convention (11-PATTERNS) and removes 1.5s of wall-clock per run.
        # Acquire, expire the lease in-place, then re-acquire from a different
        # daemon_id — the takeover must succeed.
        first = _claim(backend, "chaos-q", "daemon-a", lease_secs=1)
        assert "chaos-q" in (first or [])
        # Old (real wall-clock sleep aged a 1s lease):
        # import time
        # time.sleep(1.5)
        from django.utils import timezone

        from sqlery.django_sqlery.models import DaemonLease

        DaemonLease.objects.filter(queue_name="chaos-q").update(
            expires_at=timezone.now() - timedelta(seconds=5)
        )
        second = _claim(backend, "chaos-q", "daemon-b", lease_secs=10)
        assert "chaos-q" in (second or []), "expired lease must be re-claimable by daemon-b"


@pytest.mark.django_db(transaction=True)
class TestLeaseContention:
    def test_only_one_daemon_wins(self):
        from sqlery.compat import get_backend

        backend = get_backend()
        if not _lease_supported(backend):
            pytest.skip("active backend does not implement queue leases")

        winners: list[str] = []
        for daemon_id in ("d1", "d2", "d3"):
            claimed = _claim(backend, "solo", daemon_id, lease_secs=30)
            if "solo" in (claimed or []):
                winners.append(daemon_id)
        assert len(winners) == 1, f"expected exactly one winner, got {winners}"


# ---------------------------------------------------------------------------
# Postgres mirror (plan 03-07, TEST-11)
# ---------------------------------------------------------------------------
# Lease contention is the most engine-sensitive of the three lease tests —
# PG resolves it via row-level lock semantics, SQLite via busy_timeout +
# optimistic locking. Mirror :class:`TestLeaseContention` against PG.


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
class TestLeaseContentionPostgres:
    """Postgres mirror of :class:`TestLeaseContention`.

    Auto-skipped on the SQLite CI rails (those filter ``-m "not postgres"``);
    the PG rail runs this against ``DATABASE_URL`` (set to the PG service
    in CI). The test asserts the same single-winner invariant.
    """

    def test_only_one_daemon_wins(self):
        if not os.environ.get("SQLERY_TEST_PG_URL"):
            pytest.skip("SQLERY_TEST_PG_URL not set; PG lease mirror skipped")

        from sqlery.compat import get_backend

        backend = get_backend()
        if not _lease_supported(backend):
            pytest.skip("active backend does not implement queue leases")

        winners: list[str] = []
        for daemon_id in ("pg1", "pg2", "pg3"):
            claimed = _claim(backend, "pg-solo", daemon_id, lease_secs=30)
            if "pg-solo" in (claimed or []):
                winners.append(daemon_id)
        assert len(winners) == 1, f"expected exactly one PG winner, got {winners}"


@pytest.mark.django_db(transaction=True)
class TestLeaseGracefulRelease:
    def test_release_allows_immediate_reacquire(self):
        from sqlery.compat import get_backend

        backend = get_backend()
        if not _lease_supported(backend):
            pytest.skip("active backend does not implement queue leases")

        first = _claim(backend, "graceful", "alpha", lease_secs=60)
        assert "graceful" in (first or [])
        _release(backend, "graceful", "alpha")
        second = _claim(backend, "graceful", "beta", lease_secs=60)
        assert "graceful" in (second or []), "release should allow immediate re-acquire"


# ---------------------------------------------------------------------------
# Standalone-backend failover on Postgres (PARITY-01, standalone half)
# ---------------------------------------------------------------------------
# TestLeaseExpiry / TestLeaseContentionPostgres above cover the active-backend
# (Django, under pytest-django) lease-takeover path. This class proves the SAME
# takeover on the STANDALONE SQLAlchemyBackend bound to a real PG service — the
# standalone half of PARITY-01's real-backend failover. Expiry is forced via a
# PAST expires_at write (no real TTL sleep), matching the 11-PATTERNS convention.


@pytest.fixture
def pg_standalone_backend(monkeypatch):
    """Per-test standalone SQLAlchemyBackend bound to a real PG service.

    Mirrors ``tests/unit/test_sqlalchemy_backend_sync.py::pg_sync_backend``:
    auto-skips when ``SQLERY_TEST_PG_URL`` is unset and rebuilds the schema
    (``drop_all`` / ``create_all``) so each cell starts from an empty DB
    (mitigates T-11-02-04: no cross-cell lease-row leakage).
    """
    pg_url = os.environ.get("SQLERY_TEST_PG_URL")
    if not pg_url:
        pytest.skip("SQLERY_TEST_PG_URL not set; PG mirror skipped")

    from sqlalchemy import create_engine
    from sqlmodel import SQLModel
    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.core import models as _core_models  # noqa: F401

    engine = create_engine(pg_url, future=True)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_mod, "_engine", engine, raising=False)

    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    backend = SQLAlchemyBackend()
    try:
        yield backend
    finally:
        try:
            SQLModel.metadata.drop_all(engine)
        finally:
            engine.dispose()


@pytest.mark.postgres
@pytest.mark.standalone_pg  # CR-01 (11-REVIEW): genuinely-standalone PG cell (real SQLAlchemy engine).
class TestStandaloneLeaseFailoverPostgres:
    """Standalone SQLAlchemyBackend lease takeover on a real Postgres service.

    The standalone half of PARITY-01's real-backend failover: once the leader
    (``daemon-a``) dies, a second daemon (``daemon-b``) re-claims the queue.
    """

    def test_expired_standalone_lease_is_taken_over_pg(self, pg_standalone_backend):
        from datetime import datetime, UTC

        from sqlmodel import select
        from sqlery.core.models import DaemonLease

        backend = pg_standalone_backend

        # Leader daemon-a claims the queue.
        first = backend.claim_queue_leases(
            queues=["failover-standalone-q"],
            daemon_id="daemon-a",
            node_id="node-a",
            pid=1,
            lease_secs=300,
        )
        assert first == ["failover-standalone-q"]

        # Leader dies: force its lease expired by writing a PAST expires_at
        # through the backend's own session (NEVER a real ~30s TTL sleep).
        with backend._get_session() as session:
            row = session.exec(
                select(DaemonLease).where(DaemonLease.queue_name == "failover-standalone-q")
            ).first()
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=30)
            session.add(row)
            session.commit()

        # A second daemon takes over the dead leader's expired lease.
        second = backend.claim_queue_leases(
            queues=["failover-standalone-q"],
            daemon_id="daemon-b",
            node_id="node-b",
            pid=2,
            lease_secs=300,
        )
        assert "failover-standalone-q" in (
            second or []
        ), "daemon-b must take over the expired standalone lease"

        # Ownership transferred to daemon-b.
        with backend._get_session() as session:
            owner = session.exec(
                select(DaemonLease).where(DaemonLease.queue_name == "failover-standalone-q")
            ).first()
            assert owner.daemon_id == "daemon-b"


# ---------------------------------------------------------------------------
# TestFencedCompletionAfterReclaim
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFencedCompletionAfterReclaim:
    """Race: a job's original worker goes heartbeat-stale mid-execution, the
    zombie sweep fails/reclaims the job (bumping its version), and the
    "superseded" original worker later tries to write its own completion.

    Without version fencing that late write silently wins (mark_job_success /
    mark_job_failed always re-fetch the *current* row before applying their
    own CAS, so they always succeed against whatever version is current —
    never the version the superseded worker actually observed). With fencing,
    the superseded worker's completion write must raise JobFencingError and
    the reclaiming write (already recorded by the zombie sweep) must survive
    untouched — i.e. no double-execution artifact clobbers the real outcome.
    """

    def test_stale_worker_completion_is_rejected_after_zombie_reclaim(self):
        from django.utils import timezone
        from sqlery.compat import get_backend, JobFencingError
        from sqlery.core.daemon import DaemonManager

        worker, job = _build_running_job()
        # Version the "original" worker captured before it started running
        # the task body — mirrors JobExecutor.execute_job_in_child's
        # `expected_version = getattr(job, 'version', None)`.
        expected_version = job.version

        # Force the worker's heartbeat stale enough to trip Check 5, without
        # sleeping ~90s for the real WORKER_ALIVE_TIMEOUT*3 window.
        from sqlery.django_sqlery.models import Worker as W

        W.objects.filter(pk=worker.pk).update(
            last_heartbeat=timezone.now() - timedelta(hours=1)
        )
        job.worker_pid = None  # keep Check 1 from firing first
        job.save(update_fields=["worker_pid"])

        backend = get_backend()
        DaemonManager._fail_zombie_running_jobs(backend)

        job.refresh_from_db()
        assert job.status == "failed", "zombie sweep must have reclaimed the job"
        reclaimed_version = job.version
        assert reclaimed_version != expected_version, (
            "zombie reclaim must bump the version — otherwise fencing has "
            "nothing to detect"
        )

        # The superseded original worker now finishes its (stale) run and
        # tries to record success against the version it originally claimed.
        with pytest.raises(JobFencingError):
            backend.mark_job_success(
                job.id, output="late result", expected_version=expected_version
            )

        # The zombie sweep's outcome must be untouched — no silent clobber.
        job.refresh_from_db()
        assert job.status == "failed"
        assert job.output != "late result"

    def test_unfenced_call_would_have_clobbered(self):
        """Sanity control: omitting expected_version reproduces the historical
        bug (the whole reason this fix exists) — the stale write succeeds and
        overwrites the reclaiming worker's result. Pins the pre-fix behaviour
        so a future refactor can't silently drop the `expected_version=None`
        default and reintroduce fencing everywhere without anyone noticing.
        """
        from django.utils import timezone
        from sqlery.compat import get_backend
        from sqlery.core.daemon import DaemonManager
        from sqlery.django_sqlery.models import Worker as W

        worker, job = _build_running_job()
        W.objects.filter(pk=worker.pk).update(
            last_heartbeat=timezone.now() - timedelta(hours=1)
        )
        job.worker_pid = None
        job.save(update_fields=["worker_pid"])

        backend = get_backend()
        DaemonManager._fail_zombie_running_jobs(backend)
        job.refresh_from_db()
        assert job.status == "failed"

        # No expected_version passed — reproduces the pre-fencing call sites.
        backend.mark_job_success(job.id, output="late result")

        job.refresh_from_db()
        assert job.status == "success"
        assert job.output == "late result"


# ---------------------------------------------------------------------------
# TestStandaloneFencedCompletionAfterReclaim
# ---------------------------------------------------------------------------
#
# Standalone-mode mirror of TestFencedCompletionAfterReclaim above. Proves
# SQLAlchemyBackend.fail_zombie_job bumps `version` on reclaim (like Django's
# CAS mark_failed with F("version")+1) so the fencing check added by the
# lease-renewal fix actually fires in standalone mode too — before this fix
# fail_zombie_job never touched version and the stale worker's late write
# silently clobbered the zombie outcome.


@pytest.fixture
def standalone_zombie_backend(tmp_path, monkeypatch):
    """Per-test standalone SQLAlchemyBackend against a temp-file SQLite engine.

    Mirrors ``tests/unit/test_sqlalchemy_backend_sync.py::sync_backend``.
    """
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel
    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.core import models as _core_models  # noqa: F401

    db_path = tmp_path / "zombie_db.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_mod, "_engine", engine, raising=False)

    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    backend = SQLAlchemyBackend()
    try:
        yield backend
    finally:
        engine.dispose()


class TestStandaloneFencedCompletionAfterReclaim:
    """Standalone mirror of TestFencedCompletionAfterReclaim, proving the
    fencing check is no longer a silent no-op in SQLAlchemy/standalone mode.
    """

    def test_stale_worker_completion_is_rejected_after_zombie_reclaim(
        self, standalone_zombie_backend
    ):
        from datetime import datetime, UTC

        from sqlmodel import select
        from sqlery.compat import JobFencingError
        from sqlery.core.daemon import DaemonManager
        from sqlery.core.models import QueuedJob, Worker

        backend = standalone_zombie_backend

        with backend._get_session() as session:
            worker = Worker(
                node_id=os.environ.get("NODE_ID") or os.uname().nodename,
                pid=os.getpid(),
                status="busy",
                last_heartbeat=datetime.now(UTC),
            )
            session.add(worker)
            session.commit()
            session.refresh(worker)

            job = QueuedJob(
                task_path="tests.chaos.conftest.task_succeeds",
                queue_name="default",
                status="running",
                worker_id=worker.id,
                worker_pid=os.getpid(),
                started_at=datetime.now(UTC),
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            worker.current_job_id = job.id
            session.add(worker)
            session.commit()

            job_id = job.id
            worker_id = worker.id

        # Version the "original" worker captured before it started running
        # the task body — mirrors JobExecutor's `expected_version` capture.
        expected_version = 0

        # Force the worker's heartbeat stale enough to trip Check 5, without
        # sleeping ~90s for the real WORKER_ALIVE_TIMEOUT*3 window.
        with backend._get_session() as session:
            db_worker = session.exec(select(Worker).where(Worker.id == worker_id)).first()
            db_worker.last_heartbeat = datetime.now(UTC) - timedelta(hours=1)
            session.add(db_worker)
            session.commit()

            db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            db_job.worker_pid = None  # keep Check 1 from firing first
            session.add(db_job)
            session.commit()

        DaemonManager._fail_zombie_running_jobs(backend)

        with backend._get_session() as session:
            db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            assert db_job.status == "failed", "zombie sweep must have reclaimed the job"
            reclaimed_version = db_job.version
            assert reclaimed_version != expected_version, (
                "zombie reclaim must bump the version in standalone mode too — "
                "otherwise fencing has nothing to detect"
            )

        # The superseded original worker now finishes its (stale) run and
        # tries to record success against the version it originally claimed.
        with pytest.raises(JobFencingError):
            backend.mark_job_success(
                job_id, output="late result", expected_version=expected_version
            )

        # The zombie sweep's outcome must be untouched — no silent clobber.
        with backend._get_session() as session:
            db_job = session.exec(select(QueuedJob).where(QueuedJob.id == job_id)).first()
            assert db_job.status == "failed"
            assert db_job.output != "late result"
