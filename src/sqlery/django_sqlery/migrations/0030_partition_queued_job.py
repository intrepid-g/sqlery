"""Migration 0030: partition cutover for sqlery_queued_job.

STOP-THE-WORLD MIGRATION — operational protocol (D3):
  1. Stop all workers and daemons (they must not be writing during the rename/copy).
  2. Run: python manage.py migrate sqlery 0030
  3. Restart workers and daemons.

Expected duration as a function of table size:
  The INSERT ... SELECT copies every row. Budget ~1 min per 1M rows on modern hardware
  with a local PG instance; network-attached storage may be 3-5x slower.
  For tables > 10M rows consider running the SQL statements in the forward() body
  manually during a maintenance window and using the escape hatch below.

Escape hatch for huge tables (D3):
  Run the SQL statements in the forward() body manually during a maintenance window,
  then run: python manage.py migrate --fake sqlery 0030
  (NOT --fake-initial — that only applies to initial migrations.)

Rollback:
  The legacy table (sqlery_queued_job_legacy) is intentionally kept — rollback is a
  rename swap, not a copy. See the reverse SQL in the _VendorGuardedCutover operation.
  DROP TABLE sqlery_queued_job_legacy is a SEPARATE later migration after soak.

SQLite: this migration is a no-op. The vendor guard returns immediately.

Index byte-identity invariant (D7):
  sqlery_job_pending_idx is recreated with the same definition as migration 0028.
  If either definition changes, change both.

SHARED-ID-SEQUENCE DECISION (2026-06-11):
  Migration 0029 replaced the GENERATED AS IDENTITY defaults on sqlery_queued_job.id
  and sqlery_scheduled_job.id with nextval('sqlery_job_id_seq') — a standalone
  sequence owned by nothing.  Because the legacy table already carries
  DEFAULT nextval('sqlery_job_id_seq'), step S2 (LIKE ... INCLUDING DEFAULTS) copies
  that default to the new partitioned table automatically.  S4 (the old ADD GENERATED
  BY DEFAULT AS IDENTITY) is therefore removed — adding IDENTITY would create a
  *second* per-table identity sequence and break the shared-sequence invariant.
  S9 now seeds sqlery_job_id_seq (not a per-table sequence) past max(id).
"""

from datetime import datetime, timedelta, timezone

from django.db import migrations, models


