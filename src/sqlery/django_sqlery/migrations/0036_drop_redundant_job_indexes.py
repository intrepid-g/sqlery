"""Drop two redundant indexes on ``sqlery_queued_job`` (issue #136).

Every UPDATE that changes an indexed column (or a column in a partial-index
predicate) writes a new entry into that index and defeats HOT updates. Job
``status`` changes on every transition, so an index that contains ``status`` but
that no query uses is pure write amplification and index bloat.

Dropped (PostgreSQL: the ``sqlery_qj_*`` indexes created by 0031 on the
partitioned parent; SQLite: the indexes Django created itself):

* ``(task_path, status)`` -- no query path filters or orders on ``task_path``
  (admin search is ``icontains``, which a btree cannot serve), and ``status`` is
  rewritten on every job transition.
* ``(created_at DESC)`` -- a strict leading-column duplicate of the primary key
  ``(created_at, id)``, which serves ``ORDER BY created_at [DESC]`` (and
  ``created_at`` range filters) by forward/backward scan.

Kept on purpose: ``sqlery_job_pending_idx`` (claiming), ``status``,
``started_at``/``finished_at`` (cleanup, rate limit, dashboard), ``worker_id``,
``scheduled_task_id``, ``scheduled_at`` (next-due), and the immutable
``queue_name``/``priority``/``parent_job_id``/``job_name`` indexes.

The DROP INDEX on the partitioned parent takes a brief ACCESS EXCLUSIVE lock on
the table and its partitions; ``lock_timeout`` makes it fail fast (migration is
atomic, rerun later) instead of queueing behind a long transaction and stalling
every worker behind it.

Standalone (SQLAlchemy/Alembic) PostgreSQL installs never create these indexes
on the partitioned table, so there is no matching Alembic revision.
"""

from django.db import migrations

# PostgreSQL: (name, definition) of the indexes created by 0031 on the parent.
_PG_INDEXES = [
    ("sqlery_qj_task_path_status_idx", "(task_path, status)"),
    ("sqlery_qj_created_desc_idx", "(created_at DESC)"),
]

# SQLite: (name, definition) of the indexes Django created from the model state.
_SQLITE_INDEXES = [
    ("sqlery_queu_task_pa_cf2fe6_idx", "(task_path, status)"),
    ("sqlery_job_created_desc", "(created_at DESC)"),
]


def _indexes_for(schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        return _PG_INDEXES
    if vendor == "sqlite":
        return _SQLITE_INDEXES
    return []


def drop_indexes(apps, schema_editor):
    indexes = _indexes_for(schema_editor)
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
    for name, _cols in indexes:
        schema_editor.execute(f"DROP INDEX IF EXISTS {name}")


def restore_indexes(apps, schema_editor):
    indexes = _indexes_for(schema_editor)
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
    for name, cols in indexes:
        schema_editor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON sqlery_queued_job {cols}")


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ("sqlery", "0035_sqlite_drop_demoted_job_fks"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(
                    model_name="queuedjob", name="sqlery_queu_task_pa_cf2fe6_idx"
                ),
                migrations.RemoveIndex(model_name="queuedjob", name="sqlery_job_created_desc"),
            ],
            database_operations=[migrations.RunPython(drop_indexes, restore_indexes)],
        ),
    ]
