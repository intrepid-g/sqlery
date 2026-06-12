"""Unit tests for DjangoAsyncBackend (ASYN-02).

Covers every async method declared on AsyncDatabaseBackend, exercised
against SQLite via Django's pytest-django + pytest-asyncio integration.
All methods must use the native async ORM — `sync_to_async` is forbidden
in the implementation.
"""

import asyncio
import uuid

import pytest
from django.utils import timezone

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _backfill_version(db):
    """Idempotent backfill of QueuedJob.version for legacy/test rows.

    See plan 02-04 [ASSUMED — version backfill]. Brand-new rows already have
    a default of 0, but legacy/migration paths may leave the column NULL.
    """
    from sqlery.django_sqlery.models import QueuedJob

    QueuedJob.objects.filter(version__isnull=True).update(version=0)
    yield


@pytest.fixture
def backend():
    from sqlery.django_sqlery.async_backend import DjangoAsyncBackend

    return DjangoAsyncBackend()


@pytest.fixture
def make_job():
    from sqlery.django_sqlery.models import QueuedJob

    def _factory(**kw):
        defaults = dict(
            task_path="tests.helpers.noop",
            queue_name="default",
            status="queued",
        )
        defaults.update(kw)
        return QueuedJob.objects.create(**defaults)

    return _factory


# ----- aclaim_job -----------------------------------------------------------


async def test_aclaim_job_returns_none_when_no_rows(backend):
    result = await backend.aclaim_job(["default"], "wkr-1")
    assert result is None


async def test_aclaim_job_claims_and_marks_running(backend, make_job):
    from asgiref.sync import sync_to_async  # only in tests, not in backend code

    job = await sync_to_async(make_job)()
    claimed = await backend.aclaim_job(["default"], "wkr-1")
    assert claimed is not None
    assert claimed.id == job.id

    from sqlery.django_sqlery.models import QueuedJob

    # Old: fresh = await QueuedJob.objects.aget(pk=job.id)
    # pk is now a composite (created_at, id); look up by id only.
    fresh = await QueuedJob.objects.aget(id=job.id)
    assert fresh.status == "running"


async def test_aclaim_job_filters_by_queue(backend, make_job):
    from asgiref.sync import sync_to_async

    await sync_to_async(make_job)(queue_name="other")
    result = await backend.aclaim_job(["default"], "wkr-1")
    assert result is None


