"""Precedents for the DeadWorkerReaper party."""

from datetime import UTC, datetime, timedelta

from sqlery.contract import database
from sqlery.contract import dead_worker_reaper as reaper

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def ago(seconds):
    return NOW - timedelta(seconds=seconds)


def verdict(**kw):
    base = {
        "status": "idle",
        "pid": 10,
        "last_heartbeat": ago(1),
        "now": NOW,
        "pid_exists": None,
        "alive_timeout": 30,
    }
    return reaper.reap_verdict(**{**base, **kw})


def test_worker_silent_31_seconds_is_reaped_with_its_age_in_the_reason():
    assert verdict(last_heartbeat=ago(31)) == "heartbeat too old (31s > 30s threshold)"


def test_worker_silent_29_seconds_is_spared():
    assert verdict(last_heartbeat=ago(29)) is None


def test_worker_silent_exactly_the_alive_timeout_is_reaped_as_the_database_calls_it_dead():
    assert verdict(last_heartbeat=ago(30)) == "heartbeat too old (30s > 30s threshold)"


def test_worker_silent_45_seconds_is_reaped_under_the_default_timeout_not_a_hidden_60():
    assert verdict(last_heartbeat=ago(45)) == "heartbeat too old (45s > 30s threshold)"


def test_a_raised_alive_timeout_spares_a_worker_the_old_limit_would_have_reaped():
    assert verdict(last_heartbeat=ago(61), alive_timeout=120) is None
    assert verdict(last_heartbeat=ago(121), alive_timeout=120) == (
        "heartbeat too old (121s > 120s threshold)"
    )


def test_reaper_and_database_agree_on_the_boundary():
    for age in (29, 30, 31):
        beat = ago(age)
        reaped = verdict(last_heartbeat=beat) is not None
        assert reaped == (not database.is_beating("idle", beat, NOW, 30))


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


def test_probe_denied_by_permission_means_the_process_is_alive_under_another_user():
    assert reaper.probe_found_process(PermissionError()) is True


def test_probe_with_no_such_process_means_gone():
    assert reaper.probe_found_process(ProcessLookupError()) is False


def test_probe_that_raised_nothing_means_the_process_exists():
    assert reaper.probe_found_process(None) is True


# #48: the daemon no longer writes beats for its workers, so silence is read with care.


def test_safety_net_never_kills_a_process_the_pool_did_not_start():
    """The PID of a row the pool did not start may belong to an unrelated process by now."""
    assert reaper.safety_net_action(91, 90, True, started_by_pool=False) == "keep"
    assert reaper.safety_net_action(91, 90, False, started_by_pool=False) == "mark_dead"


def test_silence_counts_from_when_the_watcher_regained_the_database():
    assert reaper.silence_since(ago(500), ago(20)) == ago(20)


def test_a_beat_after_the_watch_began_is_its_own_age():
    assert reaper.silence_since(ago(5), ago(20)) == ago(5)


def test_silence_without_a_watch_window_is_the_beat_itself():
    assert reaper.silence_since(ago(500), None) == ago(500)
    assert reaper.silence_since(None, ago(20)) is None


def test_a_worker_silent_through_an_outage_is_spared_until_the_watch_is_old_enough():
    assert verdict(last_heartbeat=ago(500), watched_since=ago(10)) is None
    assert verdict(last_heartbeat=ago(500), watched_since=ago(31)) == (
        "heartbeat too old (31s > 30s threshold)"
    )


def test_a_live_process_the_pool_started_is_left_to_the_safety_net():
    """Marking it dead would fail its job while it (and its job child) still run."""
    assert reaper.left_to_safety_net(True, True) is True
    assert reaper.left_to_safety_net(False, True) is False
    assert reaper.left_to_safety_net(None, True) is False
    assert reaper.left_to_safety_net(True, False) is False
    assert verdict(last_heartbeat=ago(500), pid_exists=True, started_by_pool=True) is None
    assert verdict(last_heartbeat=ago(500), pid_exists=False, started_by_pool=True) is not None
