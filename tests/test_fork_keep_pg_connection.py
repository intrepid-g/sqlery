"""#127: keeping the parent's PostgreSQL connection open across the per-job fork.

Isolation contract (owner's constraint): the forked child must never use, close or write
through a connection it inherited from the parent. The keep mode is opt-in
(``KEEP_PG_CONNECTION_ACROSS_FORK``, default off), PostgreSQL only; SQLite/libSQL and any
non-idle PostgreSQL connection are always closed before the fork.

The real-PostgreSQL tests need SQLERY_TEST_PG_URL (the Django test DB then is PostgreSQL).
"""

import os
import select
from unittest.mock import MagicMock, patch

import pytest

from sqlery.core import fork_safety
from sqlery.core.fork_safety import (
    ForkSafeExecutor,
    build_default_hooks,
    can_keep_across_fork,
)

_PG = bool(os.environ.get("SQLERY_TEST_PG_URL"))
needs_pg = pytest.mark.skipif(not _PG, reason="SQLERY_TEST_PG_URL not set")


def _keep(flag):
    return patch(
        "sqlery.compat.get_config",
        side_effect=lambda key, default=None: flag
        if key == fork_safety.KEEP_PG_CONNECTION_SETTING
        else default,
    )


class TestPlan:
    def test_default_hooks_unchanged(self):
        hooks = build_default_hooks(django_available=True)
        assert hooks["pre_fork"] == ["django_close_all"]
        assert hooks["post_fork_parent"] == ["django_close_old"]
        assert hooks["post_fork_child"] == ["django_close_all"]

    def test_keep_mode_hooks(self):
        hooks = build_default_hooks(django_available=True, keep_pg_connection=True)
        assert hooks["pre_fork"] == ["django_close_unkept"]
        assert hooks["post_fork_parent"] == []
        assert hooks["post_fork_child"] == ["django_disown_pg"]

    def test_default_off_even_with_django(self):
        with _keep(False):
            assert ForkSafeExecutor.auto_configure().keep_pg_connection is False

    def test_opt_in_turns_it_on(self):
        with _keep(True):
            assert ForkSafeExecutor.auto_configure().keep_pg_connection is True

    @pytest.mark.parametrize(
        "vendor,atomic,rollback,pool,tx,expected",
        [
            ("postgresql", False, False, False, 0, True),
            ("sqlite", False, False, False, 0, False),
            ("libsql", False, False, False, 0, False),
            ("postgresql", True, False, False, 0, False),
            ("postgresql", False, True, False, 0, False),
            ("postgresql", False, False, True, 0, False),
            ("postgresql", False, False, False, 2, False),  # INTRANS
            ("postgresql", False, False, False, None, False),
        ],
    )
    def test_only_idle_unpooled_postgres_is_kept(
        self, vendor, atomic, rollback, pool, tx, expected
    ):
        assert can_keep_across_fork(vendor, atomic, rollback, pool, tx) is expected


def _fake_conn(vendor, tx_status=0, atomic=False):
    c = MagicMock()
    c.vendor = vendor
    c.in_atomic_block = atomic
    c.needs_rollback = False
    c.settings_dict = {"OPTIONS": {}}
    c.connection = MagicMock()
    c.connection.info.transaction_status = tx_status
    raw = c.connection
    return c, raw


class TestHooksWithFakeConnections:
    def _patched(self, conns):
        fake = MagicMock()
        fake.all.return_value = conns
        return patch("django.db.connections", fake)

    def test_pre_fork_closes_everything_but_idle_postgres(self):
        pg, _ = _fake_conn("postgresql")
        lite, _ = _fake_conn("sqlite")
        libsql, _ = _fake_conn("libsql")
        busy_pg, _ = _fake_conn("postgresql", tx_status=2)
        in_tx_pg, _ = _fake_conn("postgresql", atomic=True)
        with self._patched([pg, lite, libsql, busy_pg, in_tx_pg]):
            ForkSafeExecutor.django_close_unkept()
        pg.close.assert_not_called()
        for c in (lite, libsql, busy_pg, in_tx_pg):
            c.close.assert_called_once()

    def test_child_disowns_postgres_without_closing_and_closes_the_rest(self):
        pg, raw = _fake_conn("postgresql")
        lite, _ = _fake_conn("sqlite")
        with self._patched([pg, lite]):
            ForkSafeExecutor.django_disown_pg()
        raw.close.assert_not_called()
        pg.close.assert_not_called()
        assert pg.connection is None
        assert raw in fork_safety._DISOWNED_PG_CONNECTIONS  # parked: never finalised
        lite.close.assert_called_once()
        fork_safety._DISOWNED_PG_CONNECTIONS.remove(raw)


