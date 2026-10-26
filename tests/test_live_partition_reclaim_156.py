"""Issue #156: drained live-table partitions are dropped quickly and safely.

Part 1 states the pure decision (``decide_partition_reclaim``) and the health decision.
Part 2 runs the scenarios against a real PostgreSQL server in a throw-away database, using
raw psycopg only (no Django or SQLAlchemy), so the partition functions are exercised exactly as
the daemon calls them.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from psycopg import sql as pgsql

from sqlery.core import horizon
from sqlery.core.partitioning import (
    CHECK_ROWS,
    DROP,
    SKIP_DEFAULT,
    SKIP_GRACE,
    SKIP_HAS_ROWS,
    SKIP_LOCKED,
    TAKE_LOCK,
    decide_partition_reclaim,
    parse_interval,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
GRACE = timedelta(hours=1)


# --------------------------------------------------------------------------------------------
# Part 1: pure decisions
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "upper, has_rows, is_default, lock_ok, expected",
    [
        # the DEFAULT partition is never dropped, whatever else is true
        (None, False, True, True, SKIP_DEFAULT),
        (NOW - timedelta(days=9), False, True, True, SKIP_DEFAULT),
        (None, False, False, True, SKIP_DEFAULT),  # no upper bound: not a range partition
        # inside the grace period (and exactly at its edge)
        (NOW + timedelta(hours=1), False, False, True, SKIP_GRACE),  # a future partition
        (NOW - timedelta(minutes=59), False, False, True, SKIP_GRACE),
        (NOW - GRACE, False, False, True, SKIP_GRACE),
        # past grace: the next step depends on what is still unknown
        (NOW - timedelta(hours=2), None, False, None, CHECK_ROWS),
        (NOW - timedelta(hours=2), False, False, None, TAKE_LOCK),
        # any row pins the partition, even with the lock in hand
        (NOW - timedelta(hours=2), True, False, None, SKIP_HAS_ROWS),
        (NOW - timedelta(hours=2), True, False, True, SKIP_HAS_ROWS),
        # empty but not lockable at once: retried on the next tick
        (NOW - timedelta(hours=2), False, False, False, SKIP_LOCKED),
        # drained, past grace, lock held, still empty under the lock
        (NOW - timedelta(hours=2), False, False, True, DROP),
    ],
)
def test_reclaim_decision(upper, has_rows, is_default, lock_ok, expected):
    """A partition is dropped only when past grace, empty under the lock, and not DEFAULT."""
    assert decide_partition_reclaim(upper, NOW, GRACE, has_rows, is_default, lock_ok) == expected


def test_rows_found_under_the_lock_cancel_a_drop_that_looked_safe():
    """The re-check under the lock is final: a row that arrived in between wins."""
    upper = NOW - timedelta(hours=3)
    assert decide_partition_reclaim(upper, NOW, GRACE, False, False, None) == TAKE_LOCK
    assert decide_partition_reclaim(upper, NOW, GRACE, True, False, True) == SKIP_HAS_ROWS


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("1 hour", 3600),
        ("90 minutes", 5400),
        ("2 days", 172800),
        ("1 week", 604800),
        ("30 sec", 30),
        ("0.5 hour", 1800),
    ],
)
def test_interval_parsing_accepts_fixed_durations(text, seconds):
    assert parse_interval(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize(
    "text", ["", "soon", "1 month", "-1 hour", "0 hours", None, "1 hour; DROP"]
)
def test_interval_parsing_rejects_everything_else(text):
    assert parse_interval(text) is None


def test_stuck_partition_health_verdict():
    """Degraded only when a drained partition has been undroppable past the limit."""
    assert horizon.assess_live_partitions([], 900) == horizon.OK
    assert horizon.assess_live_partitions([60.0, 899.0], 900) == horizon.OK
    assert horizon.assess_live_partitions([60.0, 901.0], 900) == horizon.DEGRADED
    assert horizon.assess_live_partitions([10**6], 0) == horizon.OK  # 0 disables the limit


def test_overdue_candidates_exclude_default_future_and_within_grace():
    rows = [
        ("p_old", "FOR VALUES FROM ('2026-10-26 08:00:00+00') TO ('2026-10-26 09:00:00+00')", 0),
        ("p_rows", "FOR VALUES FROM ('2026-10-26 07:00:00+00') TO ('2026-10-26 08:00:00+00')", 3),
        ("p_grace", "FOR VALUES FROM ('2026-10-26 10:00:00+00') TO ('2026-10-26 11:30:00+00')", 0),
        ("p_future", "FOR VALUES FROM ('2026-10-26 13:00:00+00') TO ('2026-10-26 14:00:00+00')", 0),
        ("p_default", "DEFAULT", 0),
    ]
    overdue, total = horizon.live_partition_candidates(rows, GRACE, now=NOW)
    assert [name for name, _, _ in overdue] == ["p_rows", "p_old"]  # oldest first
    assert total == 2
    assert dict((n, round(a)) for n, a, _ in overdue)["p_old"] == 7200  # 12:00 - (09:00+1h)


def test_health_probe_never_raises():
    def broken(_fn):
        raise RuntimeError("database gone")

    result = horizon.check_live_partitions(lambda name, default: default, broken)
    assert result["status"] == horizon.UNKNOWN


# --------------------------------------------------------------------------------------------
# Part 2: PostgreSQL scenarios
# --------------------------------------------------------------------------------------------

PG_URL = os.environ.get("SQLERY_TEST_PG_URL", "")
_needs_pg = pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")


def pg(fn):
    """Mark a scenario as needing PostgreSQL (selected by ``-m postgres``)."""
    return pytest.mark.postgres(_needs_pg(fn))


TABLE = "sqlery_queued_job"


def _with_db(url: str, dbname: str) -> str:
    head, _, _ = url.rpartition("/")
    return f"{head}/{dbname}"


@pytest.fixture(scope="module")
def database():
    import psycopg

    name = "a156_" + uuid.uuid4().hex[:10]
    with psycopg.connect(PG_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield _with_db(PG_URL, name)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def live(database):
    """A partitioned live table with a DEFAULT partition; yields (url, connect)."""
    import psycopg

    opened = []

    def connect(autocommit=True):
        conn = psycopg.connect(database, autocommit=autocommit)
        opened.append(conn)
        return conn

    setup = connect()
    setup.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
    setup.execute(
        f"CREATE TABLE {TABLE} (id bigint GENERATED ALWAYS AS IDENTITY, status text NOT NULL,"
        " termination_reason text, created_at timestamptz NOT NULL DEFAULT now(),"
        " PRIMARY KEY (id, created_at)) PARTITION BY RANGE (created_at)"
    )
    setup.execute(f"CREATE TABLE {TABLE}_default PARTITION OF {TABLE} DEFAULT")
    try:
        yield database, connect
    finally:
        for conn in opened:
            try:
                conn.close()
            except Exception:
                pass


def _hour(hours_ago: int) -> datetime:
    """Start of the UTC hour ``hours_ago`` hours back."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now - timedelta(hours=hours_ago)


