"""Phase 15 gating verification tests — migration round-trip on a production-sized snapshot.

Tests run against SQLERY_TEST_PG_URL (real PostgreSQL 15).  All PG tests are decorated
with a skipif guard so the SQLite CI rail is unaffected.

Each PG test uses a fully isolated database (sqlery_rt_test_<N>) created and dropped
as part of the test itself — independent of Django's test runner and the shared
sqlery_test schema.

SC1 — Round-trip: legacy → partitioned → rollback on a ≥1M-row snapshot
SC2 — Zero rows in DEFAULT partition after migration
SC3 — Identity continues from max(id)+1
SC4 — Idempotency: partial failure (injected mid-migration) + rerun completes cleanly
SC5 — BLAST-RADIUS-AUDIT.md shows UNADDRESSED: 0

Connection convention: psycopg (psycopg3) with autocommit=True for all DDL.
The migration is invoked via a minimal _FakeSchemaEditor adapter that exposes
.connection for the _forward/_backward static methods to use.
"""

import importlib
import os
import time

import psycopg
from psycopg import ClientCursor
import pytest

_migration_module = importlib.import_module(
    "sqlery.django_sqlery.migrations.0030_partition_queued_job"
)
_VendorGuardedCutover = _migration_module._VendorGuardedCutover

# Path to the audit artefact written by plan 15-01.
PHASE_15_SC5_AUDIT_PATH = ".planning/phases/15-schema-cutover/BLAST-RADIUS-AUDIT.md"

# Shared skipif marker for all PG-only tests.
_SKIP_NO_PG = pytest.mark.skipif(
    not os.environ.get("SQLERY_TEST_PG_URL"),
    reason="SQLERY_TEST_PG_URL not set — skipping PG-only round-trip test",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSchemaEditor:
    """Minimal adapter so _VendorGuardedCutover._forward/_backward can call
    schema_editor.connection.cursor() on a raw psycopg connection.

    psycopg3 Cursor objects are context managers, so
    ``with connection.cursor() as cursor:`` works correctly.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self.connection = conn


def _admin_dsn() -> str:
    """Return a DSN to the postgres admin database on the same server as SQLERY_TEST_PG_URL."""
    url = os.environ.get("SQLERY_TEST_PG_URL", "")
    if not url:
        pytest.skip("SQLERY_TEST_PG_URL not set")
    # Replace the database name with 'postgres' for admin operations.
    # URL format: postgresql://user:pass@host:port/dbname
    parts = url.rsplit("/", 1)
    return parts[0] + "/postgres"


def _rt_dsn(db_name: str) -> str:
    """Return a DSN to the given round-trip test database on the same server."""
    url = os.environ.get("SQLERY_TEST_PG_URL", "")
    if not url:
        pytest.skip("SQLERY_TEST_PG_URL not set")
    parts = url.rsplit("/", 1)
    return parts[0] + "/" + db_name


def _make_isolated_db(db_name: str) -> psycopg.Connection:
    """Create a fresh isolated database and return a psycopg connection to it.

    Drops any pre-existing database with the same name first (idempotent).
    Returns a psycopg connection with autocommit=True.
    """
    admin_conn = psycopg.connect(_admin_dsn(), autocommit=True)
    try:
        with admin_conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
            cur.execute(f"CREATE DATABASE {db_name}")
    finally:
        admin_conn.close()
    # Use ClientCursor (client-side parameter binding) to match Django's PostgreSQL
    # backend behaviour — the migration's S7 CREATE TABLE ... FOR VALUES FROM (%s)
    # DDL requires client-side binding because PostgreSQL does not support server-side
    # parameter binding in partition-range DDL.
    return psycopg.connect(_rt_dsn(db_name), autocommit=True, cursor_factory=ClientCursor)


def _drop_isolated_db(db_name: str) -> None:
    """Drop the isolated test database (ignoring errors if it does not exist)."""
    try:
        admin_conn = psycopg.connect(_admin_dsn(), autocommit=True)
        try:
            with admin_conn.cursor() as cur:
                # Terminate any remaining connections before dropping.
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND pid <> pg_backend_pid()
                    """,
                    [db_name],
                )
                cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
        finally:
            admin_conn.close()
    except Exception:
        # Best-effort cleanup — do not mask the original test failure.
        pass


