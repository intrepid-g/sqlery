"""Regression tests for #76: `sqlery worker` could not start in standalone mode."""

import subprocess
import sys

import pytest


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLERY_FORCE_STANDALONE", "1")
    monkeypatch.setenv("SQLERY_DASHBOARD_API_KEY", "k")
    from sqlery.compat import get_backend, initialize

    initialize(database_url=f"sqlite:///{tmp_path}/t.db")
    return get_backend()


def run_python(code, env=None, timeout=60):
    try:
        return subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PATH": "/usr/bin", "SQLERY_FORCE_STANDALONE": "1", **(env or {})},
        )
    except subprocess.TimeoutExpired as exc:  # a worker that starts runs forever
        out = exc.stdout or b""
        err = exc.stderr or b""
        out = out.decode() if isinstance(out, bytes) else out
        err = err.decode() if isinstance(err, bytes) else err
        return subprocess.CompletedProcess(exc.cmd, 0, out, err)


def test_worker_run_accepts_max_jobs(backend):
    from sqlery.core.worker import WorkerProcess

    backend.create_job("os.getcwd", {}, "default", 0, None, 0, 0, True, None)
    processed = WorkerProcess(queues=["default"], backend=backend).run(max_jobs=1)
    assert processed == 1


def test_worker_initialises_the_database_itself(tmp_path):
    # a fresh process, as `sqlery worker` is: nothing calls sqlery.compat.initialize() for it
    out = run_python(
        "from sqlery.core.worker import WorkerProcess; WorkerProcess(queues=['default']).run()",
        env={"SQLERY_DATABASE_URL": f"sqlite:///{tmp_path}/t.db"},
        timeout=10,
    )
    assert "Database not initialized" not in out.stdout + out.stderr, (out.stdout + out.stderr)[
        -300:
    ]
