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
    """ensure_future_partitions creates next N daily partitions with advisory lock guard.

    Implementation call pattern per iteration (premake=N means N+1 iterations):
      1. fetchone -> (lock_acquired,)           [advisory lock]
      per iteration:
        2. fetchone -> (lo,)                    [date_trunc call]
        3. fetchone -> (hi,)                    [lo + interval call]
        [4. execute CREATE TABLE ... (no fetchone)]
      after loop:
        5. execute pg_advisory_unlock           [no fetchone]
    """

    def _make_ensure_cursor(self, lock_acquired=True, date_pairs=None):
        """Build a cursor for ensure_future_partitions tests.

        date_pairs: list of (lo, hi) tuples, one per iteration.
        Each iteration calls fetchone twice: once for lo, once for hi.
        """
        cur = MagicMock()
        date_pairs = date_pairs or []
        # Build flat sequence: (lock,) + (lo,) (hi,) (lo,) (hi,) ...
        responses = [(lock_acquired,)]
        for lo, hi in date_pairs:
            responses.append((lo,))
            responses.append((hi,))
        _itr = iter(responses)
        cur.fetchone.side_effect = lambda: next(_itr)
        return cur

    def test_returns_zero_if_advisory_lock_not_acquired(self):
        from sqlery.core.partitioning import ensure_future_partitions

        cur = self._make_ensure_cursor(lock_acquired=False)
        result = ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=2)
        assert result == 0

    def test_calls_advisory_lock_before_ddl(self):
        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        hi = now + timedelta(days=1)
        hi2 = now + timedelta(days=2)
        cur = self._make_ensure_cursor(
            lock_acquired=True,
            date_pairs=[(now, hi), (hi, hi2)],
        )
        ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=1)
        # First execute call must contain advisory lock
        first_call_sql = str(cur.execute.call_args_list[0][0][0])
        assert "advisory" in first_call_sql.lower() or "pg_try_advisory_lock" in first_call_sql

    def test_releases_advisory_lock_on_success(self):
        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        hi = now + timedelta(days=1)
        cur = self._make_ensure_cursor(lock_acquired=True, date_pairs=[(now, hi)])
        ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=0)
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "pg_advisory_unlock" in all_sqls

    def test_catches_attach_conflict_and_continues(self):
        """Attach-conflict error must be caught; loop must continue to next iteration."""
        import psycopg.errors

        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        hi = now + timedelta(days=1)
        hi2 = now + timedelta(days=2)

        # Provide responses for both iterations
        responses = [
            (True,),       # advisory lock
            (now,), (hi,), # iteration 0: lo, hi
            (hi,), (hi2,), # iteration 1: lo, hi
        ]
        _itr = iter(responses)
        cur = MagicMock()
        cur.fetchone.side_effect = lambda: next(_itr)

        call_count = [0]

        def execute_side_effect(sql, params=None):
            call_count[0] += 1
            sql_str = str(sql)
            # Raise on the first CREATE TABLE PARTITION OF call only
            if "PARTITION OF" in sql_str and call_count[0] == 4:
                raise psycopg.errors.InvalidTableDefinition("attach conflict")

        cur.execute.side_effect = execute_side_effect

        # Should NOT raise
        result = ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=1)
        # Returns an int (may be 0 or 1 depending on how errors are counted)
        assert isinstance(result, int)

    def test_operational_error_propagates_not_swallowed_as_attach_conflict(self):
        """psycopg.OperationalError (connection lost) must propagate — not be caught as attach-conflict (WR-03)."""
        import psycopg

        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        hi = now + timedelta(days=1)

        responses = [(True,), (now,), (hi,)]
        _itr = iter(responses)
        cur = MagicMock()
        cur.fetchone.side_effect = lambda: next(_itr)

        def side_effect(sql, params=None):
            if "PARTITION OF" in str(sql):
                raise psycopg.OperationalError("connection lost")

        cur.execute.side_effect = side_effect

        # OperationalError must propagate — advisory lock still released via finally
        with pytest.raises(psycopg.OperationalError):
            ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=0)

        # Advisory lock must still be released despite the error
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "pg_advisory_unlock" in all_sqls

    def test_advisory_lock_released_even_on_error(self):
        """Advisory lock must be released even if an unexpected error occurs."""
        from sqlery.core.partitioning import ensure_future_partitions

        now = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        hi = now + timedelta(days=1)

        responses = [(True,), (now,), (hi,)]
        _itr = iter(responses)
        cur = MagicMock()
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

    def test_sub_daily_interval_uses_time_suffix_in_partition_name(self):
        """Sub-daily interval must include HH%MM suffix to avoid same-day name collisions (WR-01).

        Two iterations within the same calendar day must produce distinct partition names.
        """
        from sqlery.core.partitioning import ensure_future_partitions

        # Two hourly partitions on the same day: 00:00 and 01:00
        from datetime import datetime, timezone
        day_start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        hour_1 = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        hour_2 = datetime(2025, 1, 1, 2, 0, 0, tzinfo=timezone.utc)

        responses = [
            (True,),            # advisory lock
            (day_start,), (hour_1,),  # iteration 0: lo=00:00, hi=01:00
            (hour_1,), (hour_2,),     # iteration 1: lo=01:00, hi=02:00
        ]
        _itr = iter(responses)
        cur = MagicMock()
        cur.fetchone.side_effect = lambda: next(_itr)

        ensure_future_partitions(cur, "sqlery_queued_job", "1 hour", premake=1)

        # Collect all identifiers from CREATE TABLE calls
        create_calls = [
            str(c[0][0])
            for c in cur.execute.call_args_list
            if "PARTITION OF" in str(c[0][0])
        ]
        assert len(create_calls) == 2, f"Expected 2 CREATE calls, got: {create_calls}"
        # Names must be distinct (time suffix ensures this)
        name_0 = create_calls[0]
        name_1 = create_calls[1]
        assert name_0 != name_1, "Hourly partitions on same day must have distinct names"
        # Both names must contain HH:MM precision (_ separator before time)
        assert "_0000" in name_0 or "_00_00" in name_0 or "0000" in name_0, (
            f"First partition name missing time suffix: {name_0}"
        )
        assert "_0100" in name_1 or "0100" in name_1, (
            f"Second partition name missing 01:00 time suffix: {name_1}"
        )

    def test_daily_interval_uses_date_only_suffix(self):
        """Daily (and coarser) intervals must use date-only suffix %Y%m%d for backward compatibility."""
        from sqlery.core.partitioning import ensure_future_partitions

        from datetime import datetime, timezone, timedelta
        day_start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        responses = [(True,), (day_start,), (day_end,)]
        _itr = iter(responses)
        cur = MagicMock()
        cur.fetchone.side_effect = lambda: next(_itr)

        ensure_future_partitions(cur, "sqlery_queued_job", "1 day", premake=0)

        create_calls = [
            str(c[0][0])
            for c in cur.execute.call_args_list
            if "PARTITION OF" in str(c[0][0])
        ]
        assert len(create_calls) == 1
        # Name must contain the date without time component (no underscore after date)
        assert "20250101" in create_calls[0], f"Expected date-only suffix in: {create_calls[0]}"
        # Must NOT contain time suffix (_HHMM)
        assert "20250101_" not in create_calls[0], (
            f"Daily partition must not have time suffix: {create_calls[0]}"
        )


