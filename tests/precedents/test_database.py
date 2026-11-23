"""Precedents for the Database party (both backends)."""

from datetime import UTC, datetime, timedelta

from sqlery.contract import database

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def test_worker_with_fresh_beat_is_alive():
    assert database.is_beating("busy", NOW - timedelta(seconds=29), NOW, 30)


def test_worker_silent_past_alive_timeout_is_dead():
    assert not database.is_beating("idle", NOW - timedelta(seconds=31), NOW, 30)


def test_worker_silent_exactly_the_alive_timeout_is_dead():
    assert not database.is_beating("idle", NOW - timedelta(seconds=30), NOW, 30)


def test_worker_marked_dead_is_dead_even_with_a_fresh_beat():
    assert not database.is_beating("dead", NOW, NOW, 30)


def test_worker_that_never_beat_is_dead():
    assert not database.is_beating("idle", None, NOW, 30)


def test_a_queued_row_may_be_a_running_job():
    assert database.can_be_running("queued")


def test_a_finished_or_failed_row_is_never_a_running_job():
    assert not database.can_be_running("failed")
    assert not database.can_be_running("success")


def test_zombie_failure_stands_when_exactly_one_attempt_was_closed():
    assert database.closed_the_attempt(1)


def test_zombie_failure_is_void_when_a_completion_closed_the_attempt_first():
    assert not database.closed_the_attempt(0)
