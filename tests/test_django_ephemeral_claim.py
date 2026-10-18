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