async def test_aclaim_job_concurrent_only_one_wins(backend, make_job):
    from asgiref.sync import sync_to_async

    await sync_to_async(make_job)()

    results = await asyncio.gather(
        backend.aclaim_job(["default"], "wkr-A"),
        backend.aclaim_job(["default"], "wkr-B"),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


# ----- amark_running / amark_success / amark_failed / amark_shutting_down ---


async def test_amark_running(backend, make_job):
    from asgiref.sync import sync_to_async
    from sqlery.django_sqlery.models import QueuedJob

    job = await sync_to_async(make_job)()
    await backend.amark_running(job.id, "wkr-1")
    # Old: fresh = await QueuedJob.objects.aget(pk=job.id)
    # pk is now a composite (created_at, id); look up by id only.
    fresh = await QueuedJob.objects.aget(id=job.id)
    assert fresh.status == "running"


async def test_amark_success(backend, make_job):
    from asgiref.sync import sync_to_async
    from sqlery.django_sqlery.models import QueuedJob

    job = await sync_to_async(make_job)(status="running")
    await backend.amark_success(job.id, "ok")
    # Old: fresh = await QueuedJob.objects.aget(pk=job.id)
    fresh = await QueuedJob.objects.aget(id=job.id)
    assert fresh.status == "success"
    assert fresh.output == "ok"


async def test_amark_failed(backend, make_job):
    from asgiref.sync import sync_to_async
    from sqlery.django_sqlery.models import QueuedJob

    job = await sync_to_async(make_job)(status="running")
    await backend.amark_failed(job.id, "boom", traceback="tb")
    # Old: fresh = await QueuedJob.objects.aget(pk=job.id)
    fresh = await QueuedJob.objects.aget(id=job.id)
    assert fresh.status == "failed"
    assert fresh.error == "boom"
    assert fresh.traceback == "tb"


async def test_amark_shutting_down(backend, make_job):
    from asgiref.sync import sync_to_async
    from sqlery.django_sqlery.models import QueuedJob

    job = await sync_to_async(make_job)(status="running")
    await backend.amark_shutting_down(job.id)
    # Old: fresh = await QueuedJob.objects.aget(pk=job.id)
    fresh = await QueuedJob.objects.aget(id=job.id)
    assert fresh.status == "shutting_down"


# ----- aget_status / aget_job -----------------------------------------------


async def test_aget_status(backend, make_job):
    from asgiref.sync import sync_to_async

    job = await sync_to_async(make_job)()
    assert await backend.aget_status(job.id) == "queued"
    assert await backend.aget_status(999999) is None


async def test_aget_job(backend, make_job):
    from asgiref.sync import sync_to_async

    job = await sync_to_async(make_job)()
    fetched = await backend.aget_job(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert await backend.aget_job(999999) is None


# ----- worker registration & heartbeat --------------------------------------


async def test_aregister_and_aunregister_worker(backend):
    from sqlery.django_sqlery.models import Worker

    wid = uuid.uuid4()
    await backend.aregister_worker(
        wid, {"node_id": "node-1", "pid": 1234, "queues": ["default"]}
    )
    assert await Worker.objects.filter(pk=wid).aexists()

    await backend.aunregister_worker(wid)
    assert not await Worker.objects.filter(pk=wid).aexists()


async def test_aupdate_heartbeat(backend):
    from sqlery.django_sqlery.models import Worker

    wid = uuid.uuid4()
    await backend.aregister_worker(
        wid, {"node_id": "node-1", "pid": 1234, "queues": ["default"]}
    )
    before = (await Worker.objects.aget(pk=wid)).last_heartbeat
    await asyncio.sleep(0.01)
    await backend.aupdate_heartbeat(wid)
    after = (await Worker.objects.aget(pk=wid)).last_heartbeat
    assert after >= before


# ----- lease ops ------------------------------------------------------------


async def test_aclaim_lease_grants_when_free(backend):
    ok = await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30)
    assert ok is True


async def test_aclaim_lease_blocked_by_live_lease(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True
    assert await backend.aclaim_lease("default", "daemon-2", ttl_seconds=30) is False


async def test_arenew_lease(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True
    assert await backend.arenew_lease("default", "daemon-1") is True


async def test_arelease_lease(backend):
    assert await backend.aclaim_lease("default", "daemon-1", ttl_seconds=30) is True
    await backend.arelease_lease("default", "daemon-1")
    # After release, a new daemon should be able to claim.
    assert await backend.aclaim_lease("default", "daemon-2", ttl_seconds=30) is True


# ----- scheduled tasks ------------------------------------------------------


async def test_aget_due_scheduled_tasks(backend):
    from asgiref.sync import sync_to_async

    from sqlery.django_sqlery.models import ScheduledTask

    def _make():
        return ScheduledTask.objects.create(
            name="t1",
            task_path="tests.helpers.noop",
            schedule_type="cron",
            cron_expression="* * * * *",
            queue_name="default",
            enabled=True,
            next_run_at=timezone.now() - timezone.timedelta(seconds=1),
        )

    await sync_to_async(_make)()
    now = timezone.now()
    due = await backend.aget_due_scheduled_tasks(now)
    assert any(t.name == "t1" for t in due)


# ----- registry ops ---------------------------------------------------------


async def test_aregistry_add_and_remove(backend, make_job):
    from asgiref.sync import sync_to_async

    from sqlery.django_sqlery.models import JobRegistry

    job = await sync_to_async(make_job)()
    await backend.aregistry_add("started", job.id)
    assert await JobRegistry.objects.filter(
        job_id=job.id, registry_type="started", exited_at__isnull=True
    ).aexists()

    await backend.aregistry_remove("started", job.id)
    assert not await JobRegistry.objects.filter(
        job_id=job.id, registry_type="started", exited_at__isnull=True
    ).aexists()


# ----- no sync_to_async in implementation -----------------------------------


def test_no_sync_to_async_in_implementation():
    import pathlib

    src = pathlib.Path(
        __file__
    ).parent.parent / "src" / "sqlery" / "django_sqlery" / "async_backend.py"
    text = src.read_text()
    assert "sync_to_async" not in text
