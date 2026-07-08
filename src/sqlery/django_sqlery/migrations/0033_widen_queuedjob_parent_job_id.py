"""Widen QueuedJob.parent_job_id from int4 to int8.

parent_job_id stores a QueuedJob id, which is a 64-bit _generate_job_id()
value. It was created as an IntegerField (0021) while id itself is a
BigIntegerField (0032) — so the moment a failed job spawns a retry, the retry
INSERT writes a 64-bit id into an int4 column and Postgres raises
"integer out of range". The failure is swallowed by the worker's mark-failed
try/except, so failed jobs silently never retry.

Postgres: the physical column is genuinely int4 and needs a real type change.
sqlery_queued_job is a partitioned table; ALTER COLUMN ... TYPE on the parent
cascades to every partition automatically (rewriting each and rebuilding its
parent_job_id index) under a brief ACCESS EXCLUSIVE lock. int4 -> int8 is an
implicit cast, so no USING clause is required.

SQLite: INTEGER is a variable-width affinity that already stores 64-bit values,
so no DDL is needed. A Django-generated AlterField would force a full table
rebuild that trips the demoted sqlery_worker -> sqlery_queued_job FK
("foreign key mismatch") — the same hazard 0032 avoided — so we skip DDL there
and only update Django's migration state.
"""

from django.db import migrations, models


def widen_parent_job_id(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "ALTER TABLE sqlery_queued_job "
        "ALTER COLUMN parent_job_id TYPE bigint"
    )


def narrow_parent_job_id(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    # Reverse migration. Any parent_job_id exceeding int4 range will make this
    # fail loudly (by design) rather than silently truncate.
    schema_editor.execute(
        "ALTER TABLE sqlery_queued_job "
        "ALTER COLUMN parent_job_id TYPE integer USING parent_job_id::integer"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0032_alter_queuedjob_id"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(widen_parent_job_id, narrow_parent_job_id),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="queuedjob",
                    name="parent_job_id",
                    field=models.BigIntegerField(
                        blank=True,
                        db_index=True,
                        help_text="ID of the failed job this retry was created from (links retry chain)",
                        null=True,
                    ),
                ),
            ],
        ),
    ]
