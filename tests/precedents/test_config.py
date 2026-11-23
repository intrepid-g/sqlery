"""Precedents for the Config party: the two liveness knobs must agree."""

import logging

from sqlery.contract import config
from sqlery.core.liveness import check_liveness_config
from sqlery.django_sqlery.settings import DEFAULTS


def test_heartbeat_below_alive_timeout_is_accepted():
    assert config.heartbeat_fits_alive_timeout(5, 30)


def test_heartbeat_equal_to_alive_timeout_is_refused():
    assert not config.heartbeat_fits_alive_timeout(30, 30)


def test_heartbeat_above_alive_timeout_is_refused():
    assert not config.heartbeat_fits_alive_timeout(45, 30)


def test_heartbeat_zero_means_beat_every_poll_and_is_accepted():
    assert config.heartbeat_fits_alive_timeout(0, 30)


def test_negative_heartbeat_is_refused():
    assert not config.heartbeat_fits_alive_timeout(-1, 30)


def test_valid_config_draws_no_complaint():
    assert config.misconfiguration(5, 30) is None


def test_invalid_config_names_both_knobs_and_both_values():
    msg = config.misconfiguration(40, 30)
    assert "WORKER_HEARTBEAT_INTERVAL (40s)" in msg
    assert "WORKER_ALIVE_TIMEOUT (30s)" in msg


def test_shipped_defaults_satisfy_the_contract():
    assert DEFAULTS["WORKER_HEARTBEAT_INTERVAL"] == 5
    assert DEFAULTS["WORKER_ALIVE_TIMEOUT"] == 30
    assert config.heartbeat_fits_alive_timeout(
        DEFAULTS["WORKER_HEARTBEAT_INTERVAL"], DEFAULTS["WORKER_ALIVE_TIMEOUT"]
    )


def test_startup_check_logs_a_warning_for_a_bad_config_and_stays_quiet_for_a_good_one(caplog):
    with caplog.at_level(logging.WARNING, logger="sqlery.core.liveness"):
        check_liveness_config(5, 30)
        assert caplog.records == []
        check_liveness_config(60, 30)
    assert len(caplog.records) == 1
