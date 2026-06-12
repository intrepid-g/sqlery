"""Migration 0031: recreate secondary indexes on the partitioned jobs table.

Phase 15's cutover (0030) built the partitioned ``sqlery_queued_job`` via
``CREATE TABLE ... (LIKE sqlery_queued_job_legacy INCLUDING DEFAULTS INCLUDING
STORAGE)``. ``LIKE`` with those options copies columns/defaults/storage but NOT
indexes — only ``sqlery_job_pending_idx`` was explicitly recreated in 0030.

This migration recreates the remaining secondary indexes (the model's other
``Meta.indexes`` plus the single-column ``db_index=True`` indexes) on the
partitioned parent so PostgreSQL cascades them to every partition. Required for
the write-path EXPLAIN pruning tests to be meaningful and for production query
performance.

PostgreSQL only — vendor-guarded no-op on SQLite (SQLite never partitions, and
its index set was created normally by the model's CreateModel in earlier
migrations). ``state_operations`` is empty: the model's Meta/db_index already
declare these indexes, so Django's migration state is unchanged and
``makemigrations --check`` stays clean — only the physical PG DDL was missing.
"""

from django.db import migrations


# (index_name, column_list_sql) for the partitioned sqlery_queued_job parent.
# CREATE INDEX on a partitioned parent cascades to all current/future partitions.
_SECONDARY_INDEXES = [
    # Remaining Meta.indexes (sqlery_job_pending_idx already created by 0030).
    # NOTE: distinct names (not the model's Meta names) are used on purpose — the
    # renamed sqlery_queued_job_legacy table still holds the canonical Meta index
    # names (index names are schema-unique), so reusing them here would silently
    # no-op under CREATE INDEX IF NOT EXISTS. The query planner selects indexes by
    # shape, not name, so EXPLAIN pruning is unaffected. Django state is unchanged
    # (state_operations=[]), so makemigrations stays clean.
    ("sqlery_qj_task_path_status_idx", "(task_path, status)"),
    ("sqlery_qj_created_desc_idx", "(created_at DESC)"),
    ("sqlery_qj_finished_desc_idx", "(finished_at DESC)"),
    ("sqlery_qj_started_desc_idx", "(started_at DESC)"),
    # Single-column db_index=True fields.
    ("sqlery_qj_queue_name_idx", "(queue_name)"),
    ("sqlery_qj_priority_idx", "(priority)"),
    ("sqlery_qj_status_idx", "(status)"),
    ("sqlery_qj_parent_job_id_idx", "(parent_job_id)"),
    ("sqlery_qj_job_name_idx", "(job_name)"),
    ("sqlery_qj_scheduled_task_id_idx", "(scheduled_task_id)"),
    ("sqlery_qj_worker_id_idx", "(worker_id)"),
    ("sqlery_qj_scheduled_at_idx", "(scheduled_at)"),
]


class _VendorGuardedIndexes(migrations.RunPython):
    """RunPython that creates/drops the secondary indexes on PostgreSQL only."""

    def __init__(self):
        super().__init__(self._forward, self._backward, atomic=False)

    @staticmethod
    def _forward(_apps, schema_editor):
        if schema_editor.connection.vendor != "postgresql":
            return
        with schema_editor.connection.cursor() as cur:
            for name, cols in _SECONDARY_INDEXES:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {name} ON sqlery_queued_job {cols};"
                )

    @staticmethod
    def _backward(_apps, schema_editor):
        if schema_editor.connection.vendor != "postgresql":
            return
        with schema_editor.connection.cursor() as cur:
            for name, _cols in _SECONDARY_INDEXES:
                cur.execute(f"DROP INDEX IF EXISTS {name};")


class Migration(migrations.Migration):

    # CREATE INDEX on a partitioned parent is fine inside a transaction; keep
    # atomic for clean rollback on partial failure.
    atomic = True

    dependencies = [
        ("sqlery", "0030_partition_queued_job"),
    ]

    operations = [
        _VendorGuardedIndexes(),
    ]
