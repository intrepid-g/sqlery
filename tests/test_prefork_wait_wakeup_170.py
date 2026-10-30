# ruff: noqa: F811
"""#170: the parent wakes the moment the job's child reports, not at the next backoff tick.

Contract: while a worker waits for a job's child (prefork pool; the fork-per-job pool waits on a pidfd) it blocks
on an fd that turns readable when the child reports or exits (the prefork result pipe, a
pidfd), instead of sleep-polling at 5, 15, 35, 75, 155 ... ms. A job that ends at time T is
therefore noticed within a few ms of T: a 50 ms job used to be reaped at the 75 ms tick, a
mean ~15 ms of idle parent per job. The parent safety net, revoke polling and the signal
and heartbeat cadence are unchanged: timeout and revoke still kill a child the parent is
blocked waiting on (see test_worker_pool_161.py and test_revoke tests for those paths).
"""

import statistics
import threading
import time

import pytest

from sqlery.core.job import job
from sqlery.core.pool import POOL_FORK, POOL_PREFORK
from tests.test_worker_pool_161 import backend, make_worker, run_next  # noqa: F401


@job
def stamp_after_nap(seconds):
    time.sleep(seconds)
    return repr(time.time())


@job
def nap_long(seconds):
    time.sleep(seconds)
    return "slept"


def test_a_finished_job_is_noticed_within_a_few_ms(backend, make_worker, monkeypatch, tmp_path):
    worker = make_worker(pool=POOL_PREFORK)
    mark = tmp_path / "child_done"
    real_run = worker.executor.run_job_in_child

    def marking_run(job):  # runs in the prefork child; the report follows right after
        code = real_run(job)
        mark.write_text(repr(time.time()))
        return code

    monkeypatch.setattr(worker.executor, "run_job_in_child", marking_run)
    real_poll = worker._poll_pool_child
    noticed = []

    def observing_poll(pool):
        done, code = real_poll(pool)
        if done:
            noticed.append(time.time())  # when the parent's wait loop saw the child report
        return done, code

    monkeypatch.setattr(worker, "_poll_pool_child", observing_poll)
    lags = []
    for i in range(25):
        stamp_after_nap.enqueue(seconds=0.05)
        claimed = backend.claim_job(["default"], worker.worker_id)
        result = worker._execute_claimed(claimed)
        assert result["success"], result
        if i:  # the first job also pays for starting the prefork child
            lags.append(noticed[-1] - float(mark.read_text()))
    # Sleep-polling put the median lag at ~20 ms (a 50 ms job was reaped at the 75 ms tick);
    # blocking on the result pipe makes it a pipe wake-up.
    assert statistics.median(lags) < 0.01, sorted(lags)


@pytest.mark.parametrize("pool", [POOL_PREFORK, POOL_FORK])
def test_revoke_still_kills_a_child_the_parent_is_blocked_on(backend, make_worker, pool):
    from sqlery.core.result import REVOKED_ERROR

    worker = make_worker(pool=pool)
    queued = nap_long.enqueue(seconds=30)
    claimed = backend.claim_job(["default"], worker.worker_id)
    # Revoked from "another host" while the parent is already blocked waiting.
    timer = threading.Timer(0.5, backend.request_job_revoke, args=(queued.id,))
    timer.start()
    try:
        start = time.monotonic()
        result = worker._execute_claimed(claimed)
    finally:
        timer.cancel()
    assert result == {"success": False, "error": REVOKED_ERROR}
    assert time.monotonic() - start < 10


def test_fork_pool_jobs_still_complete_while_waiting_on_a_pidfd(backend, make_worker):
    worker = make_worker(pool=POOL_FORK)
    for _ in range(3):
        stamp_after_nap.enqueue(seconds=0.05)
        claimed = backend.claim_job(["default"], worker.worker_id)
        result = worker._execute_claimed(claimed)
        assert result["success"], result
