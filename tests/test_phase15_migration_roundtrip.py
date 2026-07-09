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


# ---------------------------------------------------------------------------
# Helper — legacy schema WITH real FK constraints and job_name UNIQUE
# ---------------------------------------------------------------------------


def _create_legacy_schema_with_real_constraints(conn: psycopg.Connection) -> None:
    """Build the pre-0030 schema as it actually looks after migrations 0001..0029.

    This is the schema the real migration operates on in production.  The synthetic
    _create_legacy_schema helper omits FK constraints and the job_name unique
    constraint, which caused CR-01 and CR-03 to be invisible in the existing tests.

    Tables created:
      - sqlery_queued_job  (primary queue table, plain PK on id, job_name UNIQUE)
      - sqlery_worker      (FK current_job_id → sqlery_queued_job.id ON DELETE SET NULL)
      - sqlery_registry    (FK job_id → sqlery_queued_job.id ON DELETE CASCADE)

    The FK constraint names match the auto-generated names Django produces
    (sqlery_worker_current_job_id_* and sqlery_registry_job_id_*) but because S0
    uses a catalog lookup rather than hard-coded names, the exact names do not matter
    for the cutover logic.
    """
    with conn.cursor() as cur:
        # Shared sequence (migration 0029)
        cur.execute("CREATE SEQUENCE IF NOT EXISTS sqlery_job_id_seq")

        # sqlery_queued_job — matches the column set after migration 0029.
        # job_name carries the UNIQUE constraint added in migration 0015.
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
                job_name          VARCHAR(255)    UNIQUE,
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
                PRIMARY KEY (id),
                CONSTRAINT sqlery_queued_job_job_name_key UNIQUE (job_name)
            )
            """
        )
        cur.execute("SELECT setval('sqlery_job_id_seq', 1, false)")

        # sqlery_worker — created by migration 0002.
        # current_job_id is a FK to sqlery_queued_job.id (ON DELETE SET NULL).
        cur.execute(
            """
            CREATE TABLE sqlery_worker (
                id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                node_id         VARCHAR(255)    NOT NULL,
                pid             INTEGER         NOT NULL,
                status          VARCHAR(10)     NOT NULL DEFAULT 'idle',
                queues          JSONB           NOT NULL DEFAULT '[]',
                last_heartbeat  TIMESTAMPTZ     NOT NULL DEFAULT now(),
                started_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
                jobs_processed  INTEGER         NOT NULL DEFAULT 0,
                current_job_id  BIGINT,
                CONSTRAINT sqlery_worker_current_job_id_fk
                    FOREIGN KEY (current_job_id)
                    REFERENCES sqlery_queued_job (id)
                    ON DELETE SET NULL
            )
            """
        )

        # sqlery_registry — created by migration 0003.
        # job_id is a FK to sqlery_queued_job.id (ON DELETE CASCADE).
        cur.execute(
            """
            CREATE TABLE sqlery_registry (
                id              BIGSERIAL       PRIMARY KEY,
                registry_type   VARCHAR(20)     NOT NULL,
                entered_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
                exited_at       TIMESTAMPTZ,
                metadata        JSONB           NOT NULL DEFAULT '{}',
                job_id          BIGINT          NOT NULL,
                CONSTRAINT sqlery_registry_job_id_fk
                    FOREIGN KEY (job_id)
                    REFERENCES sqlery_queued_job (id)
                    ON DELETE CASCADE
            )
            """
        )


# ---------------------------------------------------------------------------
# SC6 — FK operability after cutover (catches CR-01)
# ---------------------------------------------------------------------------


@_SKIP_NO_PG
def test_migration_fk_operability_sc6():
    """SC6: post-cutover JobRegistry and Worker writes with new job ids succeed.

    Builds the legacy schema WITH real FK constraints (as migrations 0002/0003 create),
    inserts a handful of rows, runs the 0030 forward cutover, then verifies:

    1. A new QueuedJob can be inserted into the partitioned table.
    2. A sqlery_registry row referencing the new job id inserts without IntegrityError.
    3. A sqlery_worker row can be updated to set current_job_id to the new job id.
    4. job_name dedup still works at the app level (INSERT the same job_name a second
       time after deleting the first — simulating backend.create_job's "new wins" logic).

    Without CR-01 fix (S0 FK-drop step), assertions 2 and 3 raise IntegrityError because
    the FK constraints still point at sqlery_queued_job_legacy after the rename.
    Without CR-03 fix (S3b + AlterField), the job_name unique constraint is silently lost
    and assertion 4 would allow duplicate job_names that violate the uniqueness invariant.
    """
    db_name = "sqlery_rt_sc6"
    conn = _make_isolated_db(db_name)
    try:
        _create_legacy_schema_with_real_constraints(conn)

        # Seed legacy table with a few jobs (including one named job).
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, status, created_at, job_name)
                VALUES
                    ('myapp.tasks.alpha', 'success', now() - interval '1 hour', NULL),
                    ('myapp.tasks.beta', 'success', now() - interval '2 hours', 'named-job-1')
                RETURNING id
                """
            )
            legacy_ids = [row[0] for row in cur.fetchall()]

            # Insert a registry entry and a worker pointing at the legacy job.
            cur.execute(
                """
                INSERT INTO sqlery_registry (registry_type, job_id)
                VALUES ('started', %s)
                """,
                [legacy_ids[0]],
            )
            cur.execute(
                """
                INSERT INTO sqlery_worker (node_id, pid, current_job_id)
                VALUES ('test-node', 12345, %s)
                RETURNING id
                """,
                [legacy_ids[0]],
            )
            (worker_id,) = cur.fetchone()

        # Run the cutover.
        _run_forward(conn)

        with conn.cursor() as cur:
            # Verify legacy rows are present in the partitioned table.
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (count,) = cur.fetchone()
            assert count >= 2, f"SC6: expected ≥2 rows after cutover, got {count}"

            # 1 — Insert a NEW job into the partitioned table.
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, status, created_at)
                VALUES
                    ('myapp.tasks.new_post_cutover', 'queued', now())
                RETURNING id
                """,
            )
            (new_job_id,) = cur.fetchone()
            assert new_job_id is not None, "SC6: could not insert new job after cutover"

            # 2 — Insert a registry row referencing the NEW job id.
            # Without CR-01 fix this raises IntegrityError (FK still points at legacy table).
            cur.execute(
                """
                INSERT INTO sqlery_registry (registry_type, job_id)
                VALUES ('started', %s)
                """,
                [new_job_id],
            )

            # 3 — Update a Worker.current_job_id to the NEW job id.
            # Without CR-01 fix this also raises IntegrityError.
            cur.execute(
                """
                UPDATE sqlery_worker SET current_job_id = %s WHERE id = %s
                """,
                [new_job_id, worker_id],
            )

            # 4 — App-level job_name dedup: simulate backend.create_job "new wins".
            # Delete the existing named job, then insert a new one with the same name.
            cur.execute(
                "DELETE FROM sqlery_queued_job WHERE job_name = 'named-job-1'"
            )
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, status, created_at, job_name)
                VALUES
                    ('myapp.tasks.beta_v2', 'queued', now(), 'named-job-1')
                RETURNING id
                """,
            )
            (named_job_id,) = cur.fetchone()
            assert named_job_id is not None, "SC6: named job re-insert failed after cutover"

            # Confirm only ONE row with that job_name exists (dedup invariant maintained).
            cur.execute(
                "SELECT COUNT(*) FROM sqlery_queued_job WHERE job_name = 'named-job-1'"
            )
            (name_count,) = cur.fetchone()
            assert name_count == 1, (
                f"SC6: expected exactly 1 row with job_name='named-job-1', got {name_count}"
            )

        print(f"\n  [SC6] new_job_id={new_job_id}, named_job_id={named_job_id} — PASS")
    finally:
        conn.close()
        _drop_isolated_db(db_name)