class _VendorGuardedCutover(migrations.RunPython):
    """Stop-the-world partition cutover — executes only on PostgreSQL.

    On SQLite and any other non-PostgreSQL vendor, both forward and backward
    directions return immediately without touching the database.

    Forward (S1–S9):
      S1  Idempotent rename of sqlery_queued_job → sqlery_queued_job_legacy
      S2  CREATE TABLE sqlery_queued_job LIKE legacy INCLUDING DEFAULTS PARTITION BY RANGE
      S3  ADD PRIMARY KEY (created_at, id)
      S4  (REMOVED) — id DEFAULT nextval('sqlery_job_id_seq') already copied by S2
      S5  CREATE DEFAULT partition
      S6  Recreate sqlery_job_pending_idx (byte-identical to 0028 — D7)
      S7  Historical partitions BEFORE the bulk copy (prevents DEFAULT-partition trap)
      S8  INSERT INTO sqlery_queued_job SELECT * FROM legacy ON CONFLICT DO NOTHING
      S9  Seed shared sequence sqlery_job_id_seq past max(id)

    Backward:
      Creates unpartitioned copy, copies rows back, swaps names.
      Legacy table is NOT dropped (deferred per D3).
    """

    def __init__(self):
        super().__init__(
            code=self._forward,
            reverse_code=self._backward,
            atomic=False,
            hints={"target_db": None},
            elidable=False,
        )

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        self._forward(None, schema_editor)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        self._backward(None, schema_editor)

    @staticmethod
    def _forward(_apps, schema_editor):
        """Execute partition cutover SQL statements S1-S9 (PG only)."""
        connection = schema_editor.connection
        with connection.cursor() as cursor:
            # S1 — Idempotent rename: sqlery_queued_job → sqlery_queued_job_legacy
            cursor.execute(
                """
                DO $$ BEGIN
                    IF to_regclass('public.sqlery_queued_job_legacy') IS NULL
                       AND to_regclass('public.sqlery_queued_job') IS NOT NULL THEN
                        ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_legacy;
                    END IF;
                END $$;
                """
            )

            # S2 — Create partitioned table.
            # INCLUDING DEFAULTS copies DEFAULT nextval('sqlery_job_id_seq') from the
            # legacy table (set by migration 0029).  NOT INCLUDING IDENTITY so we do
            # not accidentally create a second identity sequence on the new table.
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sqlery_queued_job (
                    LIKE sqlery_queued_job_legacy
                    INCLUDING DEFAULTS INCLUDING STORAGE
                ) PARTITION BY RANGE (created_at);
                """
            )

            # S3 — Add composite primary key (catalog-guarded for idempotency)
            cursor.execute(
                """
                SELECT COUNT(*) FROM pg_constraint
                WHERE conrelid = 'sqlery_queued_job'::regclass AND contype = 'p'
                """
            )
            (pk_count,) = cursor.fetchone()
            if pk_count == 0:
                cursor.execute(
                    "ALTER TABLE sqlery_queued_job ADD PRIMARY KEY (created_at, id);"
                )

            # S4 — (REMOVED — SHARED-ID-SEQUENCE DECISION 2026-06-11)
            # The id column already has DEFAULT nextval('sqlery_job_id_seq') copied from
            # the legacy table by LIKE INCLUDING DEFAULTS in S2.  Adding GENERATED BY
            # DEFAULT AS IDENTITY here would create a second per-table identity sequence
            # and break the shared-sequence invariant.
            #
            # Old (wrong — creates a per-table identity sequence, conflicts with the
            # standalone sqlery_job_id_seq installed by migration 0029):
            # cursor.execute(
            #     """
            #     SELECT attidentity FROM pg_attribute
            #     WHERE attrelid = 'sqlery_queued_job'::regclass AND attname = 'id'
            #     """
            # )
            # row = cursor.fetchone()
            # attidentity = row[0] if row else None
            # if not attidentity:
            #     cursor.execute(
            #         """
            #         ALTER TABLE sqlery_queued_job ALTER COLUMN id
            #             ADD GENERATED BY DEFAULT AS IDENTITY;
            #         """
            #     )

            # S5 — Default partition (catch-all for rows that miss every date range)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sqlery_queued_job_default
                    PARTITION OF sqlery_queued_job DEFAULT;
                """
            )

            # S6 — Partial index byte-identical to 0028 (D7).
            # After S1 renames sqlery_queued_job → sqlery_queued_job_legacy, the index
            # sqlery_job_pending_idx moves to the legacy table.  CREATE INDEX IF NOT EXISTS
            # would then see the name already exists and skip creating it on the new
            # partitioned table — leaving the partitioned table without the index.
            # Fix: drop the index from the legacy table first (it will be rebuilt on
            # sqlery_queued_job_legacy if ever needed again, but the legacy table is
            # query-dormant after cutover), then create fresh on the partitioned table.
            #
            # Old (wrong — IF NOT EXISTS finds the name on the legacy table and silently
            # skips the create, leaving the partitioned table without sqlery_job_pending_idx):
            # CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx
            #     ON sqlery_queued_job (queue_name, priority DESC, created_at)
            #     WHERE status = 'queued';
            cursor.execute(
                "DROP INDEX IF EXISTS sqlery_job_pending_idx;"
            )
            cursor.execute(
                """
                CREATE INDEX sqlery_job_pending_idx
                    ON sqlery_queued_job (queue_name, priority DESC, created_at)
                    WHERE status = 'queued';
                """
            )

            # S7 — Historical partitions BEFORE the bulk copy (prevents DEFAULT-partition trap)
            # Fetch the date range of existing rows from the legacy table.
            cursor.execute(
                "SELECT MIN(created_at), MAX(created_at) FROM sqlery_queued_job_legacy"
            )
            min_ts, max_ts = cursor.fetchone()

            if min_ts is not None:
                # Round down to day boundary (UTC)
                if min_ts.tzinfo is None:
                    min_ts = min_ts.replace(tzinfo=timezone.utc)

                start = min_ts.replace(hour=0, minute=0, second=0, microsecond=0)
                # Include today + PREMAKE days ahead so new inserts land in a partition
                PREMAKE = 7
                now_utc = datetime.now(timezone.utc)
                end = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                    days=PREMAKE + 1
                )

                current = start
                while current < end:
                    next_day = current + timedelta(days=1)
                    # Partition name contains only digits and underscores — safe for interpolation.
                    partition_name = "sqlery_queued_job_" + current.strftime("%Y%m%d")
                    cursor.execute(
                        (
                            "CREATE TABLE IF NOT EXISTS %s PARTITION OF sqlery_queued_job"
                            " FOR VALUES FROM (%%s) TO (%%s)" % partition_name
                        ),
                        [current, next_day],
                    )
                    current = next_day
            else:
                # Empty legacy table: still create a forward-looking partition window.
                PREMAKE = 7
                now_utc = datetime.now(timezone.utc)
                start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=PREMAKE + 1)
                current = start
                while current < end:
                    next_day = current + timedelta(days=1)
                    partition_name = "sqlery_queued_job_" + current.strftime("%Y%m%d")
                    cursor.execute(
                        (
                            "CREATE TABLE IF NOT EXISTS %s PARTITION OF sqlery_queued_job"
                            " FOR VALUES FROM (%%s) TO (%%s)" % partition_name
                        ),
                        [current, next_day],
                    )
                    current = next_day

            # S8 — Bulk copy (ON CONFLICT DO NOTHING makes the copy idempotent on re-run)
            cursor.execute(
                """
                INSERT INTO sqlery_queued_job
                    SELECT * FROM sqlery_queued_job_legacy
                    ON CONFLICT DO NOTHING;
                """
            )

            # S9 — Seed the SHARED sequence sqlery_job_id_seq past the max copied id so
            # new inserts continue from max(id)+1 (or 1 on an empty table).
            # setval(..., N, true)  → nextval() returns N+1 (last value WAS consumed)
            # setval(..., 1, false) → nextval() returns 1   (on empty table)
            #
            # Old (wrong — pg_get_serial_sequence only works for GENERATED AS IDENTITY or
            # SERIAL columns; after 0029 the column is a plain DEFAULT nextval(...), so
            # pg_get_serial_sequence returns NULL and setval(NULL, ...) raises an error):
            # cursor.execute(
            #     "SELECT setval("
            #     "    pg_get_serial_sequence('sqlery_queued_job', 'id'),"
            #     "    (SELECT COALESCE(MAX(id), 1) FROM sqlery_queued_job)"
            #     ")"
            # )
            cursor.execute(
                "SELECT setval("
                "    'sqlery_job_id_seq',"
                "    GREATEST((SELECT COALESCE(MAX(id), 0) FROM sqlery_queued_job), 1),"
                "    (SELECT COUNT(*) > 0 FROM sqlery_queued_job)"
                ")"
            )

    @staticmethod
    def _backward(_apps, schema_editor):
        """Rollback: restore unpartitioned sqlery_queued_job from the live partitioned table.

        Creates an unpartitioned copy (LIKE sqlery_queued_job INCLUDING DEFAULTS), copies
        all rows back, then renames the partitioned table out of the way and promotes the
        unpartitioned copy to sqlery_queued_job.  The unpartitioned table inherits
        DEFAULT nextval('sqlery_job_id_seq') via INCLUDING DEFAULTS — no identity is added.
        The legacy table (sqlery_queued_job_legacy) is NOT dropped — that is deferred
        per D3.
        """
        connection = schema_editor.connection
        with connection.cursor() as cursor:
            # Create unpartitioned rollback table (INCLUDING DEFAULTS copies the shared
            # sequence default — id will default to nextval('sqlery_job_id_seq')).
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sqlery_queued_job_unpartitioned (
                    LIKE sqlery_queued_job INCLUDING DEFAULTS INCLUDING STORAGE
                );
                """
            )

            # Copy rows back from the partitioned table
            cursor.execute(
                """
                INSERT INTO sqlery_queued_job_unpartitioned
                    SELECT * FROM sqlery_queued_job
                    ON CONFLICT DO NOTHING;
                """
            )

            # Rename the partitioned table out of the way (idempotent guard)
            cursor.execute(
                """
                DO $$ BEGIN
                    IF to_regclass('public.sqlery_queued_job') IS NOT NULL THEN
                        ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_partitioned_bak;
                    END IF;
                END $$;
                """
            )

            # Rename the unpartitioned copy back to sqlery_queued_job
            cursor.execute(
                "ALTER TABLE sqlery_queued_job_unpartitioned RENAME TO sqlery_queued_job;"
            )


