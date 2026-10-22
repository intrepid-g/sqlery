"""AsyncWorker on the Django backend: concurrency (#68) and invoke_task semantics (#70)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from sqlery.core.async_worker import AsyncWorker
from sqlery.core.job import job as core_job
from sqlery.django_sqlery.async_backend import DjangoAsyncBackend
from sqlery.django_sqlery.models import QueuedJob

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@core_job
async def nap(seconds: float = 0.5) -> str:
    await asyncio.sleep(seconds)
    return "done"


@core_job
async def one() -> int:
    return 1


@core_job
async def plus_one(previous: int) -> int:
    return previous + 1


async def boom() -> None:
    raise RuntimeError("boom")


_bound_seen: list[Any] = []


@core_job(bind=True)
async def bound(ctx, x: int = 0) -> int:
    _bound_seen.append(ctx)
    return x


async def _run(**limits) -> None:
    worker = AsyncWorker(DjangoAsyncBackend(), ["default"], poll_interval=0.05)
    await asyncio.wait_for(worker.run(**limits), timeout=30)


async def _enqueue(name: str, **kwargs) -> QueuedJob:
    return await QueuedJob.objects.acreate(
        task_path=f"{__name__}.{name}", kwargs=kwargs, queue_name="default", status="queued"
    )


async def _check_concurrent() -> None:
    for _ in range(4):
        await _enqueue("nap", seconds=0.5)
    started = time.monotonic()
    await _run(max_jobs=4)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"4 x 0.5 s awaiting jobs took {elapsed:.2f}s: they ran one by one"
    assert [j.status async for j in QueuedJob.objects.all()] == ["success"] * 4


async def _check_chain_passes_result() -> None:
    from asgiref.sync import sync_to_async

    from sqlery import chain

    await sync_to_async(chain(one.si(), plus_one.s()).apply_async)()
    await _run(max_jobs=2)
    last = await QueuedJob.objects.order_by("-created_at").afirst()
    assert (last.status, last.output) == ("success", "2"), (last.status, last.error)


async def _check_retry_keeps_spec() -> None:
    first = await QueuedJob.objects.acreate(
        task_path=f"{__name__}.boom", kwargs={}, queue_name="default", status="queued",
        job_name="my-job", max_retries=1, retry_backoff=0.1, failure_ttl=60, result_ttl=60,
        tags=["t"], meta={"k": "v"}, dependencies=[], retry_intervals=[0.1],
    )
    await _run(max_jobs=1)
    first = await QueuedJob.objects.aget(id=first.id)
    retry = await QueuedJob.objects.aget(parent_job_id=first.id)
    assert (retry.job_name, retry.failure_ttl, retry.result_ttl) == ("my-job:retry:1", 60, 60)
    assert (retry.tags, retry.meta, retry.retry_intervals) == (["t"], {"k": "v"}, [0.1])
    assert retry.retry_count == 1
    assert first.status == "archived"


async def test_django_async_retry_keeps_spec_and_archives_failed_attempt():
    await _check_retry_keeps_spec()


@pytest.mark.postgres
async def test_django_async_retry_keeps_spec_and_archives_failed_attempt_pg():
    await _check_retry_keeps_spec()


async def test_django_async_worker_runs_waiting_jobs_concurrently():
    await _check_concurrent()


async def test_django_async_worker_passes_upstream_results_to_chain_steps():
    await _check_chain_passes_result()


async def test_django_async_worker_bind_context_and_signals():
    from sqlery.core.retry import TaskContext
    from sqlery.core.signals import task_postrun, task_prerun

    seen: list[str] = []

    def pre(sender, **kw):
        seen.append("pre")

    def post(sender, **kw):
        seen.append(kw["state"])

    task_prerun.connect(pre)
    task_postrun.connect(post)
    try:
        row = await _enqueue("bound", x=7)
        await _run(max_jobs=1)
    finally:
        task_prerun.disconnect(pre)
        task_postrun.disconnect(post)
    done = await QueuedJob.objects.aget(id=row.id)
    assert (done.status, done.output) == ("success", "7"), done.error
    assert isinstance(_bound_seen[-1], TaskContext)
    assert seen == ["pre", "SUCCESS"]


@pytest.mark.postgres
async def test_django_async_worker_runs_waiting_jobs_concurrently_pg():
    await _check_concurrent()


@pytest.mark.postgres
async def test_django_async_worker_passes_upstream_results_to_chain_steps_pg():
    await _check_chain_passes_result()


# --- orphan recovery (#71) -------------------------------------------------


async def test_django_async_worker_fails_orphaned_running_job_at_startup():
    from datetime import datetime, timezone

    orphan = await _enqueue("one")
    await QueuedJob.objects.filter(id=orphan.id).aupdate(
        status="running", started_at=datetime(2020, 1, 1, tzinfo=timezone.utc), worker=None
    )
    await _run(max_polls=2)
    assert (await QueuedJob.objects.aget(id=orphan.id)).status == "failed"


async def test_django_async_worker_sweep_spares_fresh_and_live_jobs():
    from datetime import datetime, timezone

    fresh = await _enqueue("one")
    await QueuedJob.objects.filter(id=fresh.id).aupdate(
        status="running", started_at=datetime.now(timezone.utc), worker=None
    )
    await _run(max_polls=2)
    assert (await QueuedJob.objects.aget(id=fresh.id)).status == "running"


async def test_django_async_worker_sweep_spares_long_job_of_live_worker():
    """A job older than WORKER_ALIVE_TIMEOUT claimed by a live async worker survives."""
    from datetime import datetime, timedelta, timezone

    job = await _enqueue("one")
    worker = AsyncWorker(DjangoAsyncBackend(), ["default"], poll_interval=0.05)
    await worker.backend.aregister_worker(
        worker.worker_id, {"node_id": "n", "pid": 1, "status": "busy", "queues": ["default"]}
    )
    claimed = await worker.backend.aclaim_job(["default"], str(worker.worker_id))
    assert claimed.id == job.id
    await QueuedJob.objects.filter(id=job.id).aupdate(
        started_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    await worker._recover_orphans()
    assert (await QueuedJob.objects.aget(id=job.id)).status == "running"


async def test_django_async_worker_sweep_spares_own_job_with_unresolvable_worker_id():
    """A worker id that doesn't resolve to a Worker row claims with worker=None; the sweep
    must not fail that worker's own in-flight job."""
    from datetime import datetime, timedelta, timezone

    job = await _enqueue("one")
    worker = AsyncWorker(DjangoAsyncBackend(), ["default"], worker_id="wkr-x", poll_interval=0.05)
    claimed = await worker.backend.aclaim_job(["default"], worker.worker_id)
    assert claimed.id == job.id
    await QueuedJob.objects.filter(id=job.id).aupdate(
        started_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    worker._inflight[job.id] = asyncio.ensure_future(asyncio.sleep(0))
    await worker._recover_orphans()
    assert (await QueuedJob.objects.aget(id=job.id)).status == "running"
