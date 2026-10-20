"""Standalone create_scheduled_task must set next_run_at so the task becomes due (#74)."""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "k")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    return get_backend()


def test_a_new_scheduled_task_becomes_due(backend):
    task = backend.create_scheduled_task("t", "x.y", "* * * * *", "default", 0, True)
    assert task.next_run_at is not None, "next_run_at is never set"
    nra = task.next_run_at
    if nra.tzinfo is None:
        nra = nra.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now < nra <= now + timedelta(minutes=1, seconds=1)
