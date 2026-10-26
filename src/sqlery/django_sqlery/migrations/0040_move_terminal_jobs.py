"""Data migration for issue #140: move already finished jobs into ``sqlery_job_history``.

Every ``success`` / ``failed`` / ``archived`` row of ``sqlery_queued_job`` is copied to history
and deleted from the live table, in batches of 1000 rows, each batch in its own transaction
(the migration is not atomic, so no long transaction pins the xmin horizon while it runs).
A failed job that still owes a retry (``retry_pending`` marker) stays: its retry has not been
created yet. ``finished_at`` is the history's partition key and must not be NULL: rows that
never recorded it get ``COALESCE(finished_at, started_at, created_at)``.

Resumable and idempotent: if it is interrupted, run it again; a batch that was copied but not
yet deleted is finished by the next one. The reverse moves the history rows back into the live
table (also in batches) so the schema migration 0039 can be reversed.

Stop all workers before migrating (see 0039).
"""

from django.db import migrations

from sqlery.core import history as history_core

BATCH = 1000


def _live_columns(apps):
    model = apps.get_model("sqlery", "QueuedJob")
    return [f.column for f in model._meta.concrete_fields]


def _runner(schema_editor):
    def run(sql: str) -> int:
        # one short transaction per statement: the migration is not atomic
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.rowcount

    return run


def move_terminal_jobs(apps, schema_editor):
    history_core.move_terminal_rows(_runner(schema_editor), _live_columns(apps), batch=BATCH)


def restore_terminal_jobs(apps, schema_editor):
    history_core.restore_terminal_rows(_runner(schema_editor), _live_columns(apps), batch=BATCH)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("sqlery", "0039_job_history"),
    ]

    operations = [
        migrations.RunPython(move_terminal_jobs, restore_terminal_jobs, atomic=False),
    ]