class Migration(migrations.Migration):
    """Partition cutover migration for sqlery_queued_job (atomic=False, PG-only)."""

    atomic = False

    dependencies = [
        ("sqlery", "0029_scheduled_job_staging"),
    ]

    operations = [
        # PART A: actual DDL cutover (PG only; SQLite returns immediately)
        _VendorGuardedCutover(),
        # PART B: state-only operations that mirror the 15-01 model changes
        # so that Django's migration graph state matches models.py.
        # No database_operations — the DDL above handles the physical schema.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                # QueuedJob: promote CompositePrimaryKey, demote id to non-pk BigAutoField.
                # The exact deconstruct() values are required so that makemigrations --check
                # sees no further drift after this migration.
                migrations.AddField(
                    model_name="queuedjob",
                    name="pk",
                    field=models.CompositePrimaryKey(
                        "created_at",
                        "id",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                migrations.AlterField(
                    model_name="queuedjob",
                    name="id",
                    field=models.BigAutoField(primary_key=True),
                ),
                # JobRegistry: remove old index on FK column, then swap FK → BigIntegerField
                migrations.RemoveIndex(
                    model_name="jobregistry",
                    name="sqlery_regi_job_id_404819_idx",
                ),
                migrations.RemoveField(
                    model_name="jobregistry",
                    name="job",
                ),
                migrations.AddField(
                    model_name="jobregistry",
                    name="job_id",
                    field=models.BigIntegerField(
                        db_index=True,
                        help_text=(
                            "ID of the QueuedJob being tracked "
                            "(FK demoted — D4: referential integrity to partitioned table "
                            "intentionally dropped)"
                        ),
                        default=0,
                    ),
                    preserve_default=False,
                ),
                migrations.AddIndex(
                    model_name="jobregistry",
                    index=models.Index(
                        fields=["job_id", "registry_type"],
                        name="sqlery_regi_job_id_404819_idx",
                    ),
                ),
                # Worker: remove FK column, add BigIntegerField
                migrations.RemoveField(
                    model_name="worker",
                    name="current_job",
                ),
                migrations.AddField(
                    model_name="worker",
                    name="current_job_id",
                    field=models.BigIntegerField(
                        blank=True,
                        db_index=True,
                        help_text=(
                            "ID of the QueuedJob currently being processed "
                            "(FK demoted — D4: referential integrity to partitioned table "
                            "intentionally dropped)"
                        ),
                        null=True,
                    ),
                ),
            ],
        ),
    ]
