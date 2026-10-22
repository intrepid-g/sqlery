"""rqworker must hand its CLI queues to the worker subprocess it spawns (#80)."""

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from unittest import mock

from sqlery.core import worker_pool


def test_spawn_worker_passes_pool_queues_in_env():
    from sqlery.core.worker_pool import WORKER_QUEUES_ENV, WorkerPoolManager

    pool = WorkerPoolManager(1, ["reports", "other"], backend=mock.Mock())
    with mock.patch.object(worker_pool.subprocess, "Popen") as popen:
        popen.return_value.pid = 1234
        pool.spawn_worker()
    env = popen.call_args.kwargs["env"]
    assert json.loads(env[WORKER_QUEUES_ENV]) == ["reports", "other"]


def test_rqworker_cli_queue_is_served(tmp_path):
    (tmp_path / "settings.py").write_text(textwrap.dedent(f"""
        SECRET_KEY = "x"
        USE_TZ = True
        INSTALLED_APPS = ["sqlery.django_sqlery"]
        DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": r"{tmp_path}/db.sqlite3"}}}}
        BASE_DIR = r"{tmp_path}"
        DJANGO_SQL_JOBS = {{"ENABLE_DAEMON": True, "DAEMON_CHECK_INTERVAL": 1, "WORKER_POLL_INTERVAL": 1}}
        """))
    (tmp_path / "tasks.py").write_text(
        "from sqlery import job\n\n@job(queue='reports')\ndef hello():\n    return 'hi'\n"
    )
    (tmp_path / "manage.py").write_text(
        "import sys\nfrom django.core.management import execute_from_command_line\n"
        "execute_from_command_line(sys.argv)\n"
    )
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "settings", "PYTHONPATH": str(tmp_path)}
    env.pop("SQLERY_WORKER_QUEUES", None)

    def run(*a, **kw):
        return subprocess.run(
            [sys.executable, "manage.py", *a],
            cwd=tmp_path,
            env=env,
            check=kw.pop("check", False),
            **kw,
        )

    run("migrate", "-v0", check=True)
    run("shell", "-c", "import tasks; tasks.hello.enqueue()", check=True)
    worker = subprocess.Popen(
        [sys.executable, "manage.py", "rqworker", "reports"],
        cwd=tmp_path,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        status = None
        for _ in range(30):
            time.sleep(1)
            out = run(
                "shell",
                "-c",
                "from sqlery.django_sqlery.models import QueuedJob; "
                "print(QueuedJob.objects.get().status)",
                capture_output=True,
                text=True,
            ).stdout.strip()
            status = out.splitlines()[-1] if out else None
            if status == "success":
                break
        assert status == "success", f"job on CLI queue is still {status!r} after 30 s"
    finally:
        os.killpg(worker.pid, signal.SIGTERM)
        worker.wait(timeout=30)