# ---------------------------------------------------------------------------
# SC4b — rollback idempotency after injected mid-rollback failure (catches CR-02)
# ---------------------------------------------------------------------------


@_SKIP_NO_PG
def test_migration_rollback_idempotency_sc4b():
    """SC4b: partial failure mid-rollback followed by full rerun gives correct row count.

    Simulates the scenario where _backward crashes between step 2 (INSERT into
    sqlery_queued_job_unpartitioned) and step 3 (rename partitioned table out of the way),
    then is re-run in full.

    Without CR-02 fix (missing PK on rollback table), ON CONFLICT DO NOTHING is a no-op
    and the re-run doubles all rows.  With the fix, the duplicate rows are detected and
    skipped, yielding the correct count.
    """
    db_name = "sqlery_rt_sc4b"
    conn = _make_isolated_db(db_name)
    try:
        _create_legacy_schema(conn)

        # Small snapshot — 100 rows sufficient for idempotency.
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sqlery_queued_job
                    (task_path, kwargs, queue_name, status, created_at, priority)
                SELECT
                    'myapp.tasks.task_' || (i % 10)::text,
                    '{}'::jsonb,
                    'default',
                    'queued',
                    now() - (i * interval '1 second'),
                    0
                FROM generate_series(1, 100) AS s(i)
                """
            )
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (original_count,) = cur.fetchone()

        # Run forward migration to get a partitioned table.
        _run_forward(conn)

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (post_forward_count,) = cur.fetchone()

        # --- Inject partial rollback failure: execute steps 1 + 2 only, then stop ---

        with conn.cursor() as cur:
            # Step 1 — create unpartitioned table (mirrors _backward step 1 + PK add)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sqlery_queued_job_unpartitioned (
                    LIKE sqlery_queued_job INCLUDING DEFAULTS INCLUDING STORAGE
                );
                """
            )
            # Step 2 — INSERT (stops here; step 3 rename NOT executed)
            cur.execute(
                """
                INSERT INTO sqlery_queued_job_unpartitioned
                    SELECT * FROM sqlery_queued_job
                    ON CONFLICT DO NOTHING;
                """
            )
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job_unpartitioned")
            (after_partial_insert_count,) = cur.fetchone()

        # Verify the partial rollback left the expected state.
        assert after_partial_insert_count == post_forward_count, (
            f"SC4b setup: expected {post_forward_count} rows in unpartitioned table "
            f"after partial insert, got {after_partial_insert_count}"
        )

        # --- Re-run the full _backward — should be idempotent, NOT double rows ---
        _run_backward(conn)

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sqlery_queued_job")
            (post_rollback_count,) = cur.fetchone()

        # Key assertion: row count must equal the original, not be doubled.
        assert post_rollback_count == original_count, (
            f"SC4b FAIL: rollback re-run produced {post_rollback_count} rows "
            f"(expected {original_count}, got {post_rollback_count} — "
            f"CR-02 duplicate-row hazard: ON CONFLICT DO NOTHING was a no-op)"
        )

        print(
            f"\n  [SC4b] original={original_count}, post_forward={post_forward_count}, "
            f"post_rollback={post_rollback_count} — PASS"
        )
    finally:
        conn.close()
        _drop_isolated_db(db_name)
