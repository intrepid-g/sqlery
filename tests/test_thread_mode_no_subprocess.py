"""Regression #65: thread/synchronous mode must not spawn run_jobs subprocesses."""

import subprocess
from unittest import mock

import pytest


@pytest.mark.django_db(transaction=True)
def test_thread_mode_does_not_spawn_processes():
    from sqlery.compat import get_backend
    from sqlery.triggers import trigger_queue_workers

    for i in range(2):
        get_backend().create_job(
            task_path="builtins.print",
            kwargs={},
            queue_name="default",
            priority=0,
            scheduled_at=None,
            max_retries=0,
            retry_backoff=1,
            allow_parallel=True,
            timeout_seconds=None,
            job_name=f"j{i}",
        )
    with (
        mock.patch("sqlery.triggers.get_execution_strategy", return_value="thread"),
        mock.patch(
            "sqlery.django_sqlery._executor_impl.get_manage_py_path", return_value="manage.py"
        ),
        mock.patch.object(subprocess, "Popen") as popen,
    ):
        trigger_queue_workers()
    assert not popen.called, f"spawned: {popen.call_args.args[0]}"
