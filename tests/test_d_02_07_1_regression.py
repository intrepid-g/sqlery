"""Regression tests for D-02-07-1 (duplicate CreateModel('DaemonLease')).

Root cause: migration 0022's filename promised a DeleteModel('DaemonLease')
but its operations list contained only AlterFields. The follow-up migration
0023_restore_daemonlease then issued an unconditional CreateModel that
collided with the still-existing table from 0020_daemon_lease.

Fix: 0023 reduced to operations=[] (graph-node preservation only).

These tests lock the bug closed. If a future change reintroduces a
duplicate CreateModel('DaemonLease') anywhere in the migration chain,
or if pytest-django's `setup_databases` again hits "already exists",
both tests will fail.

See: .planning/phases/03-testing-ci/03-01-PLAN.md
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.django_db
def test_scheduled_task_creation_does_not_trip_daemon_lease():
    """Canonical D-02-07-1 reproducer.

    Equivalent to:
        pytest tests/test_models.py::TestScheduledTask::test_scheduled_task_creation -x

    Before the fix, pytest-django's `setup_databases` raised OperationalError
    ("sqlery_daemon_lease already exists") and this test could not even start.
    """
    from sqlery.django_sqlery.models import ScheduledTask

    task = ScheduledTask.objects.create(
        name="d_02_07_1_probe",
        task_path="tests.test_d_02_07_1_regression.noop_task",
        schedule_type="interval",
        interval=60,
        interval_unit="seconds",
    )
    assert task.pk is not None
    assert ScheduledTask.objects.filter(name="d_02_07_1_probe").exists()


def test_setup_databases_from_clean_sqlite(tmp_path):
    """Spawn a fresh interpreter that runs Django's `setup_databases` against
    an on-disk SQLite file in tmp_path.

    Asserts:
        - exit code 0
        - stderr contains no "already exists" substring
        - stderr contains no "IntegrityError" or "OperationalError" substring
    """
    db_path = tmp_path / "regression.sqlite3"

    script = textwrap.dedent(
        f"""
        import os, sys
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"

        import django
        from django.conf import settings
        # Override DB to use a real on-disk SQLite so multi-connection
        # behaviour mirrors pytest-django's runner.
        settings.DATABASES["default"] = {{
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": {str(db_path)!r},
        }}
        django.setup()

        from django.test.utils import setup_databases, teardown_databases
        cfg = setup_databases(verbosity=0, interactive=False)
        teardown_databases(cfg, verbosity=0)
        print("OK")
        """
    )

    env = os.environ.copy()
    # Ensure the repo root is importable so `tests.settings` resolves.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    assert result.returncode == 0, (
        f"setup_databases failed (exit={result.returncode}).\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "already exists" not in combined.lower(), (
        f"D-02-07-1 regression: 'already exists' found in output.\n{combined}"
    )
    assert "IntegrityError" not in combined, combined
    assert "OperationalError" not in combined, combined


def noop_task():
    """Module-level callable referenced by the ScheduledTask probe above."""
    return None