def _add_partition(conn, hours_ago: int, width_hours: int = 1) -> tuple[str, datetime]:
    lo = _hour(hours_ago)
    hi = lo + timedelta(hours=width_hours)
    name = f"{TABLE}_{lo:%Y%m%d_%H%M}"
    conn.execute(
        pgsql.SQL("CREATE TABLE {n} PARTITION OF {t} FOR VALUES FROM ({lo}) TO ({hi})").format(
            n=pgsql.Identifier(name),
            t=pgsql.Identifier(TABLE),
            lo=pgsql.Literal(lo),
            hi=pgsql.Literal(hi),
        )
    )
    return name, lo


def _partition_names(conn) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            f"WHERE i.inhparent = '{TABLE}'::regclass"
        )
    }


def _reclaim(conn, grace="1 hour") -> int:
    from sqlery.core import partitioning

    with conn.cursor() as cur:
        return partitioning.reclaim_drained_live_partitions(cur, TABLE, grace)


@pg
def test_a_drained_past_partition_is_dropped(live):
    """Empty, past its bound plus grace: dropped. Recent, future and DEFAULT stay."""
    _, connect = live
    conn = connect()
    old, _ = _add_partition(conn, 5)
    also_old, _ = _add_partition(conn, 3)
    within_grace, _ = _add_partition(conn, 1)  # ends at the top of this hour: inside grace
    future, _ = _add_partition(conn, -2)

    assert _reclaim(conn) == 2

    assert _partition_names(conn) == {f"{TABLE}_default", within_grace, future}
    assert old not in _partition_names(conn) and also_old not in _partition_names(conn)


