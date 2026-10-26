"""Regression #113: SubprocessTriggerMiddleware spawned run_jobs children without bound.

End-to-end: the real middleware starts real child processes (a stand-in manage.py that
sleeps, as a CPU-capped container makes the real run_jobs do), and a real ``run_jobs``
chain (SQLite in a tmp dir) hands its slot to its successor. ``MAX_SUBPROCESS_WORKERS``
bounds the children alive at once; the slot is a kernel flock, so it is freed when a
child is SIGKILLed.
"""

import fcntl
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from sqlery.django_sqlery.subprocess_middleware import SubprocessTriggerMiddleware

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/stat").exists(), reason="counts children via /proc"
)


def _children(marker: str) -> list[int]:
    """Live (non-zombie) children of this process whose command line contains marker."""
    me, pids = os.getpid(), []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if (
                int(stat[1]) == me
                and stat[0] != "Z"
                and marker in Path(f"/proc/{pid}/cmdline").read_text()
            ):
                pids.append(int(pid))
        except OSError:
            pass
    return pids


@pytest.fixture
def sleeper_project(tmp_path, settings):
    """A project whose manage.py sleeps; every child is killed on teardown."""
    (tmp_path / "manage.py").write_text("import time\ntime.sleep(10)\n")
    marker = str(tmp_path / "manage.py")

    def configure(**jobs_settings):
        settings.BASE_DIR = tmp_path
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "CHECK_INTERVAL_SECONDS": 1,
            "SUBPROCESS_TRIGGER_LOG": "",
            "SUBPROCESS_SLOT_DIR": str(tmp_path / "slots"),
            **jobs_settings,
        }

    cache.clear()
    yield configure, marker
    for pid in _children(marker):
        os.kill(pid, signal.SIGKILL)
    deadline = time.time() + 5
    while _children(marker) and time.time() < deadline:
        time.sleep(0.05)


def _drive(marker, requests, pause=0.1):
    """One request per pause (throttle bypassed); returns the peak live children."""
    middleware = SubprocessTriggerMiddleware(lambda request: None)
    peak = 0
    for _ in range(requests):
        cache.clear()
        middleware(RequestFactory().get("/"))
        peak = max(peak, len(_children(marker)))
        time.sleep(pause)
    return peak


def test_children_alive_are_bounded(sleeper_project):
    """#113: the issue's repro: requests keep coming, live children stay <= the bound."""
    configure, marker = sleeper_project
    configure(MAX_SUBPROCESS_WORKERS=2)
    peak = _drive(marker, 20)
    assert 1 <= peak <= 2


def test_zero_means_unbounded(sleeper_project):
    """#113: MAX_SUBPROCESS_WORKERS=0 keeps the old unbounded behaviour."""
    configure, marker = sleeper_project
    configure(MAX_SUBPROCESS_WORKERS=0)
    assert _drive(marker, 6) >= 5


def test_slot_freed_when_child_is_sigkilled(sleeper_project):
    """#113: an OOM-killed child must not leak its slot."""
    configure, marker = sleeper_project
    configure(MAX_SUBPROCESS_WORKERS=1)
    assert _drive(marker, 3) == 1
    (pid,) = _children(marker)
    os.kill(pid, signal.SIGKILL)
    deadline = time.time() + 5
    while _children(marker) and time.time() < deadline:
        time.sleep(0.05)
    assert _drive(marker, 1, pause=0.3) == 1  # a new child took the freed slot


MANAGE_PY = """\
import os, sys
if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chainproject.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""


def test_run_jobs_chain_hands_slot_to_successor(tmp_path, settings, monkeypatch):
    """#113: a real run_jobs chain keeps one slot, drains the queue, then frees it."""
    project = tmp_path / "proj"
    (project / "chainproject").mkdir(parents=True)
    slots = tmp_path / "slots"
    (project / "chainproject" / "__init__.py").write_text("")
    (project / "chainproject" / "settings.py").write_text(textwrap.dedent(f"""\
            from pathlib import Path
            BASE_DIR = Path({str(project)!r})
            SECRET_KEY = "x"
            INSTALLED_APPS = ["sqlery.django_sqlery"]
            DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
            DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3",
                                      "NAME": str(BASE_DIR / "db.sqlite3")}}}}
            DJANGO_SQL_JOBS = {{"MAX_SUBPROCESS_WORKERS": 1, "SUBPROCESS_SLOT_DIR": {str(slots)!r},
                               "TRIGGER_MODE": "subprocess", "SUBPROCESS_TRIGGER_LOG": ""}}
            """))
    (project / "manage.py").write_text(MANAGE_PY)
    (project / "nap.py").write_text("import time\n\ndef nap():\n    time.sleep(1.5)\n")
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "chainproject.settings"}
    env["PYTHONPATH"] = os.pathsep.join([str(project), env.get("PYTHONPATH", "")])
    run = lambda *args: subprocess.run(
        [sys.executable, *args], cwd=project, env=env, check=True, capture_output=True, timeout=120
    )
    run("manage.py", "migrate", "--no-input")
    run(
        "-c",
        "import django; django.setup()\n"
        "from sqlery.compat import get_backend\n"
        "for i in range(3):\n"
        "    get_backend().create_job(task_path='nap.nap', kwargs={}, queue_name='default',\n"
        "        priority=0, scheduled_at=None, max_retries=0, retry_backoff=1.0,\n"
        "        allow_parallel=True, timeout_seconds=None, job_name=f'nap{i}')\n",
    )

    def completed() -> int:
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import django; django.setup()\n"
                    "from sqlery.django_sqlery.models import JobAll\n"
                    "print(JobAll.objects.filter(status='success').count())"
                ),
            ],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return int(out.stdout.strip() or 0)

    def slot_is_held() -> bool:
        fd = os.open(slots / "slot-0.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except OSError:
            return True
        finally:
            os.close(fd)

    # The middleware's children inherit this process's environment.
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "chainproject.settings")
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    settings.BASE_DIR = project
    settings.DJANGO_SQL_JOBS = {
        "MAX_SUBPROCESS_WORKERS": 1,
        "SUBPROCESS_SLOT_DIR": str(slots),
        "TRIGGER_MODE": "subprocess",
        "SUBPROCESS_TRIGGER_LOG": "",
    }
    cache.clear()
    marker = str(project / "manage.py")
    seen: set[int] = set()
    try:
        SubprocessTriggerMiddleware(lambda request: None)(RequestFactory().get("/"))
        assert slot_is_held()
        done, deadline = 0, time.time() + 90
        while done < 3 and time.time() < deadline:
            # Sampled before the count: a job still incomplete afterwards was incomplete
            # then, so the slot must have been held (hand-offs included).
            held = slot_is_held()
            # The bound is reached: further requests must not start another chain.
            cache.clear()
            SubprocessTriggerMiddleware(lambda request: None)(RequestFactory().get("/"))
            time.sleep(0.3)
            seen.update(_children(marker))
            done = completed()
            assert held or done == 3
        assert done == 3
        deadline = time.time() + 30
        while slot_is_held() and time.time() < deadline:
            time.sleep(0.2)
        assert not slot_is_held()
    finally:
        # Chain successors run in their own session, so they are not our children: they
        # are killed by process group, recorded while they were still direct children.
        for pid in seen | set(_children(marker)):
            try:
                pgid = os.getpgid(pid)
                if pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
