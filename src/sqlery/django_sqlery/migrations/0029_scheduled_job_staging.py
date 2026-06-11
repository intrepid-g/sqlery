"""Migration 0029: create sqlery_scheduled_job staging table.

Far-future jobs land here instead of sqlery_queued_job to avoid pinning partitions.
The id column shares sqlery_queued_job_id_seq on PostgreSQL so ids are globally
unique across both tables. Depends on 0028_partial_pending_index.
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
                # Detach the auto-created per-table sequence from the column so the
                # ALTER COLUMN below can point id to the shared sqlery_queued_job_id_seq.
                "ALTER SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq OWNED BY NONE;",
                # Drop the now-orphaned per-table sequence so it does not accumulate
                # in repeated test/migration runs or survive a table drop (WR-03).
                # Old: sequence was left dangling with OWNED BY NONE — leaked indefinitely.
                "DROP SEQUENCE IF EXISTS sqlery_scheduled_job_id_seq;",
                # Share the queued-job sequence so ids are globally unique across tables.
                "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
                " SET DEFAULT nextval('sqlery_queued_job_id_seq'::regclass);",
            ],
            reverse_sql=[
                # Recreate and own the sequence BEFORE dropping the shared default so
                # no INSERT window exists without a sequence (WR-03 reverse ordering fix).
                # Old: DROP DEFAULT first, then CREATE SEQUENCE — any INSERT in between failed.
                "CREATE SEQUENCE IF NOT EXISTS sqlery_scheduled_job_id_seq"
                " OWNED BY sqlery_scheduled_job.id;",
                "ALTER TABLE sqlery_scheduled_job ALTER COLUMN id"
                " SET DEFAULT nextval('sqlery_scheduled_job_id_seq'::regclass);",
            ],
        ),
    ]
