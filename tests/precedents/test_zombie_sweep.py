"""Precedents for the ZombieSweep party: the five checks, in order."""

from datetime import UTC, datetime, timedelta

from sqlery.contract import zombie_sweep as zs
from sqlery.core.liveness import RunningJobLiveness

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def ago(seconds):
    return NOW - timedelta(seconds=seconds)


def rec(**kw):
    base = {
        "job_id": 7,
        "started_at": ago(100),
        "worker_pid": None,
        "worker_node_id": "n1",
        "worker_status": "busy",
        "worker_current_job_id": 7,
        "worker_last_heartbeat": ago(1),
        "worker_friendly_name": "w1",
        "has_worker": True,
    }
    return RunningJobLiveness(**{**base, **kw})


def verdict(r, concurrent=False, pid_alive=None):
    return zs.zombie_verdict(
        r, now=NOW, alive_timeout=30, pid_alive=pid_alive, concurrent_workers=concurrent
    )


def test_healthy_job_is_left_running():
    assert verdict(rec()) is None


def test_stale_threshold_is_three_alive_timeouts():
    assert zs.stale_threshold(30) == 90


def test_pid_is_probed_only_on_the_node_that_owns_it():
    assert zs.pid_probe_applies(123, "n1", "n1")
    assert not zs.pid_probe_applies(123, "n2", "n1")
    assert not zs.pid_probe_applies(None, "n1", "n1")


def test_job_whose_worker_pid_is_gone_is_a_zombie():
    r = rec(worker_pid=123)
    assert verdict(r, pid_alive=False) == "Worker PID 123 no longer exists on n1"


def test_job_whose_pid_was_not_probed_is_not_failed_for_it():
    assert verdict(rec(worker_pid=123), pid_alive=None) is None


def test_running_job_with_no_worker_is_a_zombie():
    r = rec(has_worker=False, worker_status=None, worker_current_job_id=None)
    assert verdict(r) == "Running job has no worker assigned"


def test_young_unassigned_job_of_an_async_worker_gets_the_claim_grace_period():
    r = rec(has_worker=False, started_at=ago(5), worker_current_job_id=None)
    assert verdict(r, concurrent=True) is None


def test_old_unassigned_job_of_an_async_worker_is_a_zombie():
    r = rec(has_worker=False, started_at=ago(31), worker_current_job_id=None)
    assert verdict(r, concurrent=True) == "Running job has no worker assigned"


def test_young_unassigned_job_without_async_workers_is_still_a_zombie():
    r = rec(has_worker=False, started_at=ago(5), worker_current_job_id=None)
    assert verdict(r) == "Running job has no worker assigned"


def test_job_of_a_dead_worker_is_a_zombie():
    assert verdict(rec(worker_status="dead")) == "Assigned worker w1 is dead"


def test_job_the_worker_moved_on_from_is_a_zombie():
    r = rec(worker_current_job_id=9)
    assert verdict(r) == "Worker w1 moved on to job #9 — this job was abandoned"


def test_moved_on_check_is_skipped_for_workers_running_many_jobs():
    assert verdict(rec(worker_current_job_id=9), concurrent=True) is None


def test_job_of_an_idle_worker_older_than_the_alive_timeout_is_a_zombie():
    r = rec(worker_current_job_id=None, started_at=ago(45))
    assert verdict(r) == "Worker w1 is idle but job has been running for 45s — zombie"


def test_job_of_an_idle_worker_inside_the_claim_grace_period_is_spared():
    assert verdict(rec(worker_current_job_id=None, started_at=ago(10))) is None


def test_job_of_a_worker_silent_91_seconds_is_a_zombie():
    r = rec(worker_last_heartbeat=ago(91))
    assert verdict(r) == "Worker w1 heartbeat stale (91s old, threshold 90s)"


def test_job_of_a_worker_silent_89_seconds_is_spared():
    assert verdict(rec(worker_last_heartbeat=ago(89))) is None


def test_job_of_a_worker_with_no_beat_recorded_is_not_failed_for_staleness():
    assert verdict(rec(worker_last_heartbeat=None)) is None


def test_earliest_failing_check_names_the_reason():
    r = rec(worker_status="dead", worker_current_job_id=9, worker_last_heartbeat=ago(500))
    assert verdict(r) == "Assigned worker w1 is dead"


def test_each_check_stays_silent_on_a_healthy_job_when_asked_alone():
    r = rec()
    assert zs.pid_is_gone(r, True) is None
    assert zs.unassigned_job_is_zombie(r, NOW, 30, False) is None
    assert zs.worker_marked_dead(r) is None
    assert zs.worker_abandoned_job(r, NOW, 30, False) is None
    assert zs.heartbeat_is_stale(r, NOW, 30) is None


def test_each_check_speaks_for_its_own_fault_when_asked_alone():
    assert zs.pid_is_gone(rec(worker_pid=5), False) == "Worker PID 5 no longer exists on n1"
    assert zs.worker_marked_dead(rec(worker_status="dead")) == "Assigned worker w1 is dead"
    assert zs.worker_abandoned_job(rec(worker_current_job_id=3), NOW, 30, False).startswith(
        "Worker w1 moved on"
    )
    assert zs.heartbeat_is_stale(rec(worker_last_heartbeat=ago(200)), NOW, 30).startswith(
        "Worker w1 heartbeat stale"
    )
    assert zs.unassigned_job_is_zombie(rec(has_worker=False), NOW, 30, False) is not None
