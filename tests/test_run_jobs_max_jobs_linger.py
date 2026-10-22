"""Regression #118: run_jobs --max-jobs / --idle-linger and the SUBPROCESS_* settings."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory

pytestmark = pytest.mark.django_db(transaction=True)


def _make_jobs(n):
    from sqlery.compat import get_backend

    for i in range(n):
        get_backend().create_job(
            task_path="builtins.abs",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1.0,
            allow_parallel=True,
            timeout_seconds=None,
            job_name=f"j{i}",
        )


def _executor():
    from sqlery.core.worker import TaskExecutor

    return TaskExecutor()


def test_default_processes_one_job_and_spawns_next():
    _make_jobs(3)
    ex = _executor()
    with patch.object(ex, "_spawn_next_worker") as spawn:
        done = ex.run_queue_workers()
    assert len(done) == 1
    spawn.assert_called_once()


def test_max_jobs_processes_several_then_spawns_if_more():
    _make_jobs(3)
    ex = _executor()
    with patch.object(ex, "_spawn_next_worker") as spawn:
        done = ex.run_queue_workers(max_jobs=2)
    assert len(done) == 2
    spawn.assert_called_once()


def test_max_jobs_drains_queue_without_spawning():
    _make_jobs(2)
    ex = _executor()
    with patch.object(ex, "_spawn_next_worker") as spawn:
        done = ex.run_queue_workers(max_jobs=5)
    assert len(done) == 2
    spawn.assert_not_called()


def test_linger_picks_up_a_job_enqueued_while_idle():
    ex = _executor()
    t = threading.Timer(0.6, _make_jobs, args=(1,))
    t.start()
    t0 = time.monotonic()
    with patch.object(ex, "_spawn_next_worker"):
        done = ex.run_queue_workers(max_jobs=2, idle_linger=5)
    t.join()
    # Queue empty at start: it lingered, took the late job, then ran out its cap/deadline.
    assert len(done) == 1
    assert time.monotonic() - t0 >= 0.5


def test_no_linger_exits_immediately_on_empty_queue():
    ex = _executor()
    t0 = time.monotonic()
    assert ex.run_queue_workers(max_jobs=3) == []
    assert time.monotonic() - t0 < 1


def test_linger_gives_up_after_deadline():
    ex = _executor()
    t0 = time.monotonic()
    assert ex.run_queue_workers(max_jobs=3, idle_linger=1) == []
    assert 0.9 <= time.monotonic() - t0 < 4


def test_command_flags_reach_executor():
    with patch("sqlery.django_sqlery.management.commands.run_jobs.TaskExecutor") as te:
        te.return_value.run_queue_workers.return_value = []
        call_command("run_jobs", "--worker-only")
        te.return_value.run_queue_workers.assert_called_with(queue_name=None)
        call_command("run_jobs", "--worker-only", "--max-jobs", "4", "--idle-linger", "2.5")
        te.return_value.run_queue_workers.assert_called_with(
            queue_name=None, max_jobs=4, idle_linger=2.5
        )


def test_extra_args_empty_by_default(settings):
    from sqlery.django_sqlery.subprocess_executor import run_jobs_extra_args

    settings.DJANGO_SQL_JOBS = {}
    assert run_jobs_extra_args() == []
    settings.DJANGO_SQL_JOBS = {"SUBPROCESS_JOBS_PER_PROCESS": 5, "SUBPROCESS_IDLE_LINGER_S": 3}
    assert run_jobs_extra_args() == ["--max-jobs", "5", "--idle-linger", "3.0"]


@pytest.mark.parametrize(
    "conf,extra",
    [
        ({}, []),
        ({"SUBPROCESS_JOBS_PER_PROCESS": 4}, ["--max-jobs", "4"]),
        ({"SUBPROCESS_JOBS_PER_PROCESS": 4, "SUBPROCESS_IDLE_LINGER_S": 2},
         ["--max-jobs", "4", "--idle-linger", "2.0"]),
    ],
)
def test_middleware_passes_settings_through(settings, conf, extra):
    from sqlery.django_sqlery.subprocess_middleware import SubprocessTriggerMiddleware

    cache.clear()
    settings.DJANGO_SQL_JOBS = {
        "TRIGGER_MODE": "subprocess",
        "ENABLE_MIDDLEWARE_TRIGGER": True,
        "CHECK_INTERVAL_SECONDS": 60,
        **conf,
    }
    mw = SubprocessTriggerMiddleware(MagicMock(return_value=MagicMock()))
    with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as popen, patch(
        "sqlery.django_sqlery.subprocess_middleware.get_manage_py_path", return_value="/m.py"
    ):
        mw(RequestFactory().get("/"))
    cmd = popen.call_args[0][0]
    assert cmd[1:] == ["/m.py", "run_jobs", *extra]
