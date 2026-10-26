"""Sequences for the opt-in ready-queue rotation (issue #141, PostgreSQL only).

* ``sqlery_ready_gen_seq``: the generation; ``generation % 3`` is the slot writers use.
* ``sqlery_ready_switch_ts_seq`` / ``sqlery_ready_reclaim_ts_seq``: epoch seconds of the last
  switch and the last successful reclaim (sequences, so updating them leaves no dead tuples).

Nothing changes until ``READY_ROTATION_ENABLED`` is set. Reversible; no-op on SQLite.
Mirrors the Alembic ``20261026_0022`` revision.
"""

from django.db import migrations

from sqlery.core import rotation


def create_sequences(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in rotation.postgres_rotation_ddl():
        schema_editor.execute(statement)


def drop_sequences(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in rotation.drop_rotation_ddl():
        schema_editor.execute(statement)


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0040_move_terminal_jobs"),
    ]

    operations = [
        migrations.RunPython(create_sequences, drop_sequences),
    ]
