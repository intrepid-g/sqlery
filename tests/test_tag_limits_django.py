"""Django mode: TAG_CONCURRENCY_LIMITS from settings are applied by claim_next_job_with_queue_priority."""

import pytest
from django.test import override_settings

from sqlery.models import QueuedJob


def _job(**kw):
    kw.setdefault("task_path", "tests.tasks.dummy_task")
    kw.setdefault("queue_name", "default")
    kw.setdefault("status", "queued")
    return QueuedJob.objects.create(**kw)


@pytest.mark.django_db
def test_backend_claim_applies_configured_tag_limit():
    from sqlery.core.claiming import claim_next_job_with_queue_priority
    from sqlery.django_sqlery.backend import DjangoBackend

    with override_settings(DJANGO_SQL_JOBS={"TAG_CONCURRENCY_LIMITS": {"api": 1}}):
        _job(priority=5, tags=["api"])
        blocked = _job(priority=5, tags=["api"])
        plain = _job(priority=0)
        backend = DjangoBackend()
        first = claim_next_job_with_queue_priority(None, backend, queues=["default"])
        second = claim_next_job_with_queue_priority(None, backend, queues=["default"])
        assert first.tags == ["api"]
        # The second "api" job is over the limit; the untagged job behind it still runs.
        assert second.id == plain.id
        blocked.refresh_from_db()
        assert blocked.status == "queued"
