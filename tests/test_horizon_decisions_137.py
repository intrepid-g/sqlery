"""Issue #137: pure decisions of the PostgreSQL xmin-horizon guard.

Each test states a scenario and the verdict ``assess_horizon`` must reach.
"""

import pytest

from sqlery.core.horizon import (
    DEGRADED,
    OK,
    UNKNOWN,
    HorizonThresholds,
    assess_horizon,
    metrics_from_row,
    thresholds_from_config,
)

T = HorizonThresholds(
    max_xmin_age_seconds=300, max_dead_tuple_ratio=0.5, min_dead_tuples=1000, max_slot_xid_age=1000
)


@pytest.mark.parametrize(
    "age, ratio, dead, slot, expected",
    [
        (0, 0.0, 0, 0, OK),  # idle healthy system
        (299, 0.1, 50, 10, OK),  # just under the age limit
        (301, 0.1, 50, 10, DEGRADED),  # a transaction held open past the limit
        (10, 0.9, 5000, 10, DEGRADED),  # table already bloated
        (10, 0.9, 20, 10, OK),  # high ratio on a tiny table is noise
        (10, 0.1, 50, 5000, DEGRADED),  # abandoned replication slot
        (None, None, None, None, UNKNOWN),  # probe failed entirely
        (None, 0.9, 5000, None, DEGRADED),  # bloat visible although age unreadable
        (None, 0.1, 50, None, OK),  # only the readable input is healthy
        (301, None, None, None, DEGRADED),  # age breach without bloat data
        (10, None, None, None, OK),  # age fine, bloat data unavailable
    ],
)
def test_horizon_verdict(age, ratio, dead, slot, expected):
    assert assess_horizon(age, ratio, T, dead_tuples=dead, slot_xid_age=slot) == expected


def test_zero_disables_each_limit():
    off = HorizonThresholds(0, 0, 0, 0)
    assert assess_horizon(10**6, 99.0, off, dead_tuples=10**6, slot_xid_age=10**9) == OK


def test_dead_tuple_floor_ignored_when_count_unknown():
    assert assess_horizon(0, 0.9, T) == DEGRADED


def test_empty_table_with_dead_rows_counts_as_bloated():
    row = (0, 0, 0, 0, 5000, 0)
    m = metrics_from_row(row)
    assert (
        assess_horizon(
            m["oldest_xmin_age_seconds"], m["dead_tuple_ratio"], T, dead_tuples=m["dead_tuples"]
        )
        == DEGRADED
    )


def test_oldest_holder_is_max_of_sessions_and_prepared_xacts():
    m = metrics_from_row((10, 400, 0, 0, 0, 0))
    assert m["oldest_xmin_age_seconds"] == 400


def test_invalid_or_negative_config_falls_back_to_defaults():
    cfg = {"PG_XMIN_AGE_DEGRADED_SECONDS": "abc", "PG_DEAD_TUPLE_RATIO_DEGRADED": -1}
    t = thresholds_from_config(lambda k, d: cfg.get(k, d))
    assert t == HorizonThresholds()


def test_configured_thresholds_are_used():
    t = thresholds_from_config(lambda k, d: {"PG_XMIN_AGE_DEGRADED_SECONDS": 60}.get(k, d))
    assert assess_horizon(61, 0.0, t) == DEGRADED
    assert assess_horizon(59, 0.0, t) == OK
