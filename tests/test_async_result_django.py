"""Django mode: JobFunction.apply_async + AsyncResult over DjangoBackend."""

import pytest

from sqlery.core.result import AsyncResult, serialize_result
from sqlery.django_sqlery.decorators import job


@job(queue="ar", max_retries=2, tags=["t1"])
def _add(a, b=0):
    return a + b


@pytest.mark.django_db
def test_apply_async_binds_args_and_returns_result_handle():
    from sqlery.compat import get_backend

    res = _add.apply_async(args=(2,), kwargs={"b": 3}, countdown=30, expires=300)
    assert isinstance(res, AsyncResult) and res.state == "PENDING"
    j = get_backend().get_job_by_id(res.id)
    assert j.kwargs == {"a": 2, "b": 3}
    assert j.queue_name == "ar" and j.max_retries == 2 and j.tags == ["t1"]
    assert j.ttl == 300 and j.scheduled_at is not None

    j.status = "success"
    j.output = serialize_result(5)
    j.save(update_fields=["status", "output"])
    assert res.successful() and res.get(timeout=1) == 5


@pytest.mark.django_db
def test_django_retry_chain_is_followed():
    from sqlery.compat import get_backend

    backend = get_backend()
    res = _add.apply_async(args=(1,))
    parent = backend.get_job_by_id(res.id)
    parent.status = "archived"
    parent.save(update_fields=["status"])
    child = backend.create_job(
        task_path=parent.task_path, kwargs=parent.kwargs, queue_name="ar", priority=0,
        scheduled_at=None, max_retries=2, retry_backoff=1.0, allow_parallel=True,
        timeout_seconds=None, parent_job_id=parent.id, retry_count=1,
    )
    assert backend.get_latest_retry(parent.id).id == child.id
    child.status = "success"
    child.output = "7"
    child.save(update_fields=["status", "output"])
    assert res.get(timeout=1) == 7