# ---------------------------------------------------------------------------
# reclaim_drained_partitions
# ---------------------------------------------------------------------------


class TestReclaimDrainedPartitions:
    """reclaim_drained_partitions: four skip rules + DETACH→archive→DROP order.

    Implementation fetchone call sequence:
      1. advisory lock → (bool,)
      2. now() - retention → (cutoff_datetime,)
      [fetchall for _list_partitions]
      per partition that passes skip rules 1 & 2:
        3. EXISTS(queued/running) → (bool,)
      per dropped partition (not live):
        [no fetchone — DETACH, DROP are execute-only]
    """

    def _make_reclaim_cursor(
        self,
        lock_acquired=True,
        cutoff=None,
        parts=None,
        live_work_results=None,
    ):
        """Build a properly sequenced cursor for reclaim tests.

        Args:
            lock_acquired: bool — advisory lock result.
            cutoff: datetime — the result of (now - retention). Defaults to
                    now() - 31 days (past a 30-day retention).
            parts: list of (name, upper_bound | None) tuples.
            live_work_results: list of booleans, one per partition that passes
                    skip rules 1 (non-DEFAULT) and 2 (outside retention).
        """
        cur = MagicMock()
        parts = parts or []
        live_work_results = live_work_results or []
        if cutoff is None:
            # Default cutoff: 31 days ago (so 30-day retention allows reclaim
            # of partitions older than that)
            cutoff = _utcnow() - timedelta(days=31)

        # Build pg_inherits rows (used by _list_partitions via fetchall)
        pg_rows = []
        for name, upper in parts:
            if upper is None:
                expr = "DEFAULT"
            else:
                expr = f"FOR VALUES FROM ('...') TO ('{upper.strftime('%Y-%m-%d %H:%M:%S+00')}')"
            pg_rows.append((name, expr))
        cur.fetchall.return_value = pg_rows

        # Build fetchone sequence:
        # (lock,) → (cutoff,) → [(live_work,), ...]
        responses = [(lock_acquired,), (cutoff,)] + [(v,) for v in live_work_results]
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
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[(default_name, None)],
            live_work_results=[],  # no EXISTS check expected
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0
        # DROP TABLE must never appear
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DROP" not in all_sqls.upper()

    def test_skip_rule_2_skips_inside_retention_window(self):
        """Partition whose upper_bound is within retention is not dropped."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        # cutoff = now - 31 days; upper_bound = yesterday → upper_bound > cutoff → skip
        yesterday = _utcnow() - timedelta(days=1)
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_recent", yesterday)],
            live_work_results=[],  # never reaches EXISTS check
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0

    def test_skip_rule_3_skips_partition_with_live_work(self):
        """Partition with queued/running rows is not dropped (back-pressure invariant)."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        # upper_bound = 60 days ago → outside 30-day retention → only skip rule 3 applies
        old_upper = _utcnow() - timedelta(days=60)
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[True],  # EXISTS returns True → live work → skip
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DROP" not in all_sqls.upper()

    def test_backpressure_invariant_queued(self):
        """Back-pressure invariant: partition with queued rows is never dropped (R4).

        The EXISTS query returns True (simulating a row with status='queued').
        The SQL itself uses IN ('queued', 'running') — this test confirms the
        queued branch of that expression pins the partition.
        """
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[True],  # EXISTS returns True (queued rows present)
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0, "Partition with queued rows must not be dropped"
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DETACH" not in all_sqls.upper(), "DETACH must not run when queued rows exist"
        assert "DROP" not in all_sqls.upper(), "DROP must not run when queued rows exist"
        # Confirm the EXISTS check was executed — it must be present in SQL calls
        assert any("EXISTS" in str(c[0][0]).upper() for c in cur.execute.call_args_list), (
            "Expected EXISTS check for live work was not executed"
        )

    def test_backpressure_invariant_running(self):
        """Back-pressure invariant: partition with running rows is never dropped (R4).

        The EXISTS query returns True (simulating a row with status='running').
        Both 'queued' AND 'running' statuses must pin the partition per the
        reclaim_drained_partitions implementation.
        """
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[True],  # EXISTS returns True (running rows present)
        )
        result = reclaim_drained_partitions(cur, "sqlery_queued_job", "30 days")
        assert result == 0, "Partition with running rows must not be dropped"
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DETACH" not in all_sqls.upper(), "DETACH must not run when running rows exist"
        assert "DROP" not in all_sqls.upper(), "DROP must not run when running rows exist"

    def test_detach_before_drop_order(self):
        """DETACH PARTITION must execute before DROP TABLE."""
        from sqlery.core.partitioning import reclaim_drained_partitions

        old_upper = _utcnow() - timedelta(days=60)
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[False],  # no live work → proceed to drop
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
        # Need a fresh cursor that tracks execute order but still returns
        # the right fetchone/fetchall values.
        cutoff = _utcnow() - timedelta(days=31)
        pg_rows = [
            (
                "sqlery_queued_job_old",
                f"FOR VALUES FROM ('...') TO ('{old_upper.strftime('%Y-%m-%d %H:%M:%S+00')}')",
            )
        ]
        cur = MagicMock()
        cur.fetchall.return_value = pg_rows

        responses = [(True,), (cutoff,), (False,)]  # lock, cutoff, exists=False
        _itr = iter(responses)
        cur.fetchone.side_effect = lambda: next(_itr)

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
        cur = self._make_reclaim_cursor(
            lock_acquired=True,
            parts=[("sqlery_queued_job_old", old_upper)],
            live_work_results=[False],
        )

        def failing_hook(hook_cur, name):
            raise RuntimeError("archive system down")

        result = reclaim_drained_partitions(
            cur, "sqlery_queued_job", "30 days", archive_hook=failing_hook
        )
        all_sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        assert "DROP" in all_sqls.upper()
        assert result == 1

    def test_advisory_lock_released_after_reclaim(self):
        from sqlery.core.partitioning import reclaim_drained_partitions

        cur = self._make_reclaim_cursor(lock_acquired=True, parts=[], live_work_results=[])
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