@pg
@pytest.mark.parametrize(
    "status, reason",
    [
        ("queued", None),
        ("running", None),
        ("queued", "retry_pending:attempt 2"),
        ("success", "webhook_pending"),
        ("failed", None),  # any row at all pins it, not only the known live statuses
    ],
)
def test_a_partition_with_a_row_is_never_dropped(live, status, reason):
    """One row of any status keeps the partition (and the row) in place."""
    _, connect = live
    conn = connect()
    name, lo = _add_partition(conn, 5)
    conn.execute(
        f"INSERT INTO {TABLE} (status, termination_reason, created_at) VALUES (%s, %s, %s)",
        [status, reason, lo + timedelta(minutes=10)],
    )

    assert _reclaim(conn) == 0

    assert name in _partition_names(conn)
    assert conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 1


@pg
def test_the_default_partition_is_never_dropped_even_when_empty(live):
    _, connect = live
    conn = connect()
    _add_partition(conn, 5)
    assert _reclaim(conn) == 1
    assert f"{TABLE}_default" in _partition_names(conn)


@pg
def test_a_partition_read_by_an_open_repeatable_read_snapshot_is_skipped_without_waiting(live):
    """The reclaim does not wait for the old snapshot, and claims/enqueues are not blocked.

    A REPEATABLE READ transaction that read the partition holds ACCESS SHARE on it, so
    ACCESS EXCLUSIVE NOWAIT fails at once. Meanwhile an enqueue (insert) and a claim-style
    UPDATE on the parent proceed on the other partitions inside a 500 ms lock_timeout.
    """
    _, connect = live
    conn = connect()
    drained, _ = _add_partition(conn, 5)
    current, cur_lo = _add_partition(conn, 0)
    conn.execute(
        f"INSERT INTO {TABLE} (status, created_at) VALUES ('queued', %s)",
        [cur_lo + timedelta(minutes=1)],
    )

    old_snapshot = connect(autocommit=False)
    old_snapshot.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    old_snapshot.execute(f"SELECT count(*) FROM {TABLE}").fetchone()  # reads every partition

    started = time.monotonic()
    assert _reclaim(conn) == 0
    assert time.monotonic() - started < 2.0, "reclaim must not wait for the snapshot"
    assert drained in _partition_names(conn)

    worker = connect()
    worker.execute("SET lock_timeout = '500ms'")
    worker.execute(
        f"INSERT INTO {TABLE} (status, created_at) VALUES ('queued', %s)",
        [cur_lo + timedelta(minutes=2)],
    )
    claimed = worker.execute(
        f"UPDATE {TABLE} SET status = 'running' WHERE id IN "
        f"(SELECT id FROM {TABLE} WHERE status = 'queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    ).rowcount
    assert claimed == 1

    # Once the snapshot ends the very next tick succeeds.
    old_snapshot.rollback()
    assert _reclaim(conn) == 1
    assert drained not in _partition_names(conn) and current in _partition_names(conn)


@pg
def test_a_pending_insert_into_the_partition_blocks_the_drop(live):
    """An uncommitted insert (a backfill with a past created_at) holds the partition: the
    drop is skipped, the insert commits, and the row is there afterwards."""
    _, connect = live
    conn = connect()
    name, lo = _add_partition(conn, 5)

    writer = connect(autocommit=False)
    writer.execute(
        f"INSERT INTO {TABLE} (status, created_at) VALUES ('queued', %s)",
        [lo + timedelta(minutes=5)],
    )
    assert _reclaim(conn) == 0
    writer.commit()

    assert _reclaim(conn) == 0  # now the row itself pins it
    assert conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 1


@pg
def test_an_insert_racing_the_drop_is_never_lost(live):
    """Inserts into a draining partition race the reclaim. Each insert either committed (and
    then the row exists somewhere in the table) or failed with a database error; none vanish."""
    import psycopg

    _, connect = live
    conn = connect()
    committed, failed = 0, 0

    for round_no in range(25):
        name, lo = _add_partition(conn, 5)
        ts = lo + timedelta(minutes=round_no % 50)
        marker = f"race{round_no}"
        outcome: dict = {}
        barrier = threading.Barrier(2)

        def insert():
            c = connect()
            barrier.wait()
            try:
                c.execute(
                    f"INSERT INTO {TABLE} (status, termination_reason, created_at)"
                    " VALUES ('queued', %s, %s)",
                    [marker, ts],
                )
                outcome["ok"] = True
            except psycopg.Error as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=insert)
        thread.start()
        barrier.wait()
        _reclaim(conn)
        thread.join(10)
        assert not thread.is_alive()

        present = conn.execute(
            f"SELECT count(*) FROM {TABLE} WHERE termination_reason = %s", [marker]
        ).fetchone()[0]
        if outcome.get("ok"):
            committed += 1
            assert present == 1, f"round {round_no}: a committed insert was lost"
        else:
            failed += 1
            assert "error" in outcome and present == 0
        # Reset for the next round: remove the partition if it is still there, keep the rows.
        if name in _partition_names(conn):
            conn.execute(f"DELETE FROM {TABLE} WHERE termination_reason = %s", [marker])
            conn.execute(f"ALTER TABLE {TABLE} DETACH PARTITION {name}")
            conn.execute(f"DROP TABLE {name}")
        conn.execute(f"DELETE FROM {TABLE} WHERE termination_reason = %s", [marker])

    assert committed + failed == 25


