"""Migration 0029: create sqlery_scheduled_job staging table.

Far-future jobs land here instead of sqlery_queued_job to avoid pinning partitions.
The id column shares the standalone sqlery_job_id_seq on PostgreSQL so ids are
globally unique across both tables. Depends on 0028_partial_pending_index.

SHARED-ID-SEQUENCE DECISION (2026-06-11): Django BigAutoField creates GENERATED AS
IDENTITY columns on PostgreSQL. GENERATED AS IDENTITY sequences cannot be re-owned
or shared between tables. Instead we create one standalone sequence
(sqlery_job_id_seq), DROP IDENTITY on both tables, and set both id columns to
nextval('sqlery_job_id_seq'). This gives a single monotonic id space across both
tables that survives the 0030 cutover (which copies the default via LIKE INCLUDING
DEFAULTS).
"""

from django.core.serializers.json import DjangoJSONEncoder
from django.db import migrations, models


class _PgSequenceWiring(migrations.RunSQL):
    """RunSQL subclass that skips sequence-wiring DDL on non-PostgreSQL databases.

    On SQLite the id DEFAULT is irrelevant (SQLite uses its own rowid mechanism);
    the guard ensures no-op on SQLite while the ALTER runs on PostgreSQL.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ("sqlery", "0028_partial_pending_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScheduledJob",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "queue_name",
                    models.CharField(
                        default="default",
                        help_text="Queue name for job routing",
                        max_length=50,
                    ),
                ),
                (
                    "task_path",
                    models.CharField(
                        help_text="Python path to callable",
                        max_length=500,
                    ),
                ),
                (
                    "payload",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=DjangoJSONEncoder,
                        help_text="Serialised job kwargs dict",
                    ),
                ),
                (
                    "scheduled_at",
                    models.DateTimeField(
                        help_text="UTC datetime when this job becomes due for promotion",
                    ),
                ),
                (
                    "priority",
                    models.IntegerField(
                        default=0,
                        help_text="Priority for enqueued jobs (higher = sooner)",
                    ),
                ),
                (
                    "max_retries",
                    models.IntegerField(
                        default=0,
                        help_text="Max retry attempts after failure",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Staged scheduled job",
                "verbose_name_plural": "Staged scheduled jobs",
                "db_table": "sqlery_scheduled_job",
                "ordering": ["scheduled_at"],
                "indexes": [
                    models.Index(
                        fields=["scheduled_at"],
                        name="sqlery_staged_job_sched_idx",
                    ),
                ],
            },
        ),
        _PgSequenceWiring(
            sql=[
                # SHARED-ID-SEQUENCE DECISION (2026-06-11):
                # Django BigAutoField on PG creates GENERATED AS IDENTITY columns.
                # GENERATED AS IDENTITY sequences cannot be shared/re-owned across tables.
                # Solution: create ONE standalone sequence, drop IDENTITY from both tables,
                # and set both defaults to nextval('sqlery_job_id_seq').
                #
                # Old (wrong — sqlery_queued_job_id_seq is an identity sequence, not
                # an ordinary named sequence; ALTER SEQUENCE OWNED BY NONE and
                # nextval references both fail on identity columns):
                # "ALTER SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq OWNED BY NONE;",
                # "DROP SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq;",
                # "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
                # " SET DEFAULT nextval('sqlery_queued_job_id_seq'::regclass);",
                #
                # Step 1: Create the standalone shared sequence (owned by nothing so it
                # survives both tables and the 0030 cutover rename).
                "CREATE SEQUENCE IF NOT EXISTS sqlery_job_id_seq;",
                # Step 2: Drop IDENTITY on sqlery_queued_job.id (the column was created
                # GENERATED BY DEFAULT AS IDENTITY by Django's BigAutoField).
                "ALTER TABLE sqlery_queued_job ALTER COLUMN id DROP IDENTITY IF EXISTS;",
                # Step 3: Point sqlery_queued_job.id at the shared sequence.
                "ALTER TABLE sqlery_queued_job ALTER COLUMN id"
                " SET DEFAULT nextval('sqlery_job_id_seq');",
                # Step 4: Drop IDENTITY on the newly-created sqlery_scheduled_job.id
                # (same reason — BigAutoField → GENERATED AS IDENTITY on PG).
                "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id DROP IDENTITY IF EXISTS;",
                # Step 5: Point sqlery_scheduled_job.id at the shared sequence.
                "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
                " SET DEFAULT nextval('sqlery_job_id_seq');",
                # Step 6: Seed the shared sequence past the current max queued id so
                # the next nextval() call yields max(id)+1 (or 1 on an empty table).
                # setval(..., N, true) means nextval() will return N+1.
                # setval(..., 1, false) on empty table means nextval() returns 1.
                "SELECT setval('sqlery_job_id_seq',"
                " GREATEST((SELECT COALESCE(MAX(id), 0) FROM sqlery_queued_job), 1),"
                " (SELECT COUNT(*) > 0 FROM sqlery_queued_job));",
            ],
            reverse_sql=[
                # Reverse: drop both shared-sequence defaults FIRST (they reference
                # sqlery_job_id_seq, which we drop last), then restore IDENTITY on
                # sqlery_queued_job.id (sqlery_scheduled_job is dropped by CreateModel
                # reverse so we don't need to restore its identity).
                #
                # Old (wrong — was recreating sqlery_scheduled_job_id_seq as OWNED BY):
                # "CREATE SEQUENCE IF NOT EXISTS sqlery_scheduled_job_id_seq"
                # " OWNED BY sqlery_scheduled_job.id;",
                # "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
                # " SET DEFAULT nextval('sqlery_scheduled_job_id_seq'::regclass);",
                #
                # Drop sqlery_scheduled_job default first (table still exists here;
                # CreateModel reverse runs AFTER this RunSQL reverse).
                "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id DROP DEFAULT;",
                # Drop sqlery_queued_job shared default.
                "ALTER TABLE sqlery_queued_job ALTER COLUMN id DROP DEFAULT;",
                # Restore IDENTITY on sqlery_queued_job.id.
                "ALTER TABLE sqlery_queued_job ALTER COLUMN id"
                " ADD GENERATED BY DEFAULT AS IDENTITY;",
                # Drop the standalone shared sequence (both defaults are gone).
                "DROP SEQUENCE IF EXISTS sqlery_job_id_seq;",
            ],
        ),
    ]
