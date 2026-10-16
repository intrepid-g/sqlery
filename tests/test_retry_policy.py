"""autoretry_for / Retry / bind=True / retry_backoff_max, in standalone and Django executors."""

from datetime import datetime, timezone

import pytest

from sqlery.core.job import job
from sqlery.core.retry import Retry, TaskContext, get_retry_policy
from sqlery.core.worker import JobExecutor, compute_retry_delay


# ---- tasks (module-level so import_task("tests.test_retry_policy.X") resolves) ----------
@job(max_retries=2, autoretry_for=(ValueError,))
def only_value_errors(kind):
    raise {"value": ValueError, "key": KeyError}[kind]("boom")


@job(bind=True, max_retries=3)
def bound_retrying(self, x):
    if self.request.retries == 0:
        self.retry(exc=RuntimeError("flaky"), countdown=7)
    return x * 2


@job(bind=True)
def bound_echo(self, x):
    return (self.request.retries, x)


@job(retry_backoff_max=3, retry_jitter=False)
def capped():
    raise RuntimeError("x")


# ---- standalone ------------------------------------------------------------------------
@pytest.fixture
def backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'p.db'}")
    return SQLAlchemyBackend()


def _enqueue(backend, func, kwargs, **kw):
    base = dict(
        task_path=func.task_path, kwargs=kwargs, queue_name="default", priority=0,
        scheduled_at=None, max_retries=func.max_retries or 0, retry_backoff=1.0,
        allow_parallel=False, timeout_seconds=None,
    )
    base.update(kw)
    j = backend.create_job(**base)
    return backend.claim_job_by_id(j.id, "w")


def _run(backend, j):
    JobExecutor(backend=backend).execute_job(j)
    return backend.get_latest_retry(j.id)


def test_policy_is_read_from_decorator():
    p = get_retry_policy(only_value_errors)
    assert p.autoretry_for == (ValueError,)
    assert get_retry_policy(capped).backoff_max == 3
    assert get_retry_policy(lambda: 0).autoretry_for is None


def test_autoretry_for_filters_exceptions(backend):
    assert _run(backend, _enqueue(backend, only_value_errors, {"kind": "value"})) is not None
    # KeyError is not in autoretry_for: fails permanently, no retry job.
    assert _run(backend, _enqueue(backend, only_value_errors, {"kind": "key"})) is None


def test_self_retry_uses_countdown_and_carries_job_spec(backend):
    j = _enqueue(backend, bound_retrying, {"x": 4}, tags=["a"], meta={"k": 1},
                 on_failure_path="tests.test_retry_policy.bound_echo")
    before = datetime.now(timezone.utc)
    child = _run(backend, j)
    assert child is not None and child.retry_count == 1
    sched = child.scheduled_at if child.scheduled_at.tzinfo else child.scheduled_at.replace(tzinfo=timezone.utc)
    assert 6 <= (sched - before).total_seconds() <= 9  # countdown=7, not exponential backoff
    assert child.tags == ["a"] and child.meta == {"k": 1}
    assert child.on_failure_path == "tests.test_retry_policy.bound_echo"
    assert "flaky" in backend.get_job_by_id(j.id).error


def test_bound_task_receives_context_and_second_attempt_succeeds(backend):
    j = _enqueue(backend, bound_echo, {"x": 5})
    JobExecutor(backend=backend).execute_job(j)
    assert backend.get_job_by_id(j.id).output == "[0, 5]"


def test_retry_respects_max_retries(backend):
    j = _enqueue(backend, bound_retrying, {"x": 1}, max_retries=0)
    assert _run(backend, j) is None  # Retry requested but no attempts allowed -> fails


def test_backoff_max_caps_delay(backend):
    j = _enqueue(backend, capped, {}, max_retries=5, retry_backoff=2.0)
    before = datetime.now(timezone.utc)
    # retry_count 0 -> 2s, under cap; force a high count to see the cap
    j2 = backend.create_job(
        task_path=capped.task_path, kwargs={}, queue_name="default", priority=0,
        scheduled_at=None, max_retries=9, retry_backoff=2.0, allow_parallel=False,
        timeout_seconds=None, retry_count=5,
    )
    j2 = backend.claim_job_by_id(j2.id, "w")
    child = _run(backend, j2)
    sched = child.scheduled_at if child.scheduled_at.tzinfo else child.scheduled_at.replace(tzinfo=timezone.utc)
    assert (sched - before).total_seconds() <= 4  # 2*2**5 = 64 capped to 3
    assert compute_retry_delay(2.0, 5) == 64.0  # helper itself unchanged without per-task cap


def test_direct_call_of_bound_task_gets_detached_context():
    assert bound_echo(3) == (0, 3)
    assert isinstance(TaskContext(), TaskContext)
    with pytest.raises(Retry):
        TaskContext().retry(countdown=1)


# ---- Django ----------------------------------------------------------------------------
@pytest.mark.django_db
def test_django_executor_autoretry_and_countdown():
    from sqlery.django_sqlery.decorators import job as djob
    from sqlery.executor import TaskExecutor
    from sqlery.models import QueuedJob

    @djob(max_retries=2, autoretry_for=(ValueError,), bind=True)
    def dj(self, kind):
        if kind == "retry":
            self.retry(countdown=11)
        raise {"value": ValueError, "key": KeyError}[kind]("x")

    # Make the function importable by path for the executor.
    globals()["dj_task"] = dj
    path = f"{__name__}.dj_task"

    def run(kind):
        j = QueuedJob.objects.create(
            task_path=path, queue_name="default", status="queued", max_retries=2,
            kwargs={"kind": kind}, tags=["t"],
        )
        TaskExecutor().execute_job(j)
        return QueuedJob.objects.filter(parent_job_id=j.id).first(), j

    child, _ = run("key")
    assert child is None  # KeyError not in autoretry_for
    child, parent = run("value")
    assert child is not None and child.retry_count == 1 and child.tags == ["t"]
    child, parent = run("retry")
    assert child is not None
    assert 9 <= (child.scheduled_at - datetime.now(timezone.utc)).total_seconds() <= 12
