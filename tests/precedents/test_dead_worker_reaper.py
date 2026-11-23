"""Precedents for the DeadWorkerReaper party."""

from datetime import UTC, datetime, timedelta

from sqlery.contract import dead_worker_reaper as reaper

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def ago(seconds):
    return NOW - timedelta(seconds=seconds)


def verdict(**kw):
    base = {"status": "idle", "pid": 10, "last_heartbeat": ago(1), "now": NOW, "pid_exists": None}
    return reaper.reap_verdict(**{**base, **kw})


def test_worker_silent_61_seconds_is_reaped_with_its_age_in_the_reason():
    assert verdict(last_heartbeat=ago(61)) == "heartbeat too old (61s > 60s threshold)"


def test_worker_silent_45_seconds_is_spared_even_though_it_is_past_the_alive_timeout():
    # Reaper limit (60s) is deliberately not the 30s alive timeout: a documented disagreement.
    assert verdict(last_heartbeat=ago(45)) is None


def test_worker_silent_exactly_60_seconds_is_spared():
    assert verdict(last_heartbeat=ago(60)) is None


def test_local_worker_whose_process_vanished_is_reaped_despite_a_fresh_beat():
    assert verdict(pid_exists=False) == "process not found on this node"


def test_local_worker_whose_process_exists_is_spared():
    assert verdict(pid_exists=True) is None


def test_remote_worker_with_a_fresh_beat_is_spared():
    assert verdict(pid_exists=None) is None


def test_daemon_row_is_never_reaped_however_silent():
    assert verdict(pid=0, last_heartbeat=ago(9999), pid_exists=False) is None


def test_worker_already_dead_is_not_reaped_twice():
    assert verdict(status="dead", last_heartbeat=ago(9999), pid_exists=False) is None


def test_worker_with_no_beat_at_all_is_left_to_other_sweeps():
    assert verdict(last_heartbeat=None, pid_exists=False) is None


def test_silent_past_uses_the_limit_it_is_given():
    assert reaper.is_silent_past("idle", 10, ago(31), NOW, 30)
    assert not reaper.is_silent_past("idle", 10, ago(29), NOW, 30)


def test_safety_net_keeps_a_worker_within_the_stale_limit():
    assert reaper.safety_net_action(90, 90, True) == "keep"


def test_safety_net_kills_a_stale_worker_whose_process_still_runs():
    assert reaper.safety_net_action(91, 90, True) == "kill"


def test_safety_net_marks_a_stale_worker_dead_when_its_process_is_gone():
    assert reaper.safety_net_action(91, 90, False) == "mark_dead"


def test_vanished_process_counts_as_gone_only_for_a_live_looking_local_worker():
    beat = datetime(2026, 1, 1, tzinfo=UTC)
    assert reaper.process_gone_on_this_node("busy", 4242, beat, False) is True
    assert reaper.process_gone_on_this_node("busy", 4242, beat, True) is False
    assert reaper.process_gone_on_this_node("busy", 4242, beat, None) is False
    assert reaper.process_gone_on_this_node("dead", 4242, beat, False) is False
    assert reaper.process_gone_on_this_node("busy", 4242, None, False) is False