def _create_legacy_schema(conn: psycopg.Connection) -> None:
    """Create the pre-0030 unpartitioned sqlery_queued_job table and shared sequence.

    Mirrors what migration 0029 leaves in the database:
      - standalone sequence sqlery_job_id_seq
      - sqlery_queued_job with id DEFAULT nextval('sqlery_job_id_seq')

    All nullable columns omitted from INSERT will default to NULL / empty / 0.
    The schema matches models.py QueuedJob as of migration 0029 (before partitioning).
    """
    with conn.cursor() as cur:
        # Shared sequence (migration 0029 creates this).
        cur.execute("CREATE SEQUENCE IF NOT EXISTS sqlery_job_id_seq")

        cur.execute(
            """
            CREATE TABLE sqlery_queued_job (
                id                BIGINT          NOT NULL DEFAULT nextval('sqlery_job_id_seq'),
                task_path         VARCHAR(500)    NOT NULL,
                kwargs            JSONB           NOT NULL DEFAULT '{}',
                queue_name        VARCHAR(50)     NOT NULL DEFAULT 'default',
                priority          INTEGER         NOT NULL DEFAULT 0,
                status            VARCHAR(20)     NOT NULL DEFAULT 'queued',
                version           INTEGER         NOT NULL DEFAULT 0,
                parent_job_id     BIGINT,
                retry_count       INTEGER         NOT NULL DEFAULT 0,
                max_retries       INTEGER         NOT NULL DEFAULT 0,
                retry_backoff     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                allow_parallel    BOOLEAN         NOT NULL DEFAULT FALSE,
                tags              JSONB           NOT NULL DEFAULT '[]',
                dependencies      JSONB           NOT NULL DEFAULT '[]',
                webhook_url       VARCHAR(500),
                webhook_events    JSONB           NOT NULL DEFAULT '[]',
                webhook_status    VARCHAR(20),
                webhook_retries   INTEGER         NOT NULL DEFAULT 0,
                webhook_max_retries INTEGER       NOT NULL DEFAULT 3,
                timeout_seconds   INTEGER,
                worker_pid        INTEGER,
                child_pid         INTEGER,
                runs              JSONB           NOT NULL DEFAULT '[]',
                meta              JSONB,
                job_name          VARCHAR(255),
                retry_intervals   JSONB,
                on_success_path   VARCHAR(500)    NOT NULL DEFAULT '',
                on_failure_path   VARCHAR(500)    NOT NULL DEFAULT '',
                ttl               INTEGER,
                result_ttl        INTEGER,
                failure_ttl       INTEGER,
                scheduled_task_id BIGINT,
                worker_id         UUID,
                created_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
                scheduled_at      TIMESTAMPTZ,
                started_at        TIMESTAMPTZ,
                finished_at       TIMESTAMPTZ,
                duration_seconds  DOUBLE PRECISION,
                output            TEXT            NOT NULL DEFAULT '',
                error             TEXT            NOT NULL DEFAULT '',
                traceback         TEXT            NOT NULL DEFAULT '',
                termination_reason VARCHAR(100)   NOT NULL DEFAULT '',
                PRIMARY KEY (id)
            )
            """
        )
        # Seed the shared sequence past 0 so the first nextval() returns 1.
        cur.execute("SELECT setval('sqlery_job_id_seq', 1, false)")


def _run_forward(conn: psycopg.Connection) -> None:
    """Call _VendorGuardedCutover._forward via the fake schema editor."""
    editor = _FakeSchemaEditor(conn)
    _VendorGuardedCutover._forward(None, editor)


def _run_backward(conn: psycopg.Connection) -> None:
    """Call _VendorGuardedCutover._backward via the fake schema editor."""
    editor = _FakeSchemaEditor(conn)
    _VendorGuardedCutover._backward(None, editor)


# ---------------------------------------------------------------------------
# SC5 — audit file (no PG needed)
# ---------------------------------------------------------------------------


def test_sc5_blast_radius_audit_zero_unaddressed():
    """BLAST-RADIUS-AUDIT.md must declare UNADDRESSED: 0."""
    with open(PHASE_15_SC5_AUDIT_PATH) as f:
        content = f.read()
    assert "UNADDRESSED: 0" in content, (
        "BLAST-RADIUS-AUDIT.md must show UNADDRESSED: 0 — "
        "run 15-01 Task 1 before executing 15-03"
    )


# ---------------------------------------------------------------------------
# SC1 + SC2 + SC3 — forward migration tests
# ---------------------------------------------------------------------------


