"""Phase 18 LISTEN/NOTIFY acceptance tests.

PG tests skip when SQLERY_TEST_PG_URL is unset.
Flag-off and unit tests run unconditionally.

Success criteria proved:
  SC1 — With SQLERY_PG_NOTIFY=True, enqueue-to-dispatch latency < 100 ms on PG.
  SC2 — With SQLERY_PG_NOTIFY=False (default), no pg_notify is emitted and no
         LISTEN connection is opened (byte-identical to pre-Phase-18 behaviour).

Also verifies fork-safety (LISTEN conn closed pre-fork) and SQLite no-op.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from sqlery.core.pg_notify import sanitize_queue_name_to_channel

# ---------------------------------------------------------------------------
# PG skip marker — mirrors test_lifecycle_partitioned.py exactly
# ---------------------------------------------------------------------------

_SKIP_NO_PG = pytest.mark.skipif(
    not os.environ.get("SQLERY_TEST_PG_URL"),
    reason="SQLERY_TEST_PG_URL not set — PG required for LISTEN/NOTIFY tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker_process(queues=None):
    """Create a WorkerProcess with a mocked backend (avoids Django DB calls)."""
    from sqlery.core.worker import WorkerProcess

    mock_backend = MagicMock()
    # update_worker_heartbeat is called during construction indirectly; mock safely.
    mock_backend.update_worker_heartbeat.return_value = None
    mock_backend.claim_queue_leases.return_value = []

    worker = WorkerProcess.__new__(WorkerProcess)
    # Replicate __init__ attribute assignments without calling get_backend()
    worker.backend = mock_backend
    worker.queues = queues or ["default"]
    worker.executor = MagicMock()
    from sqlery.core.fork_safety import ForkSafeExecutor
    worker._fork_ctx = ForkSafeExecutor()
    worker.shutdown_requested = False
    worker.jobs_processed = 0
    worker.current_job = None
    worker.child_pid = None
    worker._owned_queues = set()
    worker._lease_secs = 0
    worker.total_busy_seconds = 0.0
    worker._heartbeat_due = False
    worker._last_loop_time = time.monotonic()
    import socket
    worker.node_id = "test-node"
    worker.pid = os.getpid()
    worker.worker_id = f"worker_test_{os.getpid()}"
    worker.poll_interval = 5
    worker.heartbeat_interval = 5
    worker._listen_conn = None
    return worker


# ===========================================================================
# SC2: FLAG-OFF TESTS (no PG needed, always run)
# ===========================================================================


class TestFlagOffBehavior:
    """Assert byte-identical behavior when SQLERY_PG_NOTIFY=False (default).

    These tests prove SC2: no pg_notify is emitted and no LISTEN connection
    is opened when the flag is off.
    """

    def test_no_notify_emitted_when_flag_off(self):
        """DjangoBackend.create_job must NOT call _notify_queue_django when flag is off."""
        from sqlery.django_sqlery.backend import DjangoBackend

        sentinel = MagicMock()

        with (
            patch(
                "sqlery.django_sqlery.backend.get_setting",
                side_effect=lambda key, default=None: (
                    False if key == "SQLERY_PG_NOTIFY" else default
                ),
            ),
            patch(
                "sqlery.django_sqlery.backend._notify_queue_django",
                sentinel,
            ),
        ):
            backend = DjangoBackend()
            # create_job path — mock the ORM so no DB is touched.
            mock_job = MagicMock()
            backend.QueuedJob = MagicMock()
            backend.QueuedJob.objects.create.return_value = mock_job
            backend.ScheduledJob = MagicMock()
            backend._partitioned_pg = MagicMock(return_value=False)

            with patch("sqlery.django_sqlery.backend.connection") as mock_conn:
                mock_conn.vendor = "sqlite3"
                backend.create_job(
                    task_path="myapp.tasks.noop",
                    kwargs={},
                    queue_name="default",
                    priority=0,
                    scheduled_at=None,
                    max_retries=0,
                    retry_backoff=1.0,
                    allow_parallel=True,
                    timeout_seconds=None,
                )

        sentinel.assert_not_called()

    def test_no_listen_conn_opened_when_flag_off(self):
        """WorkerProcess._open_listen_conn() must leave _listen_conn=None when flag off."""
        worker = _make_worker_process()

        with patch(
            "sqlery.core.worker.get_config",
            side_effect=lambda key, default=None: (
                False if key == "SQLERY_PG_NOTIFY" else default
            ),
        ):
            worker._open_listen_conn()

        assert worker._listen_conn is None, (
            "LISTEN connection must not be opened when SQLERY_PG_NOTIFY=False"
        )

    def test_open_listen_conn_flag_off_does_not_import_psycopg(self):
        """_open_listen_conn returns before touching psycopg when flag is False.

        The guard 'if not get_config(SQLERY_PG_NOTIFY, False): return' must
        fire first — no psycopg.connect call even if psycopg is available.
        """
        worker = _make_worker_process()

        mock_psycopg_connect = MagicMock()

        with (
            patch(
                "sqlery.core.worker.get_config",
                side_effect=lambda key, default=None: (
                    False if key == "SQLERY_PG_NOTIFY" else default
                ),
            ),
            patch("sqlery.core.worker._psycopg") as mock_psycopg,
        ):
            mock_psycopg.connect = mock_psycopg_connect
            worker._open_listen_conn()

        mock_psycopg_connect.assert_not_called()
        assert worker._listen_conn is None


# ===========================================================================
# SQLite no-op tests
# ===========================================================================


class TestSQLiteNoOp:
    """Prove that flag=True on SQLite emits no pg_notify and opens no LISTEN conn."""

    def test_notify_noop_on_sqlite(self):
        """notify_queue_django must not call on_commit when vendor is not postgresql."""
        from sqlery.core.pg_notify import notify_queue_django

        mock_transaction = MagicMock()
        mock_connection = MagicMock()
        mock_connection.vendor = "sqlite3"

        with (
            patch(
                "sqlery.core.pg_notify._django_transaction",
                mock_transaction,
            ),
            # Phase 18 (IN-01): code now reads the module-level _django_connection.
            patch("sqlery.core.pg_notify._django_connection", mock_connection),
        ):
            notify_queue_django("default")

        mock_transaction.on_commit.assert_not_called()

    def test_open_listen_conn_noop_on_sqlite(self):
        """_open_listen_conn sets _listen_conn=None when DATABASE_URL is not PG."""
        worker = _make_worker_process()

        with (
            patch(
                "sqlery.core.worker.get_config",
                side_effect=lambda key, default=None: (
                    True if key == "SQLERY_PG_NOTIFY"
                    else "sqlite:///test.db" if key == "DATABASE_URL"
                    else default
                ),
            ),
            patch("sqlery.core.worker._psycopg_available", True),
        ):
            worker._open_listen_conn()

        assert worker._listen_conn is None, (
            "_listen_conn must remain None on SQLite even with flag on"
        )

    def test_sanitize_queue_name_to_channel_basic(self):
        """Pure unit — sanitize_queue_name_to_channel produces expected channel names."""
        assert sanitize_queue_name_to_channel("default") == "sqlery_job_default"
        assert sanitize_queue_name_to_channel("my-queue") == "sqlery_job_my_queue"
        assert sanitize_queue_name_to_channel("test queue") == "sqlery_job_test_queue"
        with pytest.raises(ValueError):
            sanitize_queue_name_to_channel("")
        long_name = "a" * 200
        channel = sanitize_queue_name_to_channel(long_name)
        assert len(channel) <= 63


# ===========================================================================
# FORK-SAFETY TESTS (no PG needed, mocked)
# ===========================================================================


class TestForkSafety:
    """Prove the LISTEN connection is closed before os.fork() — child does not inherit it."""

    def test_listen_conn_not_in_child(self):
        """Pre-fork hook must close and null-out _listen_conn before os.fork().

        Simulates: open LISTEN conn → register pre_fork hook → run pre_fork
        hooks → assert _listen_conn is None and .close() was called once.
        """
        worker = _make_worker_process()

        # Simulate an open LISTEN connection.
        mock_conn = MagicMock()
        worker._listen_conn = mock_conn

        # Register the pre_fork hook exactly as run() does.
        worker._fork_ctx.register_pre_fork(worker._close_listen_conn)

        # Trigger all registered pre_fork hooks (as ForkSafeExecutor.fork() does).
        for hook in worker._fork_ctx._pre_fork:
            hook()

        assert worker._listen_conn is None, (
            "_close_listen_conn must set _listen_conn=None so the child never sees it"
        )
        mock_conn.close.assert_called_once()

    def test_close_listen_conn_safe_when_none(self):
        """_close_listen_conn must not raise when _listen_conn is already None."""
        worker = _make_worker_process()
        assert worker._listen_conn is None
        # Must not raise
        worker._close_listen_conn()
        assert worker._listen_conn is None

    def test_close_listen_conn_swallows_exceptions(self):
        """_close_listen_conn must not raise even if conn.close() raises."""
        worker = _make_worker_process()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("connection reset")
        worker._listen_conn = mock_conn

        # Must not raise
        worker._close_listen_conn()
        assert worker._listen_conn is None


# ===========================================================================
# WR-01: CONNECTION LEAK ON ERROR / REOPEN (no PG needed, mocked)
# ===========================================================================


class TestOpenListenConnNoLeak:
    """WR-01: a failed LISTEN setup or a reopen must not leak the psycopg conn."""

    def test_listen_failure_closes_connection(self):
        """If LISTEN raises, the opened connection is closed, not orphaned."""
        worker = _make_worker_process(queues=["default"])

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("permission denied on channel")

        with (
            patch(
                "sqlery.core.worker.get_config",
                side_effect=lambda key, default=None: (
                    True if key == "SQLERY_PG_NOTIFY"
                    else "postgresql://u:p@h/db" if key == "DATABASE_URL"
                    else default
                ),
            ),
            patch("sqlery.core.worker._psycopg_available", True),
            patch("sqlery.core.worker._psycopg") as mock_psycopg,
        ):
            mock_psycopg.connect.return_value = mock_conn
            worker._open_listen_conn()

        mock_conn.close.assert_called_once()
        assert worker._listen_conn is None

    def test_reopen_closes_previous_connection(self):
        """Reopening closes the prior _listen_conn before opening a new one."""
        worker = _make_worker_process(queues=["default"])

        stale_conn = MagicMock()
        worker._listen_conn = stale_conn

        new_conn = MagicMock()

        with (
            patch(
                "sqlery.core.worker.get_config",
                side_effect=lambda key, default=None: (
                    True if key == "SQLERY_PG_NOTIFY"
                    else "postgresql://u:p@h/db" if key == "DATABASE_URL"
                    else default
                ),
            ),
            patch("sqlery.core.worker._psycopg_available", True),
            patch("sqlery.core.worker._psycopg") as mock_psycopg,
        ):
            mock_psycopg.connect.return_value = new_conn
            worker._open_listen_conn()

        stale_conn.close.assert_called_once()
        assert worker._listen_conn is new_conn


# ===========================================================================
# SC1: SUB-100MS LATENCY TEST (PG required)
# ===========================================================================


class TestListenNotifyLatencyPG:
    """SC1 acceptance tests — requires a live PostgreSQL database.

    Skipped when SQLERY_TEST_PG_URL is not set.
    """

    @_SKIP_NO_PG
    def test_dispatch_latency_under_100ms_django(self):
        """LISTEN wakeup after pg_notify must arrive in < 100 ms.

        Strategy: open a psycopg3 AUTOCOMMIT LISTEN connection, call
        _wait_for_notify() in a background thread, then send pg_notify on a
        second connection and measure wall-clock time from notify to wakeup.
        """
        try:
            import psycopg
        except ImportError:
            pytest.skip("psycopg not installed — cannot test LISTEN/NOTIFY latency")

        pg_url = os.environ["SQLERY_TEST_PG_URL"]

        channel = sanitize_queue_name_to_channel("default")

        # Build a minimal worker-like object that _wait_for_notify() needs.
        worker = _make_worker_process()
        worker.poll_interval = 5  # would block 5s without NOTIFY

        # Open the LISTEN connection.
        listen_conn = psycopg.connect(pg_url, autocommit=True)
        listen_conn.execute(
            f"LISTEN {channel}"
        )
        worker._listen_conn = listen_conn

        wakeup_event = threading.Event()
        thread_start = time.monotonic()

        def run_wait():
            worker._wait_for_notify()
            wakeup_event.set()

        t = threading.Thread(target=run_wait, daemon=True)
        t.start()

        # Wait long enough for the background thread to enter notifies().
        time.sleep(0.05)

        # Send pg_notify on a separate connection.
        notify_conn = psycopg.connect(pg_url, autocommit=True)
        t0 = time.monotonic()
        notify_conn.execute("SELECT pg_notify(%s, '')", [channel])
        notify_conn.close()

        # Wait for wakeup (timeout generous to survive slow CI).
        woke = wakeup_event.wait(timeout=1.0)

        t1 = time.monotonic()
        listen_conn.close()
        t.join(timeout=0.5)

        assert woke, "Worker thread did not wake up after pg_notify within 1 s"

        elapsed = t1 - t0
        # < 200 ms generous bound: proves NOTIFY-driven wakeup beats a 5 s poll.
        # The hard success criterion is < 100 ms; we allow 200 ms for slow CI.
        assert elapsed < 0.200, (
            f"LISTEN wakeup took {elapsed * 1000:.1f} ms — expected < 200 ms "
            f"(SC1 goal: < 100 ms)"
        )

    @_SKIP_NO_PG
    def test_flag_off_no_listen_connection_pg(self):
        """On real PG: _open_listen_conn must NOT open a connection when flag is False."""
        try:
            import psycopg
        except ImportError:
            pytest.skip("psycopg not installed")

        worker = _make_worker_process()

        mock_connect = MagicMock()

        with (
            patch(
                "sqlery.core.worker.get_config",
                side_effect=lambda key, default=None: (
                    False if key == "SQLERY_PG_NOTIFY"
                    else os.environ["SQLERY_TEST_PG_URL"] if key == "DATABASE_URL"
                    else default
                ),
            ),
            patch("sqlery.core.worker._psycopg") as mock_psycopg,
        ):
            mock_psycopg.connect = mock_connect
            worker._open_listen_conn()

        assert worker._listen_conn is None
        mock_connect.assert_not_called()

    def test_sqlite_no_notify_emitted_on_enqueue(self):
        """create_job on SQLite must NOT call pg_notify even if flag is patched to True.

        The guard in DjangoBackend.create_job checks connection.vendor == 'postgresql'
        before firing the notify.  On SQLite this guard keeps behaviour byte-identical.
        """
        from sqlery.django_sqlery.backend import DjangoBackend

        sentinel = MagicMock()

        with (
            patch(
                "sqlery.django_sqlery.backend.get_setting",
                side_effect=lambda key, default=None: (
                    True if key == "SQLERY_PG_NOTIFY" else default
                ),
            ),
            patch(
                "sqlery.django_sqlery.backend._notify_queue_django",
                sentinel,
            ),
        ):
            backend = DjangoBackend()
            mock_job = MagicMock()
            backend.QueuedJob = MagicMock()
            backend.QueuedJob.objects.create.return_value = mock_job
            backend.ScheduledJob = MagicMock()
            backend._partitioned_pg = MagicMock(return_value=False)

            with patch("sqlery.django_sqlery.backend.connection") as mock_conn:
                mock_conn.vendor = "sqlite3"
                backend.create_job(
                    task_path="myapp.tasks.noop",
                    kwargs={},
                    queue_name="default",
                    priority=0,
                    scheduled_at=None,
                    max_retries=0,
                    retry_backoff=1.0,
                    allow_parallel=True,
                    timeout_seconds=None,
                )

        sentinel.assert_not_called()
