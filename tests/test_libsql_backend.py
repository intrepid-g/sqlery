"""EXPERIMENTAL libsql backend: a real enqueue and a real worker run on a libsql file, in a fresh Django project."""
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("libsql")

SETTINGS = """
SECRET_KEY = "x"
BASE_DIR = {base!r}
INSTALLED_APPS = ["sqlery.django_sqlery", "app"]
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATABASES = {{"default": {{"ENGINE": "sqlery.django_sqlery.libsql_backend", "NAME": {db!r}}}}}
DJANGO_SQL_JOBS = {{"WORKER_QUEUES": ["default"], "TRIGGER_MODE": "disabled"}}
"""

TASKS = """
from sqlery import job

@job(queue="default")
def add(x, y):
    return x + y
"""

CHECK = """
import django
django.setup()
import subprocess, sys
from app.tasks import add

res = add.apply_async(args=(20, 22))
subprocess.run([sys.executable, "-m", "django", "run_jobs", "--worker-only", "--max-jobs", "1"], check=True)
print("RESULT", res.state, res.get(timeout=10))
"""


def run(args, tmp_path, **kw):
    env = {"DJANGO_SETTINGS_MODULE": "proj_settings", "PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, **kw)


def test_enqueue_and_worker_run_on_libsql_file(tmp_path):
    (tmp_path / "proj_settings.py").write_text(SETTINGS.format(base=str(tmp_path), db=str(tmp_path / "q.db")))
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "tasks.py").write_text(textwrap.dedent(TASKS))
    migrate = run(["-m", "django", "migrate", "-v", "0"], tmp_path)
    assert migrate.returncode == 0, migrate.stderr
    (tmp_path / "check.py").write_text(CHECK)
    done = run(["check.py"], tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "RESULT SUCCESS 42" in done.stdout, done.stdout
