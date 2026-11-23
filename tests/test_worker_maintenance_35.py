"""A bare WorkerProcess does the daemon's housekeeping when it holds a lease (#35)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sqlery.core import maintenance
from tests.test_ready_queue_139 import DjangoMode, StandaloneMode, mode  # noqa: F401


def test_maintenance_runs_at_once_after_a_restart_then_waits_the_interval():
    t0 = datetime(2026, 11, 22, 13, 5, tzinfo=UTC)
    assert maintenance.is_due(None, 5, t0)
    assert not maintenance.is_due(t0, 5, t0 + timedelta(minutes=4, seconds=59))
    assert maintenance.is_due(t0, 5, t0 + timedelta(minutes=5))


def test_a_tick_fails_the_job_a_dead_worker_left_running(mode):
    worker = "worker_deadhost_999999"
    mode.backend.update_worker_heartbeat(worker, "busy")
    job = mode.new_job()
    assert mode.backend.claim_job(["default"], worker).id == job.id
    mode.backend.update_worker_heartbeat(worker, "dead")  # the worker crashed mid-job
    result = maintenance.maintenance_tick(mode.backend, None)
    assert result["zombies_failed"] == 1
    assert mode.backend.get_running_jobs() == []


def test_failing_a_zombie_job_leaves_its_silent_worker_for_cleanup_to_reap(mode):
    if not isinstance(mode, DjangoMode):
        pytest.skip("Django worker rows only")
    from django.utils import timezone

    from sqlery.django_sqlery.models import Worker
    from sqlery.django_sqlery.worker_registry import cleanup_dead_workers

    worker = "worker_deadhost_888888"
    mode.backend.update_worker_heartbeat(worker, "busy")
    job = mode.new_job()
    assert mode.backend.claim_job(["default"], worker).id == job.id
    stale = timezone.now() - timedelta(hours=8)
    Worker.objects.filter(node_id="deadhost", pid=888888).update(last_heartbeat=stale)
    assert mode.backend.fail_zombie_job(job.id, "zombie")
    row = Worker.objects.get(node_id="deadhost", pid=888888)
    assert row.last_heartbeat <= stale + timedelta(seconds=1)  # release did not refresh it
    cleanup_dead_workers()
    assert Worker.objects.get(node_id="deadhost", pid=888888).status == "dead"
