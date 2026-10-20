"""Standalone create_scheduled_task must set next_run_at so the task becomes due (#74)."""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "k")
    from sqlery.compat import _reset_backend, get_backend, initialize

    _reset_backend()
    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    yield get_backend()
    _reset_backend()


def test_a_new_scheduled_task_becomes_due(backend):
    task = backend.create_scheduled_task("t", "x.y", "* * * * *", "default", 0, True)
    assert task.next_run_at is not None, "next_run_at is never set"
    nra = task.next_run_at
    if nra.tzinfo is None:
        nra = nra.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now < nra <= now + timedelta(minutes=1, seconds=1)


def test_explicit_next_run_at_is_honoured(backend):
    when = datetime.now(timezone.utc) + timedelta(hours=3)
    task = backend.create_scheduled_task(
        "t2", "x.y", "* * * * *", "default", 0, True, next_run_at=when
    )
    nra = task.next_run_at
    if nra.tzinfo is None:
        nra = nra.replace(tzinfo=timezone.utc)
    assert nra == when
