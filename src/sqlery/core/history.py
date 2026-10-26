"""Terminal jobs: insert-only history (issue #140).

A job that reaches a terminal state (``success``, ``failed`` or ``archived``) leaves
``sqlery_queued_job`` in the same transaction that records the outcome::

    INSERT INTO sqlery_job_history SELECT ... FROM sqlery_queued_job WHERE <job> AND version=:v
    DELETE FROM sqlery_queued_job WHERE <job> AND version=:v

Both statements must touch exactly one row, otherwise the transaction is rolled back and the
completion is stale (same fencing as the attempt close, see ``sqlery.core.attempts``). After
that the live table holds only ``queued`` / ``running`` rows (plus the short transient states
listed below), so its churn no longer depends on how many jobs have ever finished, and
history rows are never updated: only retention removes them.

* ``sqlery_job_history`` has every column of ``sqlery_queued_job``; primary key
  ``(finished_at, id)``. On PostgreSQL it is ``PARTITION BY RANGE (finished_at)`` (like the
  live table), so retention is a ``DROP`` of whole partitions; elsewhere it is a plain table
  and retention is a batched ``DELETE``.
* ``sqlery_job_all`` is a read-only view, the union of the live table and history. Every
  reader that must see a job whatever its state (dashboards, lists, counts, statistics,
  dependency checks, retry chains) reads the view or both tables.

Transient states that stay in the live table until they settle (a sweep, run with the ready
queue maintenance, moves strays into history):

* ``failed`` with a ``retry_pending`` marker (see ``sqlery.core.retry``): the retry has not been
  created yet, so the chain is not complete. It leaves when the retry exists (as ``archived``)
  or when no retry is owed.
* a finished job whose webhook delivery is still being attempted.

This module holds the framework-agnostic parts: names, DDL, SQL text and the decisions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..tables import JOB_ALL, JOB_ATTEMPT, JOB_HISTORY, QUEUED_JOB

HISTORY_TABLE = JOB_HISTORY
JOB_ALL_VIEW = JOB_ALL
LIVE_TABLE = QUEUED_JOB
ATTEMPT_TABLE = JOB_ATTEMPT

TERMINAL_STATUSES = ("success", "failed", "archived")

# Rows of the live table that retention must never drop with a partition: work that is not
# finished, or finished but not yet settled into history.
LIVE_ROW_PREDICATE = (
    "status IN ('queued', 'running', 'shutting_down') OR termination_reason LIKE 'retry_pending%'"
)
OPEN_ATTEMPT_PREDICATE = "finished_at IS NULL"

HISTORY_ID_INDEX = "sqlery_hist_id_idx"
HISTORY_PARENT_INDEX = "sqlery_hist_parent_idx"
HISTORY_STATUS_INDEX = "sqlery_hist_status_idx"
HISTORY_NAME_INDEX = "sqlery_hist_name_idx"
HISTORY_CREATED_INDEX = "sqlery_hist_created_idx"

# (name, columns/expressions, optional partial-index predicate)
HISTORY_INDEXES = (
    (HISTORY_ID_INDEX, "id", None),
    (HISTORY_PARENT_INDEX, "parent_job_id", "parent_job_id IS NOT NULL"),
    (HISTORY_STATUS_INDEX, "status, finished_at DESC", None),
    (HISTORY_NAME_INDEX, "job_name", "job_name IS NOT NULL"),
    (HISTORY_CREATED_INDEX, "created_at DESC", None),
)

MISSING_TABLE_MESSAGE = (
    f"The job history table '{HISTORY_TABLE}' does not exist, so this worker refuses to start: "
    "finished jobs could not be recorded. Run the database migrations (Django: 'python "
    "manage.py migrate sqlery'; standalone: 'sqlery-migrate' / 'alembic upgrade head'). Stop all "
    "running workers before migrating: a worker started before the table existed keeps "
    "finishing jobs in the live table."
)


class HistoryTableMissingError(RuntimeError):
    """The history table is missing: migrations have not been applied."""


def require_history_table(backend) -> None:
    """Refuse to start a worker on a database that has no history table."""
    check = getattr(backend, "history_table_exists", None)
    if check is None:
        return
    if not check():
        raise HistoryTableMissingError(MISSING_TABLE_MESSAGE)


# --------------------------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------------------------


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL_STATUSES


def settled_termination_reason(reason: str | None) -> str:
    """The reason a job carries into history: the retry marker is cleared (the chain is
    complete or no retry is owed), the original reason is kept."""
    from .retry import settled_reason

    return settled_reason(reason or "")


def settle_grace_seconds(get_config) -> float:
    """Seconds a finished job may sit in the live table before the settle sweep moves it
    (``HISTORY_SETTLE_GRACE_SECONDS``, default 60): long enough for any in-flight completion
    (a retry about to be queued, a webhook being delivered) to finish by itself."""
    try:
        return max(0.0, float(get_config("HISTORY_SETTLE_GRACE_SECONDS", 60)))
    except (TypeError, ValueError):
        return 60.0


def settle_status(status: str, reason: str | None, has_retry: bool) -> str | None:
    """What a stray terminal row in the live table becomes when it is settled.

    Returns the status it enters history with, or None to leave it (still transient):

    * ``success`` / ``archived`` -> unchanged
    * ``failed`` still owing a retry (marker) -> None
    * ``failed`` with a retry child -> ``archived`` (as the retry bookkeeping would have set)
    * ``failed`` otherwise -> unchanged
    """
    from .retry import is_retry_pending

    if status in ("success", "archived"):
        return status
    if status != "failed":
        return None
    if is_retry_pending(reason):
        return None
    return "archived" if has_retry else "failed"


# --------------------------------------------------------------------------------------------
# SQL text (positional parameters are the caller's business; only identifiers here)
# --------------------------------------------------------------------------------------------


def insert_select_sql(columns: Iterable[str], overrides: Mapping[str, str], where_sql: str) -> str:
    """``INSERT INTO history (cols) SELECT <col or override expression> FROM live WHERE ..``."""
    cols = list(columns)
    select_list = ", ".join(overrides.get(c, c) for c in cols)
    return (
        f"INSERT INTO {HISTORY_TABLE} ({', '.join(cols)}) "
        f"SELECT {select_list} FROM {LIVE_TABLE} WHERE {where_sql}"
    )


def delete_live_sql(where_sql: str) -> str:
    return f"DELETE FROM {LIVE_TABLE} WHERE {where_sql}"


# Columns of a live job that a running job takes from its open attempt (#157). The attempt is
# the only place the claim writes: while a job runs its row is untouched, so the view derives
# these from the open attempt (``a``) and leaves every other column as stored.
_RUNNING_FROM_ATTEMPT = {
    "status": "'running'",
    "started_at": "a.claimed_at",
    "worker_id": "a.worker_id",
    "worker_pid": "a.worker_pid",
    "child_pid": "a.child_pid",
}


def view_sql(columns: Iterable[str], replace: bool = True, with_attempts: bool = False) -> str:
    """The union view over the live table and history (``columns`` are the shared ones).

    With ``with_attempts`` (what #157 installs; the default stays the shape the released
    migrations 0039 / 0021 create) a live job with ``status = 'queued'`` and an
    open attempt reads as ``running``, with ``started_at`` / ``worker_id`` / ``worker_pid`` /
    ``child_pid`` taken from that attempt. A job that is claimed has no UPDATE on its row, so
    this view is where "running" exists for every reader of "any job". Without it the view is
    the plain union (the pre-#157 shape, used by the older migrations and their reverse).
    """
    cols = list(columns)
    head = "CREATE OR REPLACE VIEW" if replace else "CREATE VIEW"
    plain = ", ".join(cols)
    if not with_attempts:
        return (
            f"{head} {JOB_ALL_VIEW} AS SELECT {plain} FROM {LIVE_TABLE} "
            f"UNION ALL SELECT {plain} FROM {HISTORY_TABLE}"
        )
    live = ", ".join(
        f"CASE WHEN a.id IS NULL THEN q.{c} ELSE {_RUNNING_FROM_ATTEMPT[c]} END AS {c}"
        if c in _RUNNING_FROM_ATTEMPT
        else f"q.{c} AS {c}"
        for c in cols
    )
    return (
        f"{head} {JOB_ALL_VIEW} AS SELECT {live} FROM {LIVE_TABLE} q "
        f"LEFT JOIN {ATTEMPT_TABLE} a ON a.job_id = q.id AND a.finished_at IS NULL "
        f"AND q.status = 'queued' "
        f"UNION ALL SELECT {plain} FROM {HISTORY_TABLE}"
    )


#: Live-row columns a finished job takes from its newest attempt (#157): the claim does not
#: write them to the job row, so the terminal transition copies them from the attempt table.
FROM_ATTEMPT = (
    ("started_at", "claimed_at"),
    ("worker_id", "worker_id"),
    ("worker_pid", "worker_pid"),
    ("child_pid", "child_pid"),
)


def from_attempt_sql(column: str, attempt_column: str, job_id_sql: str = f"{LIVE_TABLE}.id") -> str:
    """SQL expression: ``attempt_column`` of the newest attempt of the job, else the row's own
    ``column`` (a job claimed before attempts existed keeps what its row says)."""
    return (
        f"COALESCE((SELECT a.{attempt_column} FROM {ATTEMPT_TABLE} a "
        f"WHERE a.job_id = {job_id_sql} ORDER BY a.id DESC LIMIT 1), {column})"
    )


def backfill_history_sql(columns: Iterable[str], batch: int | None = None) -> str:
    """Data migration, one batch: copy terminal live rows to history.

    ``finished_at`` is the partition key and must not be NULL: rows that never recorded it
    (cancelled before they ran) get ``COALESCE(finished_at, started_at, created_at)``.
    Idempotent (``ON CONFLICT DO NOTHING`` on the history key, understood by PostgreSQL and
    SQLite >= 3.24); the matching delete is :func:`backfill_delete_sql`.
    """
    cols = list(columns)
    select_list = ", ".join(
        "COALESCE(finished_at, started_at, created_at)" if c == "finished_at" else c for c in cols
    )
    return (
        f"INSERT INTO {HISTORY_TABLE} ({', '.join(cols)}) SELECT {select_list} "
        f"FROM {LIVE_TABLE} WHERE {_backfill_where(batch)} ON CONFLICT DO NOTHING"
    )


def backfill_delete_sql(batch: int | None = None) -> str:
    return (
        f"DELETE FROM {LIVE_TABLE} WHERE {_backfill_where(batch)} "
        f"AND EXISTS (SELECT 1 FROM {HISTORY_TABLE} h WHERE h.id = {LIVE_TABLE}.id)"
    )


def _backfill_where(batch: int | None) -> str:
    terminal = (
        "status IN ('success', 'failed', 'archived') "
        "AND COALESCE(termination_reason, '') NOT LIKE 'retry_pending%'"
    )
    if batch is None:
        return terminal
    return (
        f"{terminal} AND id IN (SELECT id FROM {LIVE_TABLE} WHERE {terminal} "
        f"ORDER BY created_at, id LIMIT {int(batch)})"
    )


def restore_live_sql(columns: Iterable[str], batch: int) -> tuple[str, str]:
    """Reverse data migration, one batch: history rows back into the live table.

    Returns ``(insert_sql, delete_sql)``. The ``id IN (SELECT .. LIMIT)`` picks the same rows
    for both statements because the delete only removes rows already present in the live table.
    """
    cols = ", ".join(columns)
    pick = (
        f"SELECT id FROM {HISTORY_TABLE} h WHERE NOT EXISTS "
        f"(SELECT 1 FROM {LIVE_TABLE} q WHERE q.id = h.id) ORDER BY finished_at, id "
        f"LIMIT {int(batch)}"
    )
    insert = (
        f"INSERT INTO {LIVE_TABLE} ({cols}) SELECT {cols} FROM {HISTORY_TABLE} "
        f"WHERE id IN ({pick}) ON CONFLICT DO NOTHING"
    )
    delete = (
        f"DELETE FROM {HISTORY_TABLE} WHERE EXISTS "
        f"(SELECT 1 FROM {LIVE_TABLE} q WHERE q.id = {HISTORY_TABLE}.id)"
    )
    return insert, delete


# --------------------------------------------------------------------------------------------
# DDL (shared by the Alembic revision, the standalone bootstrap and the Django migration)
# --------------------------------------------------------------------------------------------


def history_index_ddl() -> list[str]:
    out = []
    for name, cols, where in HISTORY_INDEXES:
        stmt = f"CREATE INDEX IF NOT EXISTS {name} ON {HISTORY_TABLE} ({cols})"
        if where:
            stmt += f" WHERE {where}"
        out.append(stmt)
    return out


def postgres_history_ddl(partitioned: bool = True, indexes: bool = True) -> list[str]:
    """PostgreSQL DDL for the history table: same column types as the live table
    (``LIKE``), primary key ``(finished_at, id)``, ``PARTITION BY RANGE (finished_at)`` when
    the live table is partitioned, plus its DEFAULT partition."""
    suffix = " PARTITION BY RANGE (finished_at)" if partitioned else ""
    statements = [
        f"CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} "
        f"(LIKE {LIVE_TABLE} INCLUDING STORAGE, PRIMARY KEY (finished_at, id)){suffix}"
    ]
    if partitioned:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {HISTORY_TABLE}_default "
            f"PARTITION OF {HISTORY_TABLE} DEFAULT"
        )
    if indexes:
        statements.extend(history_index_ddl())
    return statements


def view_columns_ddl(
    columns: Iterable[str], dialect: str, with_attempts: bool = False
) -> list[str]:
    """DDL of the union view for ``dialect``: always drop and create, in the caller's
    transaction. ``CREATE OR REPLACE`` is not used because the status column's type modifier
    differs between the two shapes of the view, which PostgreSQL refuses to replace."""
    return [
        f"DROP VIEW IF EXISTS {JOB_ALL_VIEW}",
        view_sql(columns, replace=False, with_attempts=with_attempts),
    ]


def drop_view_ddl() -> str:
    return f"DROP VIEW IF EXISTS {JOB_ALL_VIEW}"


ATTEMPT_ID_SEQUENCE = "sqlery_attempt_id_seq"
#: Tiny partial index of the open attempts by job: "is this job running" and "which jobs are
#: running" (#157) read it instead of the whole attempt table.
ATTEMPT_OPEN_JOB_INDEX = "sqlery_attempt_open_job_idx"
ATTEMPT_OUTCOME_CHECK = (
    "CONSTRAINT sqlery_attempt_outcome_chk "
    "CHECK (outcome IS NULL OR outcome IN ('success', 'failed', 'abandoned'))"
)


def postgres_attempt_partitioned_ddl(table: str = ATTEMPT_TABLE) -> list[str]:
    """PostgreSQL DDL for ``sqlery_job_attempt`` partitioned by ``claimed_at``.

    The primary key must contain the partition key: ``(claimed_at, id)``; the fencing
    compare-and-set still addresses an attempt by ``(id, token)`` through the ``id`` index.
    Retention drops whole partitions (only those without an open attempt).
    """
    return [
        f"CREATE SEQUENCE IF NOT EXISTS {ATTEMPT_ID_SEQUENCE}",
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id          BIGINT       NOT NULL DEFAULT nextval('{ATTEMPT_ID_SEQUENCE}'),
            job_id      BIGINT       NOT NULL,
            token       VARCHAR(36)  NOT NULL,
            worker_id   UUID,
            worker_pid  INTEGER,
            child_pid   INTEGER,
            claimed_at  TIMESTAMPTZ  NOT NULL,
            finished_at TIMESTAMPTZ,
            outcome     VARCHAR(10),
            PRIMARY KEY (claimed_at, id),
            {ATTEMPT_OUTCOME_CHECK}
        ) PARTITION BY RANGE (claimed_at)
        """,
        f"CREATE TABLE IF NOT EXISTS {table}_default PARTITION OF {table} DEFAULT",
        f"CREATE INDEX IF NOT EXISTS sqlery_attempt_id_idx ON {table} (id)",
        f"CREATE INDEX IF NOT EXISTS sqlery_attempt_job_idx ON {table} (job_id)",
        f"CREATE INDEX IF NOT EXISTS sqlery_attempt_open_worker_idx ON {table} (worker_id) "
        f"WHERE finished_at IS NULL",
    ]


