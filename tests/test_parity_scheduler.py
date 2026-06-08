"""PARITY-01 (failover) + PARITY-04 (bare-worker E2E) across the (integration, db) axis.

This module generalizes the SQLite-proven Phase 9 ``TestWorkerSchedulerElection``
precedent (``tests/unit/test_worker.py``) to the full parity grid and adds the
Postgres cells Phase 9 deferred:

* **PARITY-01 (failover)** — killing the lease leader causes another worker to
  re-claim the queue and fire its due cron within one TTL. Proven in-process on
  SQLite via ``FakeBackend`` + ``_run_one_election_cycle`` (PAST ``expires_at``),
  and against a real Django backend on Postgres via ``claim_queue_leases`` takeover.
* **PARITY-04 (bare-worker E2E)** — a bare ``sqlery-worker`` (no ``DaemonManager``
  constructed anywhere) self-elects and fires a due cron. Proven in-process on
  SQLite, and via a real no-Django standalone subprocess (``slow``).

Failover is ALWAYS simulated via a PAST ``expires_at`` on the inner-loop cells —
no test waits a real ~30s TTL (the production failover window is
``check_interval * 3``; see ELECT-06). ``time.sleep`` is monkeypatched to a no-op
in the in-process cells (mitigates T-11-02-01). The freshly enqueued ``QueuedJob``
is the load-bearing assertion (count == 1): a cell cannot pass unless
``WorkerProcess.run`` election actually ran and fired the cron (mitigates
T-11-02-02). No ``DaemonManager`` is constructed in the bare-worker cells
(mitigates T-11-02-03).

All Postgres cells carry ``@pytest.mark.postgres`` and SKIP cleanly when
``SQLERY_TEST_PG_URL`` is unset (they are excluded from the SQLite rail via
``-m "not postgres"`` and run on the dedicated PG rail). No production source is
changed; the election + failover wiring shipped in Phases 8-9.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from sqlery.core.worker import WorkerProcess

# Reuse the proven election harness verbatim rather than duplicating it — these
# are the exact helpers `TestWorkerSchedulerElection` drives the wiring with.
from tests.unit.conftest import FakeBackend, _utcnow, make_scheduled_task
from tests.unit.test_worker import (
    _claimed_queues,
    _job_count_for_task,
    _run_one_election_cycle,
    _seed_due_task,
)


# ---------------------------------------------------------------------------
# db axis — mirrors tests/integration/conftest.py::db_engine (lines 579-596).
# The SQLite cell stays unmarked (default rail); the Postgres param carries the
# postgres marker so it only runs on the PG rail and auto-skips without the URL.
# ---------------------------------------------------------------------------
@pytest.fixture(
    params=[
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.postgres),
    ]
)
def db(request):
    """Parametrize a cell across both engines with the PG-only marker."""
    if request.param == "postgres":
        if not os.environ.get("SQLERY_TEST_PG_URL"):
            pytest.skip("postgres engine requires SQLERY_TEST_PG_URL")
        # WR-B (11-REVIEW iter2): -m selection is driven by the static param mark
        # (pytest.param("postgres", marks=pytest.mark.postgres)) resolved at collection
        # time. This add_marker runs at fixture-setup (post-collection) and is therefore
        # a NO-OP for `-m postgres` / `-m "postgres and standalone_pg"` filtering; it is
        # decorative only (visible to request.node.iter_markers introspection). Do NOT
        # rely on it for selection.
        request.node.add_marker(pytest.mark.postgres)
    return request.param


# ---------------------------------------------------------------------------
# Shared helpers for the real-backend (Postgres) failover cells.
# Mirror tests/chaos/test_lease_zombie.py::_lease_supported / _claim.
# ---------------------------------------------------------------------------
def _lease_supported(backend) -> bool:
    """Best-effort check: does the active backend implement claim_queue_leases?"""
    fn = getattr(backend, "claim_queue_leases", None)
    if fn is None:
        return False
    try:
        fn(queues=[], daemon_id="probe", node_id="probe", pid=os.getpid(), lease_secs=1)
        return True
    except NotImplementedError:
        return False
    except TypeError:
        return False
    # WR-01 (11-REVIEW): a RuntimeError here means the engine/config is not
    # ready (e.g. SQLAlchemyBackend with an uninitialized _engine). Treat that
    # as "unsupported in this run" rather than claiming support and then erroring
    # on the real _claim() call far from the cause.
    # Old: except Exception: return True  # masked RuntimeError("Database not initialized")
    except RuntimeError:
        return False
    except Exception:
        return True


def _claim(backend, queue: str, daemon_id: str, lease_secs: int = 30):
    return backend.claim_queue_leases(
        queues=[queue],
        daemon_id=daemon_id,
        node_id="parity",
        pid=os.getpid(),
        lease_secs=lease_secs,
    )


# ===========================================================================
# PARITY-01 — Failover: a dead leader's lease is taken over within one TTL.
# ===========================================================================
class TestParityFailover:
    """A second worker re-claims a dead leader's queue and fires its cron.

    SQLite cell drives the real ``WorkerProcess.run`` election against
    ``FakeBackend`` (PAST ``expires_at`` = dead leader); the Postgres cell
    proves the same takeover on the real Django backend's row-lock semantics.
    """

    def test_failover_sqlite_in_process(self, monkeypatch):
        """PARITY-01 SQLite: dead leader's lease (PAST expires_at) is re-claimed
        and the due cron fires — ported from
        ``test_expired_lease_is_taken_over_and_cron_fires`` (test_worker.py 541-567)."""
        fake_backend = FakeBackend()
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        # Prior leader is dead: its lease is already expired (NEVER a real TTL
        # sleep — the PAST expires_at simulates the ~30s check_interval*3 window).
        fake_backend._leases["default"] = {
            "daemon_id": "dead_leader",
            "node_id": "dead-node",
            "pid": 111,
            "expires_at": _utcnow() - timedelta(seconds=5),
        }
        task = _seed_due_task(fake_backend, name="failover-cron", queue_name="default")

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        # Takeover is asserted via the claim record + the freshly enqueued job
        # (run()'s finally: releases held leases, so post-cycle _leases is empty).
        assert "default" in _claimed_queues(lease_calls, wp.worker_id)
        assert _job_count_for_task(fake_backend, task) == 1

    @pytest.mark.postgres
    @pytest.mark.django_db(transaction=True)
    def test_failover_postgres_real_backend(self, db):
        """PARITY-01 Postgres: a second daemon takes over an expired lease on the
        real Django backend (row-lock takeover) — mirrors ``TestLeaseExpiry`` but
        forces expiry via a PAST ``expires_at`` write rather than a real sleep."""
        if db != "postgres":
            pytest.skip("PG cell only runs on the postgres param")
        if not os.environ.get("SQLERY_TEST_PG_URL"):
            pytest.skip("SQLERY_TEST_PG_URL not set; PG failover cell skipped")

        from django.utils import timezone

        from sqlery.compat import get_backend
        from sqlery.django_sqlery.models import DaemonLease

        backend = get_backend()
        if not _lease_supported(backend):
            pytest.skip("active backend does not implement queue leases")

        # Leader daemon-a claims the queue, then dies: force its lease expired by
        # writing a PAST expires_at (no real TTL sleep, per 11-PATTERNS).
        first = _claim(backend, "failover-q", "daemon-a", lease_secs=300)
        assert "failover-q" in (first or [])
        DaemonLease.objects.filter(queue_name="failover-q").update(
            expires_at=timezone.now() - timedelta(seconds=5)
        )

        # A second worker (daemon-b) takes over the dead leader's queue.
        second = _claim(backend, "failover-q", "daemon-b", lease_secs=300)
        assert "failover-q" in (second or []), "daemon-b must take over the expired lease"
        assert (
            DaemonLease.objects.get(queue_name="failover-q").daemon_id == "daemon-b"
        ), "the takeover must transfer ownership to daemon-b"


# ===========================================================================
# PARITY-04 — Bare-worker E2E: a worker (no daemon) self-elects + fires cron.
# ===========================================================================
class TestParityBareWorkerE2E:
    """A bare ``WorkerProcess`` — with NO ``DaemonManager`` constructed — fires a
    due cron for a queue it self-elects to lead.

    SQLite cell drives the real election in-process; the standalone cell drives a
    real no-Django subprocess (``slow``) to prove true process isolation.
    """

    def test_bare_worker_sqlite_in_process(self, monkeypatch):
        """PARITY-04 SQLite: a bare worker self-elects and fires a due cron —
        ported from ``test_bare_worker_fires_due_cron_for_held_queue``
        (test_worker.py 465-478)."""
        fake_backend = FakeBackend()
        # NOTE: no DaemonManager is constructed anywhere in this cell — only a
        # bare WorkerProcess. The enqueued job is the proof of self-election.
        wp = WorkerProcess(queues=["default"], backend=fake_backend)
        task = _seed_due_task(fake_backend, name="bare-cron", queue_name="default")

        lease_calls = _run_one_election_cycle(wp, monkeypatch)

        assert "default" in _claimed_queues(lease_calls, wp.worker_id)
        # run_due_tasks fires only for held queues, so the single enqueued job
        # can only exist if the bare worker self-elected as leader for `default`.
        assert _job_count_for_task(fake_backend, task) == 1

    @pytest.mark.slow
    # CR-01 (11-REVIEW): genuinely-standalone PG cell (real no-Django subprocess).
    # standalone_pg marks BOTH db params, but only the [postgres] param also
    # carries @pytest.mark.postgres (added by the `db` fixture), so the CI
    # selector `-m "postgres and standalone_pg"` collects ONLY the postgres param.
    @pytest.mark.standalone_pg
    def test_bare_worker_standalone_real_process(self, db):
        """PARITY-04 standalone E2E: a real no-Django ``sqlery`` process with NO
        daemon constructs a bare ``WorkerProcess`` that self-elects and fires a
        due cron. The printed QueuedJob count (== 1) is the load-bearing proof.

        Uses ``_run_no_django`` so ``DJANGO_SETTINGS_MODULE`` is scrubbed and
        ``SQLERY_FORCE_STANDALONE=1`` is set (the harness handles both)."""
        import tempfile

        from tests.integration.conftest import _run_no_django

        if db == "postgres":
            if not os.environ.get("SQLERY_TEST_PG_URL"):
                pytest.skip("SQLERY_TEST_PG_URL not set; PG standalone E2E skipped")
            db_url = os.environ["SQLERY_TEST_PG_URL"]
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
            tmp.close()
            db_url = f"sqlite:///{tmp.name}"

        # The script runs in a Django-free subprocess. It seeds a DUE cron task
        # (past next_run_at), constructs ONLY a WorkerProcess (no DaemonManager),
        # drives exactly one bounded election pass (claim_job flips
        # shutdown_requested + returns None; time.sleep is a no-op), and prints
        # the resulting QueuedJob count. count == 1 proves the bare standalone
        # worker fired the cron with no daemon.
        script = f"""
