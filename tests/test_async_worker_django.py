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
