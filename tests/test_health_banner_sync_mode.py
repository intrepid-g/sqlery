"""Regression test: health banner must not cry wolf in sync/eager mode.

REGRESSION 2026-08-14: the dashboard showed "N job(s) queued but no active
workers — restart the worker daemon" while the activity feed was completing
jobs every second. Sync execution drains the queue without registering Worker
rows, so "queued > 0 and no workers" alone is a false alarm. The warning now
requires the queue to actually be stuck: no completions in 5 minutes and the
oldest due queued job waiting past a grace period.
"""

import pytest
from datetime import timedelta

from django.utils import timezone

from sqlery.models import QueuedJob
from sqlery.django_sqlery.views import _compute_health_warnings


def _no_worker_warnings(now):
    return [w for w in _compute_health_warnings(now) if "no active workers" in w["msg"]]


def _make_queued(age_seconds: int) -> QueuedJob:
    job = QueuedJob.objects.create(task_path="tests.noop", status="queued")
    QueuedJob.objects.filter(pk=job.pk).update(
        created_at=timezone.now() - timedelta(seconds=age_seconds)
    )
    return job


@pytest.mark.django_db
def test_no_warning_while_jobs_complete_without_workers():
    """Sync mode: queued jobs + zero workers + recent completions → no banner."""
    now = timezone.now()
    _make_queued(age_seconds=300)
    done = QueuedJob.objects.create(task_path="tests.noop", status="success")
    QueuedJob.objects.filter(pk=done.pk).update(finished_at=now - timedelta(seconds=10))

    assert _no_worker_warnings(now) == []


@pytest.mark.django_db
def test_no_warning_within_grace_period():
    """A just-enqueued burst with no workers yet does not trigger the banner."""
    now = timezone.now()
    _make_queued(age_seconds=5)

    assert _no_worker_warnings(now) == []


@pytest.mark.django_db
def test_warning_when_queue_truly_stuck():
    """Old queued job, no workers, nothing completing → banner shows."""
    now = timezone.now()
    _make_queued(age_seconds=300)

    warnings = _no_worker_warnings(now)
    assert len(warnings) == 1
    assert "restart the worker daemon" in warnings[0]["msg"]