def normalize_running_sql(dialect: str) -> list[str]:
    """Data migration to the attempt-derived running state (#157), idempotent.

    Rows stored as ``status = 'running'`` by the older claim become ``queued`` rows with an open
    attempt (every one of them already has one since #138; a row claimed before attempts
    existed gets one built from the columns the old claim wrote). Run in the transaction that
    replaces the ``sqlery_job_all`` view, with the workers stopped.
    """
    token = "lower(hex(randomblob(16)))" if dialect == "sqlite" else "md5(random()::text || clock_timestamp()::text)"
    return [
        f"INSERT INTO {ATTEMPT_TABLE} (token, job_id, worker_id, worker_pid, child_pid, claimed_at) "
        f"SELECT {token}, q.id, q.worker_id, q.worker_pid, q.child_pid, "
        f"COALESCE(q.started_at, q.created_at) FROM {LIVE_TABLE} q "
        f"WHERE q.status = 'running' AND NOT EXISTS ("
        f"SELECT 1 FROM {ATTEMPT_TABLE} a WHERE a.job_id = q.id AND a.finished_at IS NULL)",
        f"UPDATE {LIVE_TABLE} SET status = 'queued' WHERE status = 'running'",
    ]


def denormalize_running_sql() -> list[str]:
    """Reverse of :func:`normalize_running_sql`: a queued row with an open attempt is written
    back as the ``running`` row the older claim produced (start, worker and PIDs copied from
    the attempt). The attempts stay."""

    def newest(column: str) -> str:
        return (
            f"(SELECT a.{column} FROM {ATTEMPT_TABLE} a WHERE a.job_id = {LIVE_TABLE}.id "
            f"AND a.finished_at IS NULL ORDER BY a.id DESC LIMIT 1)"
        )

    return [
        f"UPDATE {LIVE_TABLE} SET status = 'running', started_at = {newest('claimed_at')}, "
        f"worker_id = {newest('worker_id')}, worker_pid = {newest('worker_pid')}, "
        f"child_pid = {newest('child_pid')} WHERE status = 'queued' AND EXISTS ("
        f"SELECT 1 FROM {ATTEMPT_TABLE} a WHERE a.job_id = {LIVE_TABLE}.id AND a.finished_at IS NULL)"
    ]


