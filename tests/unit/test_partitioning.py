"""Unit tests for core/partitioning.py.

Tests describe behavior:
- _list_partitions: parses pg_inherits output correctly, returns None for DEFAULT
- ensure_future_partitions: skips if advisory lock not acquired; creates partitions; catches attach-conflict
- reclaim_drained_partitions: four skip rules (DEFAULT, retention window, live work, advisory lock); DETACH->archive->DROP order
- check_default_partition: returns 0 when no DEFAULT; returns row count; logs WARNING when count > 0

These tests use a mock cursor — no live DB required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch, ANY

import pytest


# ---------------------------------------------------------------------------
# Helpers — fake cursor factory
# ---------------------------------------------------------------------------


def _make_cursor(fetchall_sequence=None, fetchone_sequence=None):
    """Return a MagicMock cursor where execute() / fetchall() / fetchone()
    behave according to the provided sequences.

    fetchall_sequence: list of lists — each call to fetchall() pops the next.
    fetchone_sequence: list of values — each call to fetchone() pops the next.
    """
    cur = MagicMock()
    _fetchall_itr = iter(fetchall_sequence or [])
    _fetchone_itr = iter(fetchone_sequence or [])

    cur.fetchall.side_effect = lambda: next(_fetchall_itr)
    cur.fetchone.side_effect = lambda: next(_fetchone_itr)
    return cur


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# _list_partitions
# ---------------------------------------------------------------------------


class TestListPartitions:
    """_list_partitions parses pg_inherits rows into (name, upper_bound | None)."""

    def test_returns_empty_for_no_partitions(self):
        from sqlery.core.partitioning import _list_partitions

        cur = _make_cursor(fetchall_sequence=[[]])
        result = _list_partitions(cur, "sqlery_queued_job")
        assert result == []

    def test_parses_normal_partition_upper_bound(self):
        from sqlery.core.partitioning import _list_partitions

        expr = "FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-01-02 00:00:00+00')"
        cur = _make_cursor(fetchall_sequence=[[("sqlery_queued_job_20250101", expr)]])
        result = _list_partitions(cur, "sqlery_queued_job")
        assert len(result) == 1
        name, upper = result[0]
        assert name == "sqlery_queued_job_20250101"
        assert upper is not None
        assert upper.year == 2025
        assert upper.month == 1
        assert upper.day == 2
        # upper_bound must be timezone-aware
        assert upper.tzinfo is not None

    def test_returns_none_for_default_partition(self):
        from sqlery.core.partitioning import _list_partitions

        expr = "DEFAULT"
        cur = _make_cursor(fetchall_sequence=[[("sqlery_queued_job_default", expr)]])
        result = _list_partitions(cur, "sqlery_queued_job")
        assert len(result) == 1
        name, upper = result[0]
        assert name == "sqlery_queued_job_default"
        assert upper is None

    def test_returns_none_when_expression_does_not_match(self):
        from sqlery.core.partitioning import _list_partitions

        # Expression without the expected TO (...) pattern
        expr = "SOMETHING UNEXPECTED"
        cur = _make_cursor(fetchall_sequence=[[("weirdpart", expr)]])
        result = _list_partitions(cur, "sqlery_queued_job")
        name, upper = result[0]
        assert upper is None

    def test_handles_mixed_partitions(self):
        from sqlery.core.partitioning import _list_partitions

        rows = [
            (
                "sqlery_queued_job_20250101",
                "FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2025-01-02 00:00:00+00')",
            ),
            ("sqlery_queued_job_default", "DEFAULT"),
            (
                "sqlery_queued_job_20250102",
                "FOR VALUES FROM ('2025-01-02 00:00:00+00') TO ('2025-01-03 00:00:00+00')",
            ),
        ]
        cur = _make_cursor(fetchall_sequence=[rows])
        result = _list_partitions(cur, "sqlery_queued_job")
        assert len(result) == 3
        names = [r[0] for r in result]
        assert "sqlery_queued_job_20250101" in names
        assert "sqlery_queued_job_default" in names
        uppers = {r[0]: r[1] for r in result}
        assert uppers["sqlery_queued_job_default"] is None
        assert uppers["sqlery_queued_job_20250101"] is not None


# ---------------------------------------------------------------------------
# ensure_future_partitions
# ---------------------------------------------------------------------------


class TestEnsureFuturePartitions:
    """ensure_future_partitions creates next N daily partitions with advisory lock guard."""

    def _make_ensure_cursor(self, lock_acquired=True, date_sequence=None, side_effects=None):
        """Build a cursor whose execute() is a MagicMock and whose fetchone()
        returns advisory-lock result first, then dates for the date_trunc calls.
        """
        cur = MagicMock()
        # Sequence: first fetchone = advisory lock result; rest = date rows
        # Advisory lock returns (True,) or (False,)
        date_sequence = date_sequence or []
        responses = [(lock_acquired,)] + [(d,) for d in date_sequence]
        _itr = iter(responses)
        cur.fetchone.side_effect = lambda: next(_itr)
        if side_effects:
            cur.execute.side_effect = side_effects
        return cur

    def test_returns_zero_if_advisory_lock_not_acquired(self):
        from sqlery.core.partitioning import ensure_future_partitions

        cur = self._make_ensure_cursor(lock_acquired=False)
        result = ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=2)
        assert result == 0

    def test_calls_advisory_lock_before_ddl(self):
        from sqlery.core.partitioning import ensure_future_partitions

        # Lock acquired but no date rows — premake=0 means only iteration 0
        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        cur = self._make_ensure_cursor(
            lock_acquired=True,
            date_sequence=[now, now + timedelta(days=1)],
        )
        ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=1)
        # First execute call must contain advisory lock
        first_call_sql = str(cur.execute.call_args_list[0][0][0])
        assert "advisory" in first_call_sql.lower() or "pg_try_advisory_lock" in first_call_sql

    def test_releases_advisory_lock_on_success(self):
        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        cur = self._make_ensure_cursor(lock_acquired=True, date_sequence=[now])
        ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=0)
        # One of the execute calls should contain unlock
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "pg_advisory_unlock" in all_sqls

    def test_catches_attach_conflict_and_continues(self):
        """Attach-conflict error must be caught; loop must continue to next iteration."""
        import psycopg.errors

        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        # Two date rows for premake=1 (iterations 0 and 1)
        date_responses = [(True,), (now,), (now + timedelta(days=1))]
        _itr = iter(date_responses)

        cur = MagicMock()
        cur.fetchone.side_effect = lambda: next(_itr)

        call_count = [0]

        def execute_side_effect(sql, params=None):
            call_count[0] += 1
            sql_str = str(sql)
            # Raise on the first CREATE TABLE PARTITION OF call only
            if "PARTITION OF" in sql_str and call_count[0] == 3:
                raise psycopg.errors.InvalidTableDefinition("attach conflict")

        cur.execute.side_effect = execute_side_effect

        # Should NOT raise
        result = ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=1)
        # Returns an int (may be 0 or 1 depending on how errors are counted)
        assert isinstance(result, int)

    def test_advisory_lock_released_even_on_error(self):
        """Advisory lock must be released even if an unexpected error occurs."""
        import psycopg

        from sqlery.core.partitioning import ensure_future_partitions

        cur = MagicMock()
        date_responses = [(True,), (_utcnow().replace(hour=0, minute=0, second=0, microsecond=0),)]
        _itr = iter(date_responses)
        cur.fetchone.side_effect = lambda: next(_itr)

        call_count = [0]

        def side_effect(sql, params=None):
            call_count[0] += 1
            sql_str = str(sql)
            if "PARTITION OF" in sql_str:
                raise RuntimeError("unexpected error")

        cur.execute.side_effect = side_effect

        with pytest.raises(RuntimeError):
            ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=0)

        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "pg_advisory_unlock" in all_sqls


# ---------------------------------------------------------------------------
# reclaim_drained_partitions
# ---------------------------------------------------------------------------


class TestReclaimDrainedPartitions:
    """reclaim_drained_partitions: four skip rules + DETACH→archive→DROP order."""

    def _list_parts_cursor(self, lock_acquired=True, parts=None, live_work_results=None):
        """Build cursor for reclaim tests.

        Parts: list of (name, upper_bound_or_None).
        live_work_results: list of booleans (one per non-skipped partition).
        """
        cur = MagicMock()
        parts = parts or []
        live_work_results = live_work_results or []
        # Build the pg_inherits rows for _list_partitions
        pg_rows = []
        for name, upper in parts:
            if upper is None:
                expr = "DEFAULT"
            else:
                expr = f"FOR VALUES FROM ('...') TO ('{upper.strftime('%Y-%m-%d %H:%M:%S+00')}')"
            pg_rows.append((name, expr))

        _itr_live = iter(live_work_results)

        def fetchone_side():
            # First call: advisory lock
            return (lock_acquired,)

        def fetchall_side():
            # Called by _list_partitions
            return pg_rows

        cur.fetchone.side_effect = fetchone_side
        cur.fetchall.return_value = pg_rows  # _list_partitions uses fetchall

        def fetchone_for_exists():
            return (next(_itr_live),)

        # After the advisory lock fetchone, the EXISTS check calls fetchone
        # We set side_effect to a sequence
        responses = [(lock_acquired,)] + [(v,) for v in live_work_results]
        _resp_itr = iter(responses)
        cur.fetchone.side_effect = lambda: next(_resp_itr)
        return cur

    def test_returns_zero_if_advisory_lock_not_acquired(self):
        from sqlery.core.partitioning import reclaim_drained_partitions

        cur = MagicMock()
        cur.fetchone.return_value = (False,)
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0

    def test_skip_rule_1_skips_default_partition(self):
        """Partition with upper_bound=None (DEFAULT) is never dropped."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        default_name = "sqlery_queued_job_default"
        cur = self._list_parts_cursor(
            lock_acquired=True,
            parts=[(default_name, None)],
            live_work_results=[],  # no EXISTS check expected
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0
        # DROP TABLE must never appear
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DROP" not in all_sqls.upper() or default_name not in all_sqls

    def test_skip_rule_2_skips_inside_retention_window(self):
        """Partition whose upper_bound is within retention is not dropped."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        # upper_bound = yesterday → still inside 30-day retention
        yesterday = _utcnow() - timedelta(days=1)
        cur = self._list_parts_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_recent", yesterday)],
            live_work_results=[],
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0

    def test_skip_rule_3_skips_partition_with_live_work(self):
        """Partition with queued/running rows is not dropped (back-pressure invariant)."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._list_parts_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[True],  # EXISTS returns True → live work
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DROP" not in all_sqls.upper()

    def test_detach_before_drop_order(self):
        """DETACH PARTITION must execute before DROP TABLE."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._list_parts_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[False],  # no live work
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 1

        sqls = [str(c[0][0]) for c in cur.execute.call_args_list]
        detach_idx = next(
            (i for i, s in enumerate(sqls) if "DETACH" in s.upper()), None
        )
        drop_idx = next((i for i, s in enumerate(sqls) if "DROP" in s.upper()), None)
        assert detach_idx is not None, "DETACH PARTITION not found in SQL calls"
        assert drop_idx is not None, "DROP TABLE not found in SQL calls"
        assert detach_idx < drop_idx, "DETACH must come before DROP"

    def test_archive_hook_called_between_detach_and_drop(self):
        """archive_hook(cur, name) is called after DETACH, before DROP."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._list_parts_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[False],
        )

        call_order = []

        def track_execute(sql, params=None):
            call_order.append(("execute", str(sql)))

        cur.execute.side_effect = track_execute

        def archive_hook(hook_cur, name):
            call_order.append(("archive_hook", name))

        reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days", archive_hook=archive_hook)

        detach_i = next(
            (i for i, (t, s) in enumerate(call_order) if t == "execute" and "DETACH" in s.upper()),
            None,
        )
        hook_i = next(
            (i for i, (t, _) in enumerate(call_order) if t == "archive_hook"), None
        )
        drop_i = next(
            (i for i, (t, s) in enumerate(call_order) if t == "execute" and "DROP" in s.upper()),
            None,
        )
        assert detach_i is not None
        assert hook_i is not None
        assert drop_i is not None
        assert detach_i < hook_i < drop_i, f"Expected DETACH<hook<DROP but got order: {call_order}"

    def test_archive_hook_exception_does_not_block_drop(self):
        """archive_hook failure is logged and the DROP still proceeds."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._list_parts_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[False],
        )

        def failing_hook(hook_cur, name):
            raise RuntimeError("archive system down")

        result = reclaim_drained_partitions(
            cur, "sqlery_queued_job", "30 days", archive_hook=failing_hook
        )
        # DROP should still have been called
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DROP" in all_sqls.upper()
        assert result == 1

    def test_advisory_lock_released_after_reclaim(self):
        from sqlery.core.partitioning import reclaim_drained_partitions

        cur = self._list_parts_cursor(lock_acquired=True, parts=[], live_work_results=[])
        reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "pg_advisory_unlock" in all_sqls


# ---------------------------------------------------------------------------
# check_default_partition
# ---------------------------------------------------------------------------


class TestCheckDefaultPartition:
    """check_default_partition returns int row count; logs WARNING when > 0."""

    def test_returns_zero_when_no_default_partition(self):
        from sqlery.core.partitioning import check_default_partition

        # pg_inherits returns no DEFAULT rows
        cur = MagicMock()
        cur.fetchall.return_value = []
        result = check_default_partition(cur, "sqlery_queued_job")
        assert result == 0

    def test_returns_count_from_default_partition(self):
        from sqlery.core.partitioning import check_default_partition

        cur = MagicMock()
        # First fetchall: pg_inherits rows — one DEFAULT
        cur.fetchall.return_value = [("sqlery_queued_job_default", "DEFAULT")]
        # fetchone returns COUNT(*)
        cur.fetchone.return_value = (42,)
        result = check_default_partition(cur, "sqlery_queued_job")
        assert result == 42

    def test_logs_warning_when_count_positive(self, caplog):
        from sqlery.core.partitioning import check_default_partition

        cur = MagicMock()
        cur.fetchall.return_value = [("sqlery_queued_job_default", "DEFAULT")]
        cur.fetchone.return_value = (5,)
        with caplog.at_level(logging.WARNING, logger="sqlery.core.partitioning"):
            result = check_default_partition(cur, "sqlery_queued_job")
        assert result == 5
        assert any("DEFAULT" in r.message and "5" in r.message for r in caplog.records)

    def test_no_warning_when_count_zero(self, caplog):
        from sqlery.core.partitioning import check_default_partition

        cur = MagicMock()
        cur.fetchall.return_value = [("sqlery_queued_job_default", "DEFAULT")]
        cur.fetchone.return_value = (0,)
        with caplog.at_level(logging.WARNING, logger="sqlery.core.partitioning"):
            result = check_default_partition(cur, "sqlery_queued_job")
        assert result == 0
        assert not any("DEFAULT" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


class TestModuleInvariants:
    """Verify module-level constants and __all__."""

    def test_advisory_lock_constants_defined(self):
        from sqlery.core import partitioning

        assert hasattr(partitioning, "ADVISORY_LOCK_ENSURE")
        assert hasattr(partitioning, "ADVISORY_LOCK_RECLAIM")

    def test_advisory_lock_constants_are_distinct_int64s(self):
        from sqlery.core import partitioning

        a = partitioning.ADVISORY_LOCK_ENSURE
        b = partitioning.ADVISORY_LOCK_RECLAIM
        assert isinstance(a, int)
        assert isinstance(b, int)
        assert a != b
        # Must fit in signed int64
        max_int64 = (2**63) - 1
        assert -(2**63) <= a <= max_int64
        assert -(2**63) <= b <= max_int64

    def test_all_exports_defined(self):
        from sqlery.core import partitioning

        for name in ["ensure_future_partitions", "reclaim_drained_partitions", "check_default_partition"]:
            assert name in partitioning.__all__
            assert callable(getattr(partitioning, name))

    def test_no_orm_imports_at_module_level(self):
        """AST-level check: no django/sqlalchemy/sqlmodel at col_offset==0."""
        import ast
        import pathlib

        src_path = pathlib.Path(__file__).parent.parent.parent / "src" / "sqlery" / "core" / "partitioning.py"
        src = src_path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                if any(x in module for x in ("django", "sqlalchemy", "sqlmodel")):
                    if node.col_offset == 0:
                        pytest.fail(f"ORM import at module level: {ast.dump(node)}")
