"""Regression test: dashboard must hide workers inactive for >24h.

REGRESSION 2026-06-16: a worker whose last_heartbeat was hundreds of hours old
still appeared in the dashboard workers table. The dashboard_stats() query
filtered on status in ('idle','busy') but never bounded heartbeat age, so a
worker that died without flipping its status row rendered forever.
"""

import json

import pytest
from datetime import timedelta

from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import caches
from django.utils import timezone

from sqlery.models import Worker
from sqlery.django_sqlery.views import dashboard_stats


class _StaffUser:
    """Minimal user that satisfies @staff_required_json."""

    is_active = True
    is_staff = True


def _stats_payload(rf):
    """Invoke dashboard_stats as a staff user with a clean cache, return parsed JSON."""
    caches['default'].clear()  # drop the 2s stats cache so we compute fresh
    request = rf.get("/admin/api/sqlery/stats/")
    request.user = _StaffUser()
    request.session = SessionStore()  # session_key is None → skips rate limit
    response = dashboard_stats(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.mark.django_db
def test_dashboard_excludes_workers_idle_more_than_24h(rf):
    """A stale idle worker (>24h heartbeat) is omitted; a fresh one is kept."""
    now = timezone.now()

    fresh = Worker.objects.create(node_id="fresh-node", pid=111, status="idle")

    stale = Worker.objects.create(node_id="stale-node", pid=222, status="idle")
    # 418 hours ago, matching the reported screenshot.
    Worker.objects.filter(pk=stale.pk).update(last_heartbeat=now - timedelta(hours=418))

    payload = _stats_payload(rf)
    node_ids = {w['node_id'] for w in payload['workers_list']}

    assert "fresh-node" in node_ids
    assert "stale-node" not in node_ids  # would FAIL before the 24h cutoff fix


@pytest.mark.django_db
def test_worker_counts_exclude_workers_that_stopped_beating(rf):
    """REGRESSION 2026-08-13: counts read the status column with no heartbeat bound.

    A container destroyed with ENABLE_DAEMON=False leaves rows at status='idle'
    forever, because only the daemon-only reaper writes status='dead'. The
    dashboard then reported those workers as active and, since has_active_workers
    feeds stuck-queue detection, called a queue with zero real workers healthy.
    """
    now = timezone.now()

    Worker.objects.create(node_id="beating-node", pid=111, status="idle")

    ghost = Worker.objects.create(node_id="ghost-node", pid=222, status="busy")
    # Never reaped: status still 'busy', but silent for an hour.
    Worker.objects.filter(pk=ghost.pk).update(last_heartbeat=now - timedelta(hours=1))

    payload = _stats_payload(rf)
    stats = payload['worker_stats']

    assert stats['active'] == 1  # only the beating worker
    assert stats['busy'] == 0  # the ghost's 'busy' status must not count
    assert stats['dead'] == 1
    assert payload['system_health']['has_active_workers'] is True


@pytest.mark.django_db
def test_queue_with_only_ghost_workers_is_not_reported_as_having_workers(rf):
    """Zero beating workers ⇒ has_active_workers is False, whatever the status column says."""
    now = timezone.now()

    ghost = Worker.objects.create(node_id="ghost-only", pid=333, status="idle")
    Worker.objects.filter(pk=ghost.pk).update(last_heartbeat=now - timedelta(hours=1))

    payload = _stats_payload(rf)

    assert payload['worker_stats']['active'] == 0
    assert payload['system_health']['has_active_workers'] is False
