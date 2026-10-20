"""Django mode: Lambda / HTTP-trigger claimers have no worker row and must still claim."""

import pytest

from sqlery.models import QueuedJob


@pytest.mark.django_db
@pytest.mark.parametrize("worker_id", ["lambda-5d7b3f0e-aaaa-bbbb-cccc-0123456789ab",
                                       "trigger-5d7b3f0e-aaaa-bbbb-cccc-0123456789ab"])
def test_ephemeral_claimer_claims_a_queued_job(worker_id):
    from sqlery.compat import get_backend

    QueuedJob.objects.create(task_path="tests.tasks.dummy_task", queue_name="default",
                             status="queued")
    claimed = get_backend().claim_job(["default"], worker_id)
    assert claimed is not None and claimed.status == "running"


@pytest.mark.django_db
def test_registered_worker_ids_still_get_their_worker_row():
    """The normal worker path is unchanged: a "worker_<node>_<pid>" id auto-registers."""
    from sqlery.compat import get_backend
    from sqlery.models import Worker

    QueuedJob.objects.create(task_path="tests.tasks.dummy_task", queue_name="default",
                             status="queued")
    claimed = get_backend().claim_job(["default"], "worker_nodeA_4242")
    assert claimed is not None
    assert Worker.objects.filter(node_id="nodeA", pid=4242).exists()


@pytest.mark.django_db
def test_core_trigger_and_lambda_paths_run_a_queued_job_in_django_mode():
    """End to end through the shared helpers that mint 'lambda-'/'trigger-' ids."""
    from sqlery.compat import get_backend
    from sqlery.core.lambda_core import process_event

    j = QueuedJob.objects.create(task_path="tests.test_executor.dummy_task",
                                 queue_name="default", status="queued")
    out = process_event({"action": "process_queue", "queue_name": "default"}, get_backend())
    assert out["job_ids"] == [j.id] and out["processed"] == [j.id]
    j.refresh_from_db()
    assert j.status == "success"


@pytest.fixture
def trigger_settings(settings):
    settings.DJANGO_SQL_JOBS = {
        **getattr(settings, "DJANGO_SQL_JOBS", {}),
        "TRIGGER_MODE": "http",
        "INTERNAL_SECRET": "s3cret",
        "INTERNAL_ALLOWED_IPS": ["*"],
    }
    return settings


@pytest.mark.django_db
def test_http_trigger_process_queue_claims_a_queued_job(trigger_settings):
    """Issue #59: a signed process_queue (no job_id) must claim and run a job from the queue."""
    import json

    from sqlery.compat import get_backend
    from sqlery.core.signature import make_signed_request_headers
    from sqlery.core.triggers import TriggerEnvelope, handle

    job = get_backend().create_job(
        task_path="tests.test_executor.dummy_task",
        kwargs={},
        queue_name="q1",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
        job_name="repro-59",
    )
    payload = {"action": "process_queue", "queue_name": "q1", "n": 1}
    body = json.dumps(payload).encode()
    headers = make_signed_request_headers("s3cret", body)
    result = handle(TriggerEnvelope(body=body, headers=headers, payload=payload))

    assert result.status_code == 200
    assert result.body["claimed"] is True, result.body
    assert result.body["job_ids"] == [job.id]
    assert QueuedJob.objects.get(id=job.id).status == "success"
