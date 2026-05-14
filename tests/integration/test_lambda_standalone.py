"""SMOD-04 — standalone Lambda smoke test (CONTEXT decision E).

Mirror of :mod:`tests.integration.test_lambda_django` for the no-Django
path. Runs the handler in a subprocess with ``DJANGO_SETTINGS_MODULE``
scrubbed (and ``SQLERY_FORCE_STANDALONE=1`` set) so the import-chain proves
the no-Django property at runtime, not just at definition time.

Fixture asymmetry (intentional; do NOT harmonize): the Django twin relies
on the global ``DJANGO_SETTINGS_MODULE = "tests.settings"`` pytest config,
the standalone twin explicitly removes that env var from the subprocess so
the compat detector routes to standalone. Both shapes are correct.

Assertion target (per PLAN-CHECKER-FIXES B1): the SQLModel row's status
field after the handler returns. NOT the handler return value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SIMPLE_JOB_PATH = "tests.integration.conftest.simple_job"


def _run_no_django(script: str, env_overrides: dict | None = None, timeout: int = 60) -> str:
    """Execute a python -c script with DJANGO_SETTINGS_MODULE scrubbed."""
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["SQLERY_FORCE_STANDALONE"] = "1"
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"subprocess failed (exit={result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return result.stdout


def test_lambda_standalone_smoke():
    """Standalone Lambda handler claims+executes; DB row lifecycle transitions."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    db_url = f"sqlite:///{tmp.name}"

    # 1. Bring up the standalone DB and enqueue a queued job in a no-django child.
    enqueue_script = (
        "import json;"
        "from sqlery.compat import initialize;"
        f"initialize(database_url={db_url!r}, enable_daemon=False);"
        "from sqlery.core.job_queue import enqueue as e;"
        f"job = e({SIMPLE_JOB_PATH!r}, a=1, b=2);"
        "print(json.dumps({'id': job.id}))"
    )
    out = _run_no_django(enqueue_script)
    job_id = int(json.loads(out.strip().splitlines()[-1])["id"])

    # 2. Invoke the standalone Lambda handler in a fresh no-django child.
    invoke_script = (
        "import json, os;"
        f"os.environ['SQLERY_DATABASE_URL'] = {db_url!r};"
        "from sqlery.fastapi_sqlery.lambda_handler import handler;"
        f"r = handler({{'action': 'process_queue', 'queue_name': 'default', 'job_id': {job_id}}}, None);"
        "print('RESULT', json.dumps(r))"
    )
    out = _run_no_django(invoke_script)
    assert "RESULT" in out

    # 3. PLAN-CHECKER-FIXES B1: read the SQLModel row's status directly and
    #    assert lifecycle transition (NOT return value).
    status_script = (
        "import json;"
        "from sqlery.compat import initialize, get_backend;"
        f"initialize(database_url={db_url!r}, enable_daemon=False);"
        f"job = get_backend().get_job_by_id({job_id});"
        "print(json.dumps({'status': getattr(job, 'status', 'missing')}))"
    )
    out = _run_no_django(status_script)
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] in {"running", "success", "failed"}, (
        f"Standalone lambda smoke: expected lifecycle transition "
        f"(running|success|failed) but row remained at {payload['status']!r}"
    )