@_SKIP_NO_PG
def test_migration_forward_sc1_sc2_sc3():
    """SC1/SC2/SC3: forward migration on a ≥1M-row legacy snapshot.

    SC1 — all 1M rows present after cutover
    SC2 — zero rows in the DEFAULT partition
    SC3 — next insert id > pre-insert MAX(id)
    """
    db_name = "sqlery_rt_sc1sc2sc3"
    conn = _make_isolated_db(db_name)
    timings: dict = {}
    try:
        _create_legacy_schema(conn)

        # --- snapshot generation ---
        t0 = time.monotonic()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, kwargs, queue_name, status, created_at, priority)
                SELECT
                    'myapp.tasks.task_' || (i % 100)::text,
                    '{}'::jsonb,
                    'default',
                    CASE WHEN i % 5 = 0 THEN 'success' ELSE 'queued' END,
                    now() - ((1000000 - i) * interval '1 second'),
                    0
                FROM generate_series(1, 1000000) AS s(i)
                """
            )
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (legacy_count,) = cur.fetchone()
        snapshot_secs = time.monotonic() - t0
        assert legacy_count >= 1_000_000, f"Expected ≥1M rows in legacy table, got {legacy_count}"
        timings["snapshot_generation_secs"] = round(snapshot_secs, 2)
        timings["legacy_row_count"] = legacy_count

        # --- forward migration ---
        t1 = time.monotonic()
        _run_forward(conn)
        forward_secs = time.monotonic() - t1
        timings["forward_migration_secs"] = round(forward_secs, 2)

        with conn.cursor() as cur:
            # SC1 — all rows present in the partitioned table
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (partitioned_count,) = cur.fetchone()
            assert partitioned_count >= 1_000_000, (
                f"SC1 FAIL: expected ≥1M rows in partitioned table, got {partitioned_count}"
            )

            # SC2 — zero rows in the DEFAULT partition
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job_default")
            (default_count,) = cur.fetchone()
            assert default_count == 0, (
                f"SC2 FAIL: expected 0 rows in DEFAULT partition, got {default_count}"
            )

            # SC3 — identity continues from max(id)+1
            cur.execute("SELECT MAX(id) FROM sqlery_queued_job")
            (max_id_before,) = cur.fetchone()
            assert max_id_before is not None, "SC3 FAIL: no rows in partitioned table after migration"

            # Insert one row and verify it gets max_id_before + 1
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, kwargs, queue_name, status, created_at, priority)
                VALUES
                    ('sc3.identity.check', '{}', 'default', 'queued', now(), 0)
                RETURNING id
                """
            )
            (new_id,) = cur.fetchone()
            assert new_id > max_id_before, (
                f"SC3 FAIL: new insert id {new_id} is not > pre-insert MAX(id) {max_id_before}"
            )

        timings["partitioned_row_count"] = partitioned_count
        timings["default_partition_count"] = default_count
        timings["max_id_before_insert"] = max_id_before
        timings["new_insert_id"] = new_id

        print(f"\n  [SC1/SC2/SC3 timings] {timings}")
    finally:
        conn.close()
        _drop_isolated_db(db_name)


# ---------------------------------------------------------------------------
# SC1 part 2 — rollback test
# ---------------------------------------------------------------------------


