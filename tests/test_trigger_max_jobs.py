"""process_queue over the HTTP trigger runs up to payload max_jobs jobs per request (#116)."""

import itertools
import json

import pytest
from django.test import override_settings

from sqlery.core.signature import make_signed_request_headers
from sqlery.core.triggers import TriggerEnvelope, handle
from sqlery.django_sqlery.models import QueuedJob

SECRET = "s3cret"
_nonce = itertools.count()
SETTINGS = {"TRIGGER_MODE": "http", "INTERNAL_SECRET": SECRET, "INTERNAL_ALLOWED_IPS": ["*"]}


def _process_queue(**extra):
    payload = {"action": "process_queue", "queue_name": "q116", "nonce": next(_nonce), **extra}
    body = json.dumps(payload).encode()
    return handle(
        TriggerEnvelope(
            body=body, headers=make_signed_request_headers(SECRET, body), payload=payload
        )
    )


def _enqueue(n):
    for _ in range(n):
        QueuedJob.objects.create(task_path="time.time", queue_name="q116", status="queued")


def _left():
    return QueuedJob.objects.filter(queue_name="q116", status="queued").count()


@pytest.mark.django_db(transaction=True)
@override_settings(DJANGO_SQL_JOBS=SETTINGS)
def test_default_runs_exactly_one_job():
    _enqueue(3)
    result = _process_queue()
    assert result.status_code == 200
    assert len(result.body["job_ids"]) == 1
    assert _left() == 2


@pytest.mark.django_db(transaction=True)
@override_settings(DJANGO_SQL_JOBS=SETTINGS)
def test_max_jobs_drains_up_to_the_cap():
    _enqueue(5)
    result = _process_queue(max_jobs=3)
    assert len(result.body["job_ids"]) == 3
    assert _left() == 2


@pytest.mark.django_db(transaction=True)
@override_settings(DJANGO_SQL_JOBS=SETTINGS)
def test_max_jobs_stops_when_the_queue_is_empty():
    _enqueue(2)
    result = _process_queue(max_jobs=10)
    assert len(result.body["job_ids"]) == 2
    assert result.body["claimed"] is True
    assert _process_queue(max_jobs=10).body == {"ok": True, "claimed": False, "job_ids": []}


@pytest.mark.django_db(transaction=True)
@override_settings(DJANGO_SQL_JOBS=SETTINGS)
def test_budget_stops_between_jobs_after_at_least_one():
    _enqueue(3)
    result = _process_queue(max_jobs=3, budget_s=1e-9)
    assert len(result.body["job_ids"]) == 1


@pytest.mark.django_db(transaction=True)
@override_settings(DJANGO_SQL_JOBS=SETTINGS)
def test_bad_max_jobs_falls_back_to_one():
    _enqueue(2)
    assert len(_process_queue(max_jobs="lots").body["job_ids"]) == 1
    assert len(_process_queue(max_jobs=-4).body["job_ids"]) == 1
