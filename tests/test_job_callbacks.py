"""on_success / on_failure callbacks must fire on every executor, not just Django's."""

import pytest

from sqlery.core.worker import JobExecutor

CALLS = []


def ok_task(x):
    return x * 2


def bad_task():
    raise ValueError("nope")


def on_ok(job, connection, result):
    CALLS.append(("ok", job.id, result))


def on_bad(job, connection, result):
    CALLS.append(("bad", job.id, result))


def exploding_callback(job, connection, result):
    raise RuntimeError("callback bug")


@pytest.fixture(autouse=True)
def _clear():
    CALLS.clear()


@pytest.fixture
def backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'cb.db'}")
    return SQLAlchemyBackend()


def _claimed(backend, task, kwargs=None, **kw):
    base = dict(task_path=f"tests.test_job_callbacks.{task}", kwargs=kwargs or {},
                queue_name="default", priority=0, scheduled_at=None, max_retries=0,
                retry_backoff=1.0, allow_parallel=False, timeout_seconds=None)
    base.update(kw)
    return backend.claim_job_by_id(backend.create_job(**base).id, "w")


def test_success_callback_runs_with_result(backend):
    j = _claimed(backend, "ok_task", {"x": 21}, on_success_path="tests.test_job_callbacks.on_ok",
                 job_name="named-job")
    JobExecutor(backend=backend).execute_job(j)
    assert CALLS == [("ok", "named-job", "42")] or CALLS == [("ok", "named-job", 42)]


def test_failure_callback_runs_and_job_id_falls_back_to_pk(backend):
    j = _claimed(backend, "bad_task", on_failure_path="tests.test_job_callbacks.on_bad")
    JobExecutor(backend=backend).execute_job(j)
    assert CALLS == [("bad", str(j.id), None)]


def test_no_callback_configured_is_a_noop(backend):
    j = _claimed(backend, "ok_task", {"x": 1})
    JobExecutor(backend=backend).execute_job(j)
    assert CALLS == [] and backend.get_job_by_id(j.id).status == "success"


def test_a_raising_callback_never_changes_the_job_outcome(backend):
    j = _claimed(backend, "ok_task", {"x": 1},
                 on_success_path="tests.test_job_callbacks.exploding_callback")
    JobExecutor(backend=backend).execute_job(j)
    assert backend.get_job_by_id(j.id).status == "success"
    j2 = _claimed(backend, "bad_task",
                  on_failure_path="tests.test_job_callbacks.exploding_callback")
    JobExecutor(backend=backend).execute_job(j2)
    assert backend.get_job_by_id(j2.id).status == "failed"


def test_callbacks_survive_into_the_retry_job_and_fire_there(backend):
    j = _claimed(backend, "bad_task", max_retries=1, retry_backoff=0,
                 on_failure_path="tests.test_job_callbacks.on_bad")
    JobExecutor(backend=backend).execute_job(j)
    retry = backend.get_latest_retry(j.id)
    assert retry.on_failure_path == "tests.test_job_callbacks.on_bad"
    assert len(CALLS) == 1                       # fired for the failed first attempt
    JobExecutor(backend=backend).execute_job(backend.claim_job_by_id(retry.id, "w"))
    assert len(CALLS) == 2                       # and for the failed retry


@pytest.mark.django_db
def test_django_executor_still_fires_callbacks():
    from sqlery.executor import TaskExecutor
    from sqlery.models import QueuedJob

    j = QueuedJob.objects.create(task_path="tests.test_job_callbacks.ok_task", queue_name="default",
                                 status="queued", kwargs={"x": 4},
                                 on_success_path="tests.test_job_callbacks.on_ok")
    TaskExecutor().execute_job(j)
    assert len(CALLS) == 1 and CALLS[0][0] == "ok" and CALLS[0][2] == 8


def file_cb(job, connection, result):
    import os

    with open(os.environ["SQLERY_CB_FILE"], "a") as f:
        f.write(f"{job.id}:{result}\n")


@pytest.mark.parametrize("task,path_kwarg,expected_result", [
    ("ok_task", "on_success_path", "10"),
    ("bad_task", "on_failure_path", "None"),
])
def test_callbacks_fire_in_the_forked_child_of_a_real_worker(
        backend, tmp_path, monkeypatch, task, path_kwarg, expected_result):
    """WorkerProcess runs jobs in a forked child (execute_job_in_child), a separate code path."""
    from sqlery.core.worker import WorkerProcess

    out = tmp_path / "cb.txt"
    monkeypatch.setenv("SQLERY_CB_FILE", str(out))
    j = _claimed(backend, task, {"x": 5} if task == "ok_task" else {},
                 job_name="forked", **{path_kwarg: "tests.test_job_callbacks.file_cb"})
    WorkerProcess(queues=["default"], backend=backend)._fork_and_execute(j)
    assert out.read_text().strip() == f"forked:{expected_result}"
