"""Precedents for the Worker party."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from sqlery.contract import worker


def test_release_sets_the_worker_idle_and_clears_its_job():
    assert worker.release_fields() == {"status": "idle", "current_job_id": None}


def test_release_does_not_write_a_heartbeat():
    assert "last_heartbeat" not in worker.release_fields()


def test_release_fields_cannot_be_changed_through_a_returned_copy():
    worker.release_fields()["last_heartbeat"] = 1
    assert "last_heartbeat" not in worker.release_fields()


def test_beat_is_owed_exactly_at_the_interval():
    assert not worker.beat_owed(4.99, 5)
    assert worker.beat_owed(5, 5)
    assert worker.beat_owed(60, 5)


def test_idle_worker_ticks_at_the_heartbeat_interval_when_it_is_shorter_than_half_the_lease():
    assert worker.idle_tick_interval(lease_secs=30, heartbeat_interval=5) == 5


def test_idle_worker_ticks_at_half_the_lease_when_that_is_shorter_than_the_heartbeat():
    assert worker.idle_tick_interval(lease_secs=6, heartbeat_interval=5) == 3


def test_idle_heartbeat_of_zero_means_no_cap_so_half_the_lease_rules():
    assert worker.idle_tick_interval(lease_secs=30, heartbeat_interval=0) == 15


def test_idle_tick_is_never_faster_than_a_tenth_of_a_second():
    assert worker.idle_tick_interval(lease_secs=0.1, heartbeat_interval=5) == 0.1


def test_async_worker_beats_at_most_once_per_poll_cycle():
    assert worker.async_beat_interval(heartbeat_interval=5, poll_interval=1) == pytest.approx(0.9)


def test_async_worker_beats_at_the_configured_interval_when_polls_are_slow():
    assert worker.async_beat_interval(heartbeat_interval=5, poll_interval=60) == 5


# --- #35 guard: the real release, against a real database -------------------------------


@pytest.mark.django_db
def test_releasing_a_worker_issues_one_statement_and_never_writes_last_heartbeat():
    from datetime import timedelta

    from sqlery.django_sqlery.models import QueuedJob, Worker

    stale = timezone.now() - timedelta(hours=8)
    row = Worker.objects.create(node_id="n", pid=4242, status="busy", queues=["default"])
    Worker.objects.filter(id=row.id).update(last_heartbeat=stale, current_job_id=77)
    job = QueuedJob(id=77, worker_id=row.id)

    with CaptureQueriesContext(connection) as ctx:
        job._release_worker()

    assert len(ctx.captured_queries) == worker.RELEASE_STATEMENTS == 1
    assert "last_heartbeat" not in ctx.captured_queries[0]["sql"]
    after = Worker.objects.get(id=row.id)
    assert (after.status, after.current_job_id) == ("idle", None)
    assert after.last_heartbeat == stale  # a silent worker keeps its stale beat
