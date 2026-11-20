"""#182: two CLI behaviours that were reported as broken.

- A foreground daemon (``manage.py daemon start``) never wrote its PID file, so
  ``daemon status`` reported "not running" while it ran.
- ``rqworker --burst`` ran one job per queue and then spawned a detached worker
  instead of draining the queue itself.
"""

import os
import subprocess
import sys
import textwrap

import pytest
from django.core.management import call_command

from sqlery.django_sqlery.models import JobAll, QueuedJob


def fast_task():
    return "ok"


def _cli(tmp_path, *args, **env):
    full_env = {k: v for k, v in os.environ.items() if k not in ("DJANGO_SETTINGS_MODULE",)}
    full_env.pop("SQLERY_DATABASE_URL", None)
    full_env.pop("DATABASE_URL", None)
    full_env.update(SQLERY_FORCE_STANDALONE="1", SQLERY_DASHBOARD_API_KEY="k", **env)
    return subprocess.run(
        [sys.executable, "-m", "sqlery.core.cli", *args],
        cwd=tmp_path,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_foreground_daemon_records_its_pid_so_status_sees_it(tmp_path):
    url = f"sqlite:///{tmp_path / 'daemon.db'}"
    script = textwrap.dedent(f"""
        import os
        from pathlib import Path
        from sqlery.compat import initialize
        initialize(database_url={url!r}, enable_daemon=False)
        from sqlery.core.daemon import DaemonManager
        seen = []
        def probe(self, backend):
            seen.append((self.read_pid() == os.getpid(), self.status()["running"]))
        DaemonManager._cleanup_stale_workers_all_nodes = probe
        DaemonManager(pid_dir=Path({str(tmp_path)!r}))._run_daemon(max_workers=1, once=True)
        print("seen", seen)
    """)
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env.update(SQLERY_FORCE_STANDALONE="1", LOG_DIR=str(tmp_path))
    done = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "seen [(True, True)]" in done.stdout, done.stdout


@pytest.mark.django_db
def test_rqworker_burst_drains_the_queue_without_spawning_workers(monkeypatch):
    from sqlery.core.worker import TaskExecutor

    spawned = []
    monkeypatch.setattr(TaskExecutor, "_spawn_next_worker", lambda self, q=None: spawned.append(q))
    for _ in range(3):
        QueuedJob.objects.create(task_path="tests.test_cli_daemon_burst_182.fast_task", queue_name="burst-q")

    call_command("rqworker", "--burst", "burst-q")

    assert JobAll.objects.filter(status="success").count() == 3
    assert QueuedJob.objects.filter(status="queued").count() == 0
    assert spawned == []
