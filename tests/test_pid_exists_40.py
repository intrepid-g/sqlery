"""#40: both PID probes read the outcome through clause DWR-3 (EPERM means alive)."""

import os
from unittest import mock

import pytest

from sqlery.core import liveness
from sqlery.core.daemon import DaemonManager
from sqlery.core.worker_pool import WorkerPoolManager


@pytest.mark.parametrize(
    ("error", "alive"),
    [(None, True), (PermissionError(1, "denied"), True), (ProcessLookupError(3, "gone"), False)],
)
def test_pool_and_daemon_probes_agree_on_every_kill_outcome(error, alive):
    with mock.patch.object(os, "kill", side_effect=error):
        assert liveness.pid_exists(99) is alive
        assert WorkerPoolManager._pid_exists(99) is alive
        rec = mock.Mock(worker_pid=99, worker_node_id="n", spec=["worker_pid", "worker_node_id"])
        assert DaemonManager._probe_pid(rec, "n") is alive
        assert DaemonManager.is_process_running(object.__new__(DaemonManager), 99) is alive