from datetime import datetime, timedelta, timezone as dt_timezone

from sqlery.compat import initialize, get_backend

initialize(database_url={db_url!r}, enable_daemon=False)
backend = get_backend()

# Seed a cron task, then force it DUE by writing a PAST next_run_at directly.
task = backend.create_scheduled_task(
    name="bare-standalone-cron",
    task_path="tests.chaos.conftest.task_succeeds",
    cron_expression="*/5 * * * *",
    queue_name="default",
    priority=0,
    enabled=True,
)
from sqlmodel import select
from sqlery.core.models import ScheduledTask, QueuedJob
past = datetime.now(dt_timezone.utc) - timedelta(seconds=5)
with backend._get_session() as session:
    row = session.exec(select(ScheduledTask).where(ScheduledTask.id == task.id)).first()
    row.next_run_at = past
    session.add(row)
    session.commit()

from sqlery.core import worker as worker_module
from sqlery.core.worker import WorkerProcess

# NO DaemonManager — a bare worker only.
wp = WorkerProcess(queues=["default"], backend=backend)

# Bound the run to exactly one election pass without any real wall-clock sleep.
worker_module.time.sleep = lambda *a, **k: None
# django IS installed in dev, so worker_module.close_old_connections is the real
# Django fn; with no DJANGO_SETTINGS_MODULE it raises ImproperlyConfigured and the
# worker's broad except would spin forever. The bare standalone worker has no
# Django connections to prune, so no-op it (mirrors _run_one_election_cycle).
worker_module.close_old_connections = None
_real_claim_job = backend.claim_job
def _claim_then_stop(queues, worker_id):
    _real_claim_job(queues, worker_id)
    wp.shutdown_requested = True
    return None
backend.claim_job = _claim_then_stop

wp.run()

with backend._get_session() as session:
    count = len(
        session.exec(
            select(QueuedJob).where(QueuedJob.scheduled_task_id == task.id)
        ).all()
    )
print("JOB_COUNT=" + str(count))
"""
        out = _run_no_django(script, timeout=60)
        assert "JOB_COUNT=1" in out, (
            "a bare standalone worker (no daemon) must fire the due cron exactly "
            f"once; subprocess output was:\n{out}"
        )
