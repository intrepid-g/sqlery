"""#37: a fork with extra threads alive logs which threads they are."""

import logging
import os
import threading

from sqlery.core.fork_safety import ForkSafeExecutor, threaded_fork_warning


def test_one_thread_is_fine_and_several_are_named():
    assert threaded_fork_warning(["MainThread"]) is None
    line = threaded_fork_warning(["MainThread", "leaked-poller"])
    assert "2 threads" in line and "leaked-poller" in line


def test_fork_logs_the_leaked_thread_and_the_child_still_runs(caplog):
    stop = threading.Event()
    leaked = threading.Thread(target=stop.wait, name="leaked-test-thread")
    leaked.start()
    try:
        with caplog.at_level(logging.WARNING, logger="sqlery.core.fork_safety"):
            pid = ForkSafeExecutor().fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
    finally:
        stop.set()
        leaked.join()
    assert "leaked-test-thread" in caplog.text
