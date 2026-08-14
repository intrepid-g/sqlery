"""Tests for pure worker-deletion eligibility decisions (no DB)."""


import pytest
from datetime import datetime, timedelta, timezone

from sqlery.core.worker_admin import (
    is_worker_beating,
    is_worker_deletable,
    worker_delete_staleness_threshold_seconds,
)

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
THRESHOLD = worker_delete_staleness_threshold_seconds()


def test_threshold_is_positive_minutes():
    assert worker_delete_staleness_threshold_seconds() == 300


def test_beating_worker_is_not_deletable():
    """A worker that beat 10s ago is alive and protected."""
    last_heartbeat = NOW - timedelta(seconds=10)
    assert is_worker_deletable("idle", last_heartbeat, NOW, THRESHOLD) is False


def test_busy_beating_worker_is_not_deletable():
    last_heartbeat = NOW - timedelta(seconds=5)
    assert is_worker_deletable("busy", last_heartbeat, NOW, THRESHOLD) is False


def test_stale_worker_is_deletable():
    """A worker silent for longer than the threshold can be removed."""
    last_heartbeat = NOW - timedelta(hours=418)
    assert is_worker_deletable("idle", last_heartbeat, NOW, THRESHOLD) is True


def test_worker_exactly_at_threshold_is_deletable():
    last_heartbeat = NOW - timedelta(seconds=THRESHOLD)
    assert is_worker_deletable("idle", last_heartbeat, NOW, THRESHOLD) is True


def test_dead_worker_always_deletable_even_if_recent():
    last_heartbeat = NOW - timedelta(seconds=1)
    assert is_worker_deletable("dead", last_heartbeat, NOW, THRESHOLD) is True


def test_missing_heartbeat_is_deletable():
    assert is_worker_deletable("idle", None, NOW, THRESHOLD) is True


# --- Single definition of "alive" (see is_worker_beating docstring) ---

ALIVE_TIMEOUT = 30


def test_recent_heartbeat_is_beating():
    assert is_worker_beating("idle", NOW - timedelta(seconds=5), NOW, ALIVE_TIMEOUT) is True


def test_unreaped_worker_with_old_heartbeat_is_not_beating():
    """status='idle' but silent for an hour — the destroyed-container case."""
    assert is_worker_beating("idle", NOW - timedelta(hours=1), NOW, ALIVE_TIMEOUT) is False


def test_dead_status_is_never_beating():
    assert is_worker_beating("dead", NOW, NOW, ALIVE_TIMEOUT) is False


def test_missing_heartbeat_is_not_beating():
    assert is_worker_beating("busy", None, NOW, ALIVE_TIMEOUT) is False


def test_heartbeat_exactly_at_timeout_is_not_beating():
    assert is_worker_beating("busy", NOW - timedelta(seconds=ALIVE_TIMEOUT), NOW, ALIVE_TIMEOUT) is False


@pytest.mark.django_db
def test_cleanup_dead_workers_runs_without_field_error():
    """cleanup_dead_workers() must not raise on the demoted current_job FK.

    REGRESSION 2026-08-13: the ghost-running-job sweep still filtered on
    `current_job__isnull`, which raises FieldError since the FK became a plain
    current_job_id column — so the entire reaper aborted every time it ran.
    """
    from django.utils import timezone as dj_tz
    from sqlery.django_sqlery.models import Worker
    from sqlery.django_sqlery.worker_registry import cleanup_dead_workers

    worker = Worker.objects.create(node_id="gone-node", pid=4242, status="idle")
    Worker.objects.filter(pk=worker.pk).update(
        last_heartbeat=dj_tz.now() - timedelta(hours=1)
    )

    assert cleanup_dead_workers() == 1

    worker.refresh_from_db()
    assert worker.status == "dead"
