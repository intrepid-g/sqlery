"""A non-parallel job waits only for a live holder of its queue, and never starves others (#35)."""

import pytest

from tests.test_ready_queue_139 import DjangoMode, StandaloneMode, mode  # noqa: F401


def test_a_blocked_non_parallel_job_does_not_starve_a_parallel_job_behind_it(mode):
    holder = mode.new_job(allow_parallel=False)
    assert mode.claim().id == holder.id
    blocked = mode.new_job(allow_parallel=False)
    parallel = mode.new_job(allow_parallel=True)
    running_parallel = mode.claim()
    assert running_parallel.id == parallel.id
    assert mode.claim() is None
    mode.finish(holder)
    mode.finish(running_parallel)  # a running parallel job still holds the queue (unchanged)
    assert mode.claim().id == blocked.id


def test_a_live_holder_still_serializes_its_queue(mode):
    holder = mode.new_job(allow_parallel=False)
    assert mode.claim().id == holder.id
    mode.new_job(allow_parallel=False)
    assert mode.claim() is None


def test_another_queue_is_not_blocked_by_a_live_holder(mode):
    holder = mode.new_job(allow_parallel=False)
    assert mode.claim().id == holder.id
    other = mode.new_job(queue_name="reports", allow_parallel=False)
    assert mode.claim("reports").id == other.id


def test_a_dead_holder_stops_blocking_without_a_sweep(mode):
    worker = "worker_deadhost_777777"
    mode.backend.update_worker_heartbeat(worker, "busy")
    holder = mode.new_job(allow_parallel=False)
    assert mode.backend.claim_job(["default"], worker).id == holder.id
    waiting = mode.new_job(allow_parallel=False)
    assert mode.claim() is None
    mode.backend.update_worker_heartbeat(worker, "dead")  # crashed; no zombie sweep runs
    assert mode.claim().id == waiting.id


@pytest.mark.django_db
def test_default_allow_parallel_setting_changes_the_enqueue_default(settings):
    from sqlery import enqueue

    assert enqueue("tests.tasks.dummy_task").allow_parallel is False
    settings.DJANGO_SQL_JOBS = {"DEFAULT_ALLOW_PARALLEL": True}
    assert enqueue("tests.tasks.dummy_task").allow_parallel is True
    assert enqueue("tests.tasks.dummy_task", allow_parallel=False).allow_parallel is False