def open_job_index_ddl(table: str = ATTEMPT_TABLE) -> str:
    """``CREATE INDEX`` of the open-attempts-by-job partial index (PostgreSQL and SQLite)."""
    return (
        f"CREATE INDEX IF NOT EXISTS {ATTEMPT_OPEN_JOB_INDEX} ON {table} (job_id) "
        f"WHERE finished_at IS NULL"
    )


# --------------------------------------------------------------------------------------------
# Partition maintenance of the three time-partitioned tables (PostgreSQL)
# --------------------------------------------------------------------------------------------


def partitioned_tables(cur, partitioning=None) -> list[tuple[str, str | None]]:
    """``[(table, live_predicate)]`` of the job tables that are currently partitioned.

    * the live job table (partitioned by ``created_at``): dropped when it holds no work;
    * the history (by ``finished_at``): insert-only, so no live work protects a partition;
    * the attempt table (by ``claimed_at``): protected while an attempt is open.

    ``partitioning`` is the ``sqlery.core.partitioning`` module (callers pass the one they
    imported, so tests can substitute it).
    """
    if partitioning is None:
        from . import partitioning

    tables: list[tuple[str, str | None]] = []
    for table, predicate in (
        (LIVE_TABLE, LIVE_ROW_PREDICATE),
        (HISTORY_TABLE, None),
        (ATTEMPT_TABLE, OPEN_ATTEMPT_PREDICATE),
    ):
        if partitioning.is_partitioned(cur, table):
            tables.append((table, predicate))
    return tables