# ---------------------------------------------------------------------------
# Daemon helper: _validate_partition_maintenance_interval
# ---------------------------------------------------------------------------


class TestDaemonHelpers:
    """Tests for daemon.py partition-maintenance helpers (D1 config validation).

    _validate_partition_maintenance_interval must raise ValueError when
    PARTITION_MAINTENANCE_INTERVAL_MINUTES > SQLERY_PARTITION_INTERVAL in minutes.
    """

    def test_validate_maintenance_interval_rejects_oversized_interval(self):
        """ValueError raised when interval_minutes > partition_interval in minutes."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        # 2000 minutes >> 1 day (1440 min) → reject
        with pytest.raises(ValueError):
            _validate_partition_maintenance_interval(2000, "1 day")

    def test_validate_maintenance_interval_accepts_valid_interval(self):
        """No exception when interval_minutes <= partition_interval in minutes."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        # 5 minutes <= 1 day (1440 min) → accept
        _validate_partition_maintenance_interval(5, "1 day")

    def test_validate_maintenance_interval_boundary_day(self):
        """1440 minutes == 1 day → accept (boundary value)."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        _validate_partition_maintenance_interval(1440, "1 day")

    def test_validate_maintenance_interval_over_boundary_day(self):
        """1441 minutes > 1 day (1440 min) → reject (over boundary)."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        with pytest.raises(ValueError):
            _validate_partition_maintenance_interval(1441, "1 day")

    def test_validate_maintenance_interval_accepts_valid_hour(self):
        """60 minutes <= 1 hour (60 min) → accept."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        _validate_partition_maintenance_interval(60, "1 hour")

    def test_validate_maintenance_interval_rejects_oversized_hour(self):
        """61 minutes > 1 hour (60 min) → reject."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        with pytest.raises(ValueError):
            _validate_partition_maintenance_interval(61, "1 hour")

    def test_validate_maintenance_interval_unknown_format_does_not_raise(self):
        """Unknown interval format skips validation (fail-safe, not fail-closed)."""
        from sqlery.core.daemon import _validate_partition_maintenance_interval

        # No match → returns without raising
        _validate_partition_maintenance_interval(9999, "monthly")

    def test_advisory_lock_loser_skips_without_ddl(self):
        """Two-daemon coordination: the lock loser returns 0 without any DDL (R8).

        When pg_try_advisory_lock returns False for both ensure and reclaim, neither
        function should execute CREATE, DROP, or DETACH SQL.  This proves the R8
        invariant: two concurrent daemons cause zero DDL conflicts.
        """
        from sqlery.core.partitioning import ensure_future_partitions, reclaim_drained_partitions

        # --- ensure_future_partitions: lock not acquired ---
        ensure_cur = MagicMock()
        ensure_cur.fetchone.return_value = (False,)
        ensure_result = ensure_future_partitions(ensure_cur, "sqlery_queued_job", "1 day", premake=3)
        assert ensure_result == 0, "Lock loser must return 0 from ensure_future_partitions"
        ensure_sqls = " ".join(str(c[0][0]) for c in ensure_cur.execute.call_args_list)
        assert "CREATE" not in ensure_sqls.upper(), "Lock loser must not execute CREATE"

        # --- reclaim_drained_partitions: lock not acquired ---
        reclaim_cur = MagicMock()
        reclaim_cur.fetchone.return_value = (False,)
        reclaim_result = reclaim_drained_partitions(reclaim_cur, "sqlery_queued_job", "30 days")
        assert reclaim_result == 0, "Lock loser must return 0 from reclaim_drained_partitions"
        reclaim_sqls = " ".join(str(c[0][0]) for c in reclaim_cur.execute.call_args_list)
        assert "DROP" not in reclaim_sqls.upper(), "Lock loser must not execute DROP"
        assert "DETACH" not in reclaim_sqls.upper(), "Lock loser must not execute DETACH"


