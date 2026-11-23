"""Precedents for the QueueLock party: who holds a queue's non-parallel lock, and for how long."""

from datetime import UTC, datetime, timedelta

from sqlery.contract.queue_lock import Holder, holder_is_live, must_wait

NOW = datetime(2026, 11, 22, 13, 5, tzinfo=UTC)
ALIVE = 30  # WORKER_ALIVE_TIMEOUT; the stale threshold is 90 s


def ago(seconds):
    return NOW - timedelta(seconds=seconds)


def worker_holder(beat_age, status="busy"):
    return Holder(status, ago(beat_age), ago(8 * 3600), 600)


def unregistered_holder(claim_age, timeout=600):
    return Holder(None, None, ago(claim_age), timeout)


def test_a_worker_that_beat_recently_holds_the_lock():
    assert holder_is_live(worker_holder(5), NOW, ALIVE)


def test_a_slow_job_whose_worker_still_beats_keeps_the_lock_past_its_timeout():
    assert holder_is_live(worker_holder(60), NOW, ALIVE)


def test_a_worker_silent_for_8_hours_no_longer_holds_the_lock():
    assert not holder_is_live(worker_holder(8 * 3600), NOW, ALIVE)


def test_a_worker_marked_dead_no_longer_holds_the_lock_even_with_a_fresh_beat():
    assert not holder_is_live(worker_holder(1, status="dead"), NOW, ALIVE)


def test_an_unregistered_holder_keeps_the_lock_until_timeout_plus_kill_grace():
    assert holder_is_live(unregistered_holder(600 + 59), NOW, ALIVE)
    assert not holder_is_live(unregistered_holder(600 + 61), NOW, ALIVE)


def test_a_parallel_job_never_waits():
    assert not must_wait(True, [worker_holder(1)], NOW, ALIVE)


def test_a_non_parallel_job_waits_for_a_live_holder_only():
    assert must_wait(False, [worker_holder(8 * 3600), worker_holder(1)], NOW, ALIVE)
    assert not must_wait(False, [worker_holder(8 * 3600)], NOW, ALIVE)
    assert not must_wait(False, [], NOW, ALIVE)