def ensure_partitions(cur, interval_str: str, premake: int, partitioning=None) -> int:
    """Create the upcoming partitions of every partitioned job table; returns how many."""
    if partitioning is None:
        from . import partitioning

    return sum(
        partitioning.ensure_future_partitions(cur, table, interval_str, premake)
        for table, _ in partitioned_tables(cur, partitioning)
    )


def reclaim_partitions(cur, retention_str: str, archive_hook=None, partitioning=None) -> dict:
    """Drop every partition past retention from the history, the live table and the attempt
    table (``{table: dropped}``). The archive hook sees history partitions only: that is
    where finished jobs are."""
    if partitioning is None:
        from . import partitioning

    dropped: dict = {}
    for table, predicate in partitioned_tables(cur, partitioning):
        dropped[table] = partitioning.reclaim_drained_partitions(
            cur,
            table,
            retention_str,
            archive_hook if table == HISTORY_TABLE else None,
            live_predicate=predicate,
        )
    return dropped


# --------------------------------------------------------------------------------------------
# Data migration helpers
# --------------------------------------------------------------------------------------------

PREMAKE_DAYS = 7


def daily_partition_ddl(table: str, first_day, last_day_exclusive) -> list[str]:
    """``CREATE TABLE .. PARTITION OF`` statements for each UTC day in ``[first, last)``.

    Names and bounds are derived from ``datetime.date`` objects (digits and dashes only), so
    interpolation is safe. Same naming as ``partitioning.ensure_future_partitions``
    (``<table>_YYYYMMDD``), which keeps those partitions and creates the following ones.
    """
    from datetime import timedelta

    statements = []
    day = first_day
    while day < last_day_exclusive:
        nxt = day + timedelta(days=1)
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {table}_{day.strftime('%Y%m%d')} "
            f"PARTITION OF {table} "
            f"FOR VALUES FROM ('{day.isoformat()} 00:00:00+00') "
            f"TO ('{nxt.isoformat()} 00:00:00+00')"
        )
        day = nxt
    return statements


def move_terminal_rows(run, columns, batch: int = 1000, commit=None) -> int:
    """Data migration: move terminal rows of the live table into history, in batches.

    ``run(sql) -> rowcount`` executes one statement; ``commit()`` (optional) is called after
    each batch so a long migration holds no long transaction. Idempotent and resumable: a
    batch that was copied but not yet deleted is finished by the next one.
    """
    moved = 0
    while True:
        inserted = run(backfill_history_sql(columns, batch)) or 0
        removed = run(backfill_delete_sql(batch)) or 0
        if commit is not None:
            commit()
        moved += removed
        if inserted <= 0 and removed <= 0:
            return moved


def restore_terminal_rows(run, columns, batch: int = 1000, commit=None) -> int:
    """Reverse data migration: history rows back into the live table, in batches."""
    restored = 0
    insert_sql, delete_sql = restore_live_sql(columns, batch)
    while True:
        inserted = run(insert_sql) or 0
        removed = run(delete_sql) or 0
        if commit is not None:
            commit()
        restored += removed
        if inserted <= 0 and removed <= 0:
            return restored
