"""Precedents for the LeaseHolder party (#35)."""

from datetime import UTC, datetime, timedelta

from sqlery.contract import lease_holder

T0 = datetime(2026, 11, 22, 13, 5, tzinfo=UTC)


def test_first_sweep_is_due_at_once():
    assert lease_holder.interval_elapsed(None, 5, T0)


def test_sweep_is_not_due_one_second_before_the_interval():
    assert not lease_holder.interval_elapsed(T0, 5, T0 + timedelta(minutes=4, seconds=59))


def test_sweep_is_due_exactly_at_the_interval():
    assert lease_holder.interval_elapsed(T0, 5, T0 + timedelta(minutes=5))


def test_worker_without_a_lease_never_sweeps_even_on_its_first_poll():
    assert not lease_holder.sweep_due(set(), None, 5, T0)


def test_worker_holding_a_lease_sweeps_on_its_first_poll():
    assert lease_holder.sweep_due({"default"}, None, 5, T0)


def test_worker_holding_a_lease_waits_out_the_interval():
    assert not lease_holder.sweep_due({"default"}, T0, 5, T0 + timedelta(minutes=1))


def test_the_lease_holder_owes_exactly_these_sweeps_in_this_order():
    assert lease_holder.SWEEP_STEPS == ("zombies_failed", "dead_workers", "partitions")