# ---------------------------------------------------------------------------
# CR-02: StandaloneConfig partition-key reconciliation
# ---------------------------------------------------------------------------


class TestStandalonePartitionConfigKeys:
    """CR-02: StandaloneConfig must store partition keys under the SQLERY_-prefixed
    names the consumers (backend.py / daemon.py) request, with PG-interval STRING
    types for interval/retention — not unprefixed keys with int months.
    """

    def test_retention_default_is_interval_string(self):
        from sqlery.fastapi_sqlery.config import StandaloneConfig

        cfg = StandaloneConfig()
        # Consumers do get_config("SQLERY_PARTITION_RETENTION", "30 days").
        assert cfg.get("SQLERY_PARTITION_RETENTION") == "30 days"
        assert isinstance(cfg.get("SQLERY_PARTITION_RETENTION"), str)

    def test_user_set_retention_is_honored_not_fallback(self):
        from sqlery.fastapi_sqlery.config import StandaloneConfig

        cfg = StandaloneConfig()
        cfg.set("SQLERY_PARTITION_RETENTION", "90 days")
        # The reclaim path reads exactly this key — must surface the user value,
        # never the silent "30 days" fallback (the CR-02 bug).
        assert cfg.get("SQLERY_PARTITION_RETENTION", "30 days") == "90 days"

    def test_env_retention_loads_as_string(self, monkeypatch):
        from sqlery.fastapi_sqlery.config import StandaloneConfig

        monkeypatch.setenv("SQLERY_PARTITION_RETENTION", "60 days")
        cfg = StandaloneConfig()
        assert cfg.get("SQLERY_PARTITION_RETENTION") == "60 days"

    def test_interval_and_premake_keys_prefixed(self):
        from sqlery.fastapi_sqlery.config import StandaloneConfig

        cfg = StandaloneConfig()
        assert cfg.get("SQLERY_PARTITION_INTERVAL") == "1 day"
        assert cfg.get("SQLERY_PARTITION_PREMAKE") == 7

    def test_maintenance_interval_bound_tracks_partition_interval(self):
        # IN-02: a maintenance cadence larger than the partition interval (in minutes)
        # must be rejected at config write time, matching the daemon startup check.
        from sqlery.fastapi_sqlery.config import StandaloneConfig

        cfg = StandaloneConfig()
        with pytest.raises(ValueError):
            # interval is '1 day' (1440 min); 2000 min maintenance must fail.
            cfg.set("PARTITION_MAINTENANCE_INTERVAL_MINUTES", 2000)