def _child_report(rfd):
    ready, _, _ = select.select([rfd], [], [], 15)
    return os.read(rfd, 4096).decode() if ready else "timeout"


@needs_pg
@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
class TestRealPostgresFork:
    def _fork_probe(self, executor, child_fn):
        r, w = os.pipe()
        pid = executor.fork()
        if pid == 0:
            os.close(r)
            try:
                msg = child_fn()
            except BaseException as e:  # report, never unwind into the test runner
                msg = f"EXC {type(e).__name__}: {e}"
            os.write(w, msg.encode())
            os._exit(0)
        os.close(w)
        msg = _child_report(r)
        os.close(r)
        os.waitpid(pid, 0)
        return msg

    def test_keep_mode_child_never_uses_or_closes_parent_connection(self):
        import gc

        import psycopg
        from django.db import connection

        connection.ensure_connection()
        with connection.cursor() as cur:
            cur.execute("SET sqlery.fork_probe = 'parent'")
            cur.execute("SELECT pg_backend_pid()")
            parent_pid = cur.fetchone()[0]
        raw = connection.connection
        parent_os_pid = os.getpid()
        real_close = psycopg.Connection.close

        def guarded_close(self, *a, **k):
            if self is raw and os.getpid() != parent_os_pid:
                raise AssertionError("child closed the parent's connection")
            return real_close(self, *a, **k)

        def child():
            if connection.connection is not None:
                return "child still holds an inherited connection"
            gc.collect()  # would PQfinish the shared socket if the object were finalised
            with connection.cursor() as cur:  # opens the child's OWN connection
                cur.execute("SELECT pg_backend_pid(), current_setting('sqlery.fork_probe', true)")
                child_pid, probe = cur.fetchone()
                cur.execute(
                    "SELECT state FROM pg_stat_activity WHERE pid = %s", [parent_pid]
                )
                parent_state = cur.fetchone()
            if child_pid == parent_pid:
                return "child used the parent's backend"
            if probe:
                return f"child saw the parent's session state {probe!r}"
            if parent_state is None:
                return "parent session vanished while child ran"
            connection.close()  # the child's own connection: allowed to close
            return "ok"

        with _keep(True), patch.object(psycopg.Connection, "close", guarded_close):
            executor = ForkSafeExecutor.auto_configure()
            msg = self._fork_probe(executor, child)

        assert msg == "ok", msg
        # The parent session is the very same connection, untouched by the child.
        assert connection.connection is raw
        with connection.cursor() as cur:
            cur.execute("SELECT pg_backend_pid(), current_setting('sqlery.fork_probe', true)")
            assert cur.fetchone() == (parent_pid, "parent")

    def test_naive_child_close_would_kill_parent_session(self):
        """Sensitivity check: the hazard the disown hook prevents is real and detected."""
        from django.db import connection
        from django.db.utils import InterfaceError, OperationalError

        connection.ensure_connection()
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        raw = connection.connection

        def child():
            raw.close()  # what a careless child would do to the inherited connection
            return "closed"

        executor = ForkSafeExecutor()  # no hooks: nothing disowns the connection
        assert self._fork_probe(executor, child) == "closed"
        with pytest.raises((OperationalError, InterfaceError)):
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        connection.connection = None  # discard the dead connection

    def test_default_closes_before_fork_and_child_inherits_nothing(self):
        from django.db import connection

        connection.ensure_connection()
        seen = {}

        def child():
            return "none" if connection.connection is None else "inherited"

        with _keep(False):
            executor = ForkSafeExecutor.auto_configure()
            seen["msg"] = self._fork_probe(executor, child)
        assert seen["msg"] == "none"
        assert connection.connection is None  # closed before the fork (default behaviour)
        with connection.cursor() as cur:  # parent reconnects on demand
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)

    def test_keep_mode_closes_a_connection_mid_transaction(self):
        from django.db import connection, transaction

        connection.ensure_connection()
        with _keep(True):
            executor = ForkSafeExecutor.auto_configure()
            with transaction.atomic():
                msg = self._fork_probe(
                    executor,
                    lambda: "none"
                    if connection.connection is None or connection.connection.closed
                    else "inherited",
                )
                # Django keeps the (now closed) driver object while inside atomic().
                assert connection.connection is None or connection.connection.closed
        assert msg == "none"
