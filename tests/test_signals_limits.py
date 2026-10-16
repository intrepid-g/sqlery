"""Task signals and soft time limit, in standalone and Django executors."""

import time

import pytest

from sqlery.core.execution import SoftTimeLimitExceeded
from sqlery.core.job import job
from sqlery.core.signals import (
    Signal,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
)
from sqlery.core.worker import JobExecutor


@job
def ok_task(x):
    return x + 1


@job(max_retries=1)
def failing_task():
    raise ValueError("nope")


@job(soft_time_limit=1, timeout_seconds=3)
def slow_cleanup():
    try:
        time.sleep(10)
    except SoftTimeLimitExceeded:
        return "cleaned-up"


@job(soft_time_limit=1, timeout_seconds=3)
def slow_swallowing():
    try:
        time.sleep(10)
    except SoftTimeLimitExceeded:
        time.sleep(10)  # ignores the soft limit; the hard limit must still stop it


@pytest.fixture
def backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 's.db'}")
    return SQLAlchemyBackend()


@pytest.fixture
def events():
    seen = []

    def make(name):
        def rec(sender, **kw):
            seen.append((name, sender, kw))
        return rec

    recs = {n: make(n) for n in ("pre", "post", "ok", "fail", "retry")}
    task_prerun.connect(recs["pre"]); task_postrun.connect(recs["post"])
    task_success.connect(recs["ok"]); task_failure.connect(recs["fail"])
    task_retry.connect(recs["retry"])
    yield seen
    for sig, n in ((task_prerun, "pre"), (task_postrun, "post"), (task_success, "ok"),
                   (task_failure, "fail"), (task_retry, "retry")):
        sig.disconnect(recs[n])


def _claim(backend, func, kwargs=None, **kw):
    base = dict(task_path=func.task_path, kwargs=kwargs or {}, queue_name="default", priority=0,
                scheduled_at=None, max_retries=0, retry_backoff=1.0, allow_parallel=False,
                timeout_seconds=func.timeout_seconds)
    base.update(kw)
    return backend.claim_job_by_id(backend.create_job(**base).id, "w")


def test_signal_dispatch_filters_by_sender_and_isolates_errors():
    sig, got = Signal("t"), []
    sig.connect(lambda sender, **kw: got.append(("all", sender)))
    sig.connect(lambda sender, **kw: got.append(("only-a", sender)), sender="a")

    @sig.connect
    def boom(sender, **kw):
        raise RuntimeError("receiver bug")

    assert sig.send("a") == 3 and sig.send("b") == 2  # boom is logged, not raised
    assert ("only-a", "b") not in got and ("only-a", "a") in got


def test_success_signals_in_order(backend, events):
    j = _claim(backend, ok_task, {"x": 1})
    JobExecutor(backend=backend).execute_job(j)
    names = [e[0] for e in events if e[1] == ok_task.task_path]
    assert names == ["pre", "ok", "post"]
    post = next(e for e in events if e[0] == "post")[2]
    assert post["state"] == "SUCCESS" and post["retval"] == 2


def test_failure_and_retry_signals(backend, events):
    j = _claim(backend, failing_task, max_retries=1)
    JobExecutor(backend=backend).execute_job(j)
    names = [e[0] for e in events if e[1] == failing_task.task_path]
    assert names == ["pre", "fail", "post", "retry"]
    fail = next(e for e in events if e[0] == "fail")[2]
    assert isinstance(fail["exc"], ValueError) and "ValueError" in fail["traceback"]
    retry = next(e for e in events if e[0] == "retry")[2]
    assert retry["retry_job"].parent_job_id == j.id


def test_soft_time_limit_lets_task_clean_up(backend):
    j = _claim(backend, slow_cleanup)
    start = time.monotonic()
    JobExecutor(backend=backend).execute_job(j)
    assert time.monotonic() - start < 2.5
    done = backend.get_job_by_id(j.id)
    assert done.status == "success" and done.output == "cleaned-up"


def test_hard_limit_still_fires_after_ignored_soft_limit(backend):
    j = _claim(backend, slow_swallowing)
    start = time.monotonic()
    JobExecutor(backend=backend).execute_job(j)
    elapsed = time.monotonic() - start
    assert 2.5 < elapsed < 5  # soft at 1s, hard at 3s total (not 1s + 3s)
    done = backend.get_job_by_id(j.id)
    assert done.status == "failed" and "timeout" in done.error.lower()


@pytest.mark.django_db
def test_django_executor_emits_signals():
    from sqlery.executor import TaskExecutor
    from sqlery.models import QueuedJob

    seen = []
    rec = lambda sender, **kw: seen.append(sender)  # noqa: E731
    task_success.connect(rec)
    try:
        j = QueuedJob.objects.create(task_path="tests.test_signals_limits.ok_task",
                                     queue_name="default", status="queued", kwargs={"x": 4})
        TaskExecutor().execute_job(j)
    finally:
        task_success.disconnect(rec)
    j.refresh_from_db()
    assert j.status == "success" and j.output == "5"
    assert seen == ["tests.test_signals_limits.ok_task"]
