"""process_scheduled (HTTP trigger + Lambda core) must enqueue due ScheduledTasks (#60)."""

import json
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from sqlery.core.signature import make_signed_request_headers
from sqlery.core.triggers import TriggerEnvelope, handle
from sqlery.django_sqlery.models import QueuedJob, ScheduledTask

SECRET = "s3cret"


def _signed(payload):
    body = json.dumps(payload).encode()
    return TriggerEnvelope(
        body=body, headers=make_signed_request_headers(SECRET, body), payload=payload
    )


def _make_due_task(queue_name):
    task = ScheduledTask.objects.create(
        name=f"repro-{queue_name}",
        task_path="builtins.print",
        schedule_type="interval",
        interval=1,
        interval_unit="seconds",
        queue_name=queue_name,
        enabled=True,
    )
    # Interval tasks start one interval in the future; force it due.
    ScheduledTask.objects.filter(pk=task.pk).update(
        next_run_at=timezone.now() - timedelta(seconds=5)
    )
    return task


@pytest.mark.django_db(transaction=True)
@override_settings(
    DJANGO_SQL_JOBS={
        "TRIGGER_MODE": "http",
        "INTERNAL_SECRET": SECRET,
        "INTERNAL_ALLOWED_IPS": ["*"],
    }
)
def test_http_process_scheduled_enqueues_due_tasks():
    _make_due_task("q2")
    result = handle(_signed({"action": "process_scheduled", "n": 2}))
    assert result.status_code == 200
    assert result.body["job_ids"], result.body
    assert QueuedJob.objects.filter(queue_name="q2").exists()


@pytest.mark.django_db(transaction=True)
def test_lambda_core_process_scheduled_enqueues_due_tasks():
    from sqlery.compat import get_backend
    from sqlery.core.lambda_core import process_event

    _make_due_task("q3")
    result = process_event({"action": "process_scheduled"}, get_backend())
    assert result["job_ids"]
    assert QueuedJob.objects.filter(queue_name="q3").exists()
