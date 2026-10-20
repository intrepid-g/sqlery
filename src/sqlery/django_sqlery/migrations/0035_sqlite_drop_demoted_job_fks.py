"""Drop the demoted job FKs that SQLite tables still carry.

Migration 0030 demoted ``JobRegistry.job_id`` and ``Worker.current_job_id`` from
foreign keys to plain ``BigIntegerField`` columns (D4: referential integrity to the
partitioned queued-job table is intentionally dropped). On PostgreSQL the physical
constraints were dropped there, but SQLite never ran that DDL, so the tables created
by 0003/0001 kept ``REFERENCES sqlery_queued_job (id)``. With the model no longer
cascading, deleting a QueuedJob that has registry rows (or is a worker's current job)
raised ``IntegrityError: FOREIGN KEY constraint failed``.

SQLite only: rebuild both tables from the current model state, which has no FK.
"""

from django.db import migrations


def rebuild_without_job_fks(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return
    for model_name in ("JobRegistry", "Worker"):
        schema_editor._remake_table(apps.get_model("sqlery", model_name))


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0034_drop_legacy_worker_fk"),
    ]

    operations = [
        migrations.RunPython(rebuild_without_job_fks, migrations.RunPython.noop),
    ]
