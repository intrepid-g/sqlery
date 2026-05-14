"""DMOD-04 — Django Lambda smoke test (CONTEXT decision E).

Reuses the global Django settings wiring from ``pyproject.toml``
(``DJANGO_SETTINGS_MODULE = "tests.settings"``); no separate
``integration_setup`` fixture is needed because the project-wide pytest
config already takes care of it. This is the asymmetry called out in
PLAN.md (also documented in :mod:`tests.integration.test_lambda_standalone`):
the Django twin relies on the global settings, the standalone twin
explicitly scrubs DJANGO_SETTINGS_MODULE from a subprocess env.

The assertion target is DB-row lifecycle (per PLAN-CHECKER-FIXES B1) —
NOT the handler return value. After ``handler({"action": "process_queue",
"queue_name": "default"}, None)`` runs, the QueuedJob row must have
transitioned to ``running`` or a terminal state.
"""

from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_lambda_django_smoke():
    """Lambda handler claims+executes one job; DB row lifecycle transitions."""
    from sqlery.django_sqlery.models import QueuedJob
    from sqlery.lambda_handler import handler

    # Enqueue via the Django ORM directly to keep the test self-contained.
    job = QueuedJob.objects.create(
        task_path="tests.integration.conftest.simple_job",
        kwargs={"a": 1, "b": 2},
        queue_name="default",
        status="queued",
    )

    # Use job_id to skip worker-registration prerequisite (Django's claim_job
    # requires a registered Worker row; in the Lambda smoke path that's an
    # operational pre-step, not something the smoke test exercises).
    result = handler({"action": "process_queue", "queue_name": "default", "job_id": job.id}, None)

    # PLAN-CHECKER-FIXES B1: assert DB-row status, NOT result["processed"].
    refreshed = QueuedJob.objects.get(id=job.id)
    assert refreshed.status in {"running", "success", "failed"}, (
        f"Lambda smoke: expected lifecycle transition (running|success|failed) "
        f"but row remained at status={refreshed.status!r}; "
        f"handler returned {result!r}"
    )