@pg
def test_reclaim_is_single_flight_through_the_advisory_lock(live):
    from sqlery.core import partitioning

    _, connect = live
    conn, other = connect(), connect()
    name, _ = _add_partition(conn, 5)
    other.execute("SELECT pg_advisory_lock(%s)", [partitioning.ADVISORY_LOCK_RECLAIM])
    assert _reclaim(conn) == 0 and name in _partition_names(conn)
    other.execute("SELECT pg_advisory_unlock(%s)", [partitioning.ADVISORY_LOCK_RECLAIM])
    assert _reclaim(conn) == 1


@pg
def test_reclaim_works_on_a_non_autocommit_connection(live):
    """The standalone backend's raw connection is not in autocommit mode."""
    _, connect = live
    conn = connect()
    name, _ = _add_partition(conn, 5)
    plain = connect(autocommit=False)
    plain.execute("SELECT 1")  # leaves an implicit transaction open, as pending DDL would
    assert _reclaim(plain) == 1
    plain.commit()
    assert name not in _partition_names(conn)


def _ranges(conn):
    from sqlery.core import partitioning

    return sorted(partitioning._list_partition_ranges(conn.cursor(), TABLE))


@pg
def test_ensure_keeps_a_day_ahead_and_is_idempotent(live):
    from sqlery.core import partitioning

    _, connect = live
    conn = connect()
    with conn.cursor() as cur:
        made = partitioning.ensure_live_partitions(cur, TABLE, "1 hour", 7)
        again = partitioning.ensure_live_partitions(cur, TABLE, "1 hour", 7)
    ranges = _ranges(conn)
    now = datetime.now(timezone.utc)
    assert made >= 24 and again == 0
    assert ranges[0][0] <= now < ranges[0][1]
    assert ranges[-1][1] >= now + timedelta(days=1)
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])), "gap or overlap"