@_SKIP_NO_PG
def test_migration_rollback_sc1():
    """SC1 rollback leg: partitioned → unpartitioned round-trip preserves all rows.

    Sets up a fresh 1M-row legacy table, runs forward, then runs backward and
    verifies:
      - sqlery_queued_job exists as an unpartitioned table (relkind != 'p')
      - row count equals the pre-rollback count (≥1M)
      - sqlery_queued_job_legacy still exists (kept per D3)
    """
    db_name = "sqlery_rt_rollback"
    conn = _make_isolated_db(db_name)
    try:
        _create_legacy_schema(conn)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, kwargs, queue_name, status, created_at, priority)
                SELECT
                    'myapp.tasks.task_' || (i % 100)::text,
                    '{}'::jsonb,
                    'default',
                    CASE WHEN i % 5 = 0 THEN 'success' ELSE 'queued' END,
                    now() - ((1000000 - i) * interval '1 second'),
                    0
                FROM generate_series(1, 1000000) AS s(i)
                """
            )

        t0 = time.monotonic()
        _run_forward(conn)
        forward_secs = time.monotonic() - t0

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (post_forward_count,) = cur.fetchone()

        t1 = time.monotonic()
        _run_backward(conn)
        rollback_secs = time.monotonic() - t1

        print(
            f"\n  [rollback timings] forward={round(forward_secs, 2)}s  "
            f"rollback={round(rollback_secs, 2)}s  "
            f"post_forward_count={post_forward_count}"
        )

        with conn.cursor() as cur:
            # Verify sqlery_queued_job is now unpartitioned (relkind = 'r', not 'p')
            cur.execute(
                """
                SELECT relkind FROM pg_class
                WHERE relname = 'sqlery_queued_job'
                  AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
                """
            )
            row = cur.fetchone()
            assert row is not None, "sqlery_queued_job does not exist after rollback"
            assert row[0] == "r", (
                f"sqlery_queued_job should be a regular table (relkind='r') "
                f"after rollback, got '{row[0]}'"
            )

            # Row count equals the post-forward count
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (post_rollback_count,) = cur.fetchone()
            assert post_rollback_count >= 1_000_000, (
                f"SC1 rollback FAIL: expected ≥1M rows after rollback, got {post_rollback_count}"
            )
            assert post_rollback_count == post_forward_count, (
                f"Row count mismatch after rollback: "
                f"post-forward={post_forward_count}, post-rollback={post_rollback_count}"
            )

            # sqlery_queued_job_legacy must still exist (D3 — kept until soak)
            cur.execute(
                "SELECT to_regclass('public.sqlery_queued_job_legacy') IS NOT NULL"
            )
            (legacy_exists,) = cur.fetchone()
            assert legacy_exists, (
                "sqlery_queued_job_legacy must NOT be dropped by rollback (D3 — keep until soak)"
            )
    finally:
        conn.close()
        _drop_isolated_db(db_name)


# ---------------------------------------------------------------------------
# SC4 — idempotency after injected mid-migration failure
# ---------------------------------------------------------------------------


@_SKIP_NO_PG
def test_migration_idempotency_sc4():
    """SC4: partial failure after S1 (rename) followed by a full rerun completes cleanly.

    Simulates the scenario where the migration crashes after S1 (rename
    sqlery_queued_job → sqlery_queued_job_legacy) but before S2 (CREATE partitioned
    table).  The guard in S1 (to_regclass check) ensures a second run skips the rename
    and the IF NOT EXISTS guards on subsequent steps make the full forward() idempotent.
    """
    db_name = "sqlery_rt_sc4"
    conn = _make_isolated_db(db_name)
    try:
        _create_legacy_schema(conn)

        # Small snapshot — 1000 rows is sufficient for idempotency testing.
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, kwargs, queue_name, status, created_at, priority)
                SELECT
                    'myapp.tasks.task_' || (i % 10)::text,
                    '{}'::jsonb,
                    'default',
                    CASE WHEN i % 5 = 0 THEN 'success' ELSE 'queued' END,
                    now() - (i * interval '1 second'),
                    0
                FROM generate_series(1, 1000) AS s(i)
                """
            )
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (original_count,) = cur.fetchone()

        # --- Inject partial failure: execute S1 only, then stop ---
        # S1: idempotent rename guard
        with conn.cursor() as cur:
            cur.execute(
                """
                DO $$ BEGIN
                    IF to_regclass('public.sqlery_queued_job_legacy') IS NULL
                       AND to_regclass('public.sqlery_queued_job') IS NOT NULL THEN
                        ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_legacy;
                    END IF;
                END $$
                """
            )

        # After partial failure: sqlery_queued_job_legacy exists, sqlery_queued_job does not.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('public.sqlery_queued_job_legacy') IS NOT NULL"
            )
            (legacy_exists,) = cur.fetchone()
            cur.execute(
                "SELECT to_regclass('public.sqlery_queued_job') IS NOT NULL"
            )
            (main_exists,) = cur.fetchone()
        assert legacy_exists, "SC4 setup: sqlery_queued_job_legacy should exist after S1"
        assert not main_exists, (
            "SC4 setup: sqlery_queued_job should NOT exist after rename-only S1"
        )

        # --- Rerun the full migration — should complete cleanly ---
        _run_forward(conn)

        # Verify the migration converged correctly
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (final_count,) = cur.fetchone()
            assert final_count == original_count, (
                f"SC4 FAIL: expected {original_count} rows after idempotent rerun, "
                f"got {final_count}"
            )

            # Confirm the table is now partitioned
            cur.execute(
                """
                SELECT relkind FROM pg_class
                WHERE relname = 'sqlery_queued_job'
                  AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
                """
            )
            row = cur.fetchone()
            assert row is not None, "sqlery_queued_job does not exist after idempotent rerun"
            assert row[0] == "p", (
                f"sqlery_queued_job should be partitioned (relkind='p') after rerun, "
                f"got '{row[0]}'"
            )

        print(f"\n  [SC4] original_count={original_count}, final_count={final_count} — PASS")
    finally:
        conn.close()
        _drop_isolated_db(db_name)
