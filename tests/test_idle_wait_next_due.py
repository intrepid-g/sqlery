"""Regression for #115: an idle worker wakes for a job that falls due before the poll interval."""

from datetime import UTC, datetime, timedelta

import pytest

from sqlery.core.job import job
from sqlery.core.worker import WorkerProcess


@job
def noop():
    return "done"


@pytest.fixture
def backend(tmp_path):
    from sqlery import compat
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'due.db'}")
    b = SQLAlchemyBackend()
    compat._backend = b
    yield b
    compat._backend = None


def _worker(backend, poll=30):
    w = WorkerProcess(queues=["default"], backend=backend)
    w.poll_interval = poll
    return w


def test_empty_queue_keeps_poll_interval(backend):
    assert backend.next_due_delay(["default"]) is None
    assert _worker(backend)._idle_timeout() == 30


def test_job_due_now_does_not_shorten_the_wait(backend):
    noop.enqueue()
    assert backend.next_due_delay(["default"]) is None


def test_retry_backoff_shortens_the_wait(backend):
    noop.enqueue_at(datetime.now(UTC) + timedelta(seconds=2))
    delay = backend.next_due_delay(["default"])
    assert delay is not None and 0 < delay <= 2
    assert _worker(backend)._idle_timeout() <= 2.1


def test_far_future_job_is_capped_by_poll_interval(backend):
    noop.enqueue_at(datetime.now(UTC) + timedelta(hours=1))
    assert _worker(backend, poll=5)._idle_timeout() == 5


def test_backend_error_falls_back_to_poll_interval(backend, monkeypatch):
    def boom(queues):
        raise RuntimeError("db down")

    monkeypatch.setattr(backend, "next_due_delay", boom)
    assert _worker(backend)._idle_timeout() == 30
