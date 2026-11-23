"""A paused Django worker claims no new job until ``paused_until`` passes (clause WRK-6).

End to end: a real worker subprocess over a real SQLite database. The pause is written the way
the dashboard API writes it (``Worker.paused_until``); the worker notices within one heartbeat
interval (1 s here), leaves the queued job alone, and takes it once the pause has run out.
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

from tests.test_hot_path_statements_172 import _ENQUEUE, _Db, _wait_finished, _Worker

PAUSE_SECONDS = 6
NOTICE_SECONDS = 2.5  # heartbeat interval 1 s, plus the poll interval and slack


def _wait_for_worker_row(db, timeout=30):
    deadline = time.monotonic() + timeout
    while not db.query("SELECT 1 FROM sqlery_worker"):
        assert time.monotonic() < deadline, "the worker never registered"
        time.sleep(0.05)


def _pause(db, seconds):
    until = (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")
    c = sqlite3.connect(db.env["H172_SQLITE"], timeout=30)
    c.execute("UPDATE sqlery_worker SET paused_until = ?", (until,))
    c.commit()
    c.close()


def test_a_paused_worker_claims_nothing_until_the_pause_passes(tmp_path):
    db = _Db("sqlite", tmp_path)
    with _Worker(db.env):
        _wait_for_worker_row(db)
        _pause(db, PAUSE_SECONDS)
        time.sleep(NOTICE_SECONDS)
        db.run(_ENQUEUE, H172_JOBS="1", H172_TASK="tests.test_hot_path_statements_172.noop")
        time.sleep(PAUSE_SECONDS / 3)
        assert db.finished() == 0
        assert db.query("SELECT count(*) FROM sqlery_job_ready")[0][0] == 1
        _wait_finished(db, 1, timeout=60)  # resumes after paused_until, no operator action