@pg
def test_ensure_handles_mixed_widths_without_overlap_or_gap(live):
    """Existing daily partitions (older installs, migration 0030) stay; new hourly ones continue
    from where they end, even when that boundary is not on the hour."""
    from sqlery.core import partitioning

    _, connect = live
    conn = connect()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    upper = today + timedelta(days=1, minutes=30)  # off-grid upper bound
    conn.execute(
        pgsql.SQL("CREATE TABLE {n} PARTITION OF {t} FOR VALUES FROM ({lo}) TO ({hi})").format(
            n=pgsql.Identifier(TABLE + "_daily"),
            t=pgsql.Identifier(TABLE),
            lo=pgsql.Literal(today),
            hi=pgsql.Literal(upper),
        )
    )
    with conn.cursor() as cur:
        partitioning.ensure_live_partitions(cur, TABLE, "1 hour", 7)
        # A narrower and a wider setting later on must be just as safe.
        partitioning.ensure_live_partitions(cur, TABLE, "30 minutes", 7)
        partitioning.ensure_live_partitions(cur, TABLE, "1 day", 2)
    ranges = _ranges(conn)
    assert ranges[0] == (today, today + timedelta(days=1, minutes=30))
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])), ranges
    assert ranges[-1][1] >= datetime.now(timezone.utc) + timedelta(days=2)
    assert conn.execute(f"SELECT count(*) FROM {TABLE}_default").fetchone()[0] == 0


@pg
def test_ensure_with_an_invalid_interval_falls_back_instead_of_raising(live):
    from sqlery.core import partitioning

    _, connect = live
    conn = connect()
    with conn.cursor() as cur:
        assert partitioning.ensure_live_partitions(cur, TABLE, "soon", 7) >= 24


def _runner(connect):
    """A ``run_in_txn`` for ``check_live_partitions`` backed by plain psycopg connections."""

    def run_in_txn(fn):
        conn = connect(autocommit=False)
        try:
            conn.execute("SET LOCAL statement_timeout = '3000'")
            return fn(lambda sql: conn.execute(sql).fetchall())
        finally:
            conn.rollback()
            conn.close()

    return run_in_txn


@pg
def test_health_reports_a_drained_partition_that_cannot_be_dropped(live):
    """A drained partition kept alive by an old snapshot is reported, with its age, and
    degrades health past the limit; a partition that still has rows is not 'drained'."""
    _, connect = live
    conn = connect()
    drained, _ = _add_partition(conn, 6)
    pinned, lo = _add_partition(conn, 5)
    conn.execute(
        f"INSERT INTO {TABLE} (status, created_at) VALUES ('queued', %s)",
        [lo + timedelta(minutes=1)],
    )
    conn.execute(f"ANALYZE {pinned}")
    snapshot = connect(autocommit=False)
    snapshot.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    snapshot.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    assert _reclaim(conn) == 0

    def settings(limit):
        return lambda name, default: {
            "PG_LIVE_PARTITION_STUCK_DEGRADED_SECONDS": limit,
            "SQLERY_LIVE_PARTITION_GRACE": "1 hour",
        }.get(name, default)

    degraded = horizon.check_live_partitions(settings(900), _runner(connect))
    assert degraded["status"] == horizon.DEGRADED
    assert degraded["drained_undroppable"] == 1
    assert degraded["overdue_with_rows"] == 1
    assert degraded["oldest_drained_age_seconds"] > 3600 * 4  # 6h-old hour + 1h grace

    assert horizon.check_live_partitions(settings(0), _runner(connect))["status"] == horizon.OK

    snapshot.rollback()
    assert _reclaim(conn) == 1  # only the drained one goes
    healthy = horizon.check_live_partitions(settings(900), _runner(connect))
    assert healthy["status"] == horizon.OK and healthy["drained_undroppable"] == 0
    assert drained not in _partition_names(conn) and pinned in _partition_names(conn)
