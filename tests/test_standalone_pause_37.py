"""#37: pausing a worker is Django-only. Standalone says so instead of pretending."""

import pytest

from tests.test_ready_queue_139 import StandaloneMode, mode  # noqa: F401


def test_a_standalone_worker_is_never_paused(mode):  # noqa: F811
    if not isinstance(mode, StandaloneMode):
        pytest.skip("standalone contract")
    mode.backend.update_worker_heartbeat("worker_host_4242", "idle")
    assert mode.backend.is_worker_paused("worker_host_4242") is False


def test_the_standalone_worker_table_has_no_pause_column():
    """If this fails, someone added pause storage: implement is_worker_paused to read it."""
    from sqlery.core.models import Worker

    assert "paused_until" not in Worker.model_fields
