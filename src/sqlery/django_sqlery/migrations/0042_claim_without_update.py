"""Claim without an UPDATE of the job row (issue #157).

A claim used to delete the ready entry, UPDATE the job row (status, started_at, worker,
version) and insert an attempt. The UPDATE left a dead version of a wide row per job and
rewrote index entries. Now a claim deletes the entry and inserts the attempt; the job row is
written only at enqueue and at completion. A job is running iff it has an open attempt
(``finished_at IS NULL``); its row keeps ``status = 'queued'``.

* ``sqlery_job_all`` is replaced by the attempt-derived view: a queued row with an open
  attempt reads as ``running``, with ``started_at`` / ``worker_id`` / ``worker_pid`` /
  ``child_pid`` taken from the attempt.
* ``sqlery_attempt_open_job_idx``: partial index of the open attempts by job (the running jobs).
* Data: rows stored as ``running`` become ``queued`` rows with an open attempt (one is built
  for a row that has none).
* The ``worker_id`` index of the live table is replaced by a partial one (``WHERE worker_id IS
  NOT NULL``): the claim no longer writes the worker to the row, so the column is NULL for
  every new job and a full index would only collect NULL entries. The partial index is empty
  but still serves the ``ON DELETE SET NULL`` of a worker row. The ``status`` index stays: the
  claim no longer rewrites it, and the sweeps that look for the rare non-queued rows (a failure
  owing its retry, a stray terminal row, ``shutting_down``) need it.

Stop all workers before migrating: a worker started before this migration still claims with
the UPDATE and would not be seen as running by the new readers. Reversible: the reverse
writes the running rows back (status, start, worker and PIDs from the open attempt).
Mirrors the Alembic ``20261026_0023`` revision.
"""

from django.db import migrations, models

from sqlery.core import history as history_core
from sqlery.tables import JOB_ATTEMPT, QUEUED_JOB

_WORKER_INDEX = "sqlery_qj_worker_id_idx"


def _live_columns(apps):
    model = apps.get_model("sqlery", "QueuedJob")
    return [f.column for f in model._meta.concrete_fields]


def _lock_timeout(schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")


def derive_running(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    _lock_timeout(schema_editor)
    schema_editor.execute(history_core.open_job_index_ddl(JOB_ATTEMPT))
    for statement in history_core.normalize_running_sql(vendor):
        schema_editor.execute(statement)
    for statement in history_core.view_columns_ddl(
        _live_columns(apps), vendor, with_attempts=True
    ):
        schema_editor.execute(statement)


def restore_running(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    _lock_timeout(schema_editor)
    for statement in history_core.view_columns_ddl(
        _live_columns(apps), vendor, with_attempts=False
    ):
        schema_editor.execute(statement)
    for statement in history_core.denormalize_running_sql():
        schema_editor.execute(statement)
    schema_editor.execute(f"DROP INDEX IF EXISTS {history_core.ATTEMPT_OPEN_JOB_INDEX}")


def _worker_indexes(schema_editor):
    """Names of the (non-unique) indexes on exactly ``worker_id`` of the live table."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, QUEUED_JOB)
    return [
        name
        for name, info in constraints.items()
        if info.get("index")
        and not info.get("unique")
        and not info.get("primary_key")
        and info.get("columns") == ["worker_id"]
    ]


def partial_worker_index(apps, schema_editor):
    _lock_timeout(schema_editor)
    for name in {*_worker_indexes(schema_editor), _WORKER_INDEX}:
        schema_editor.execute(f"DROP INDEX IF EXISTS {name}")
    schema_editor.execute(
        f"CREATE INDEX {_WORKER_INDEX} ON {QUEUED_JOB} (worker_id) WHERE worker_id IS NOT NULL"
    )


def full_worker_index(apps, schema_editor):
    _lock_timeout(schema_editor)
    schema_editor.execute(f"DROP INDEX IF EXISTS {_WORKER_INDEX}")
    schema_editor.execute(f"CREATE INDEX {_WORKER_INDEX} ON {QUEUED_JOB} (worker_id)")


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ("sqlery", "0041_ready_rotation"),
    ]

    operations = [
        migrations.RunPython(derive_running, restore_running),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="jobattempt",
                    index=models.Index(
                        condition=models.Q(("finished_at__isnull", True)),
                        fields=["job_id"],
                        name="sqlery_attempt_open_job_idx",
                    ),
                ),
            ],
            # created above (IF NOT EXISTS, so a re-run is harmless)
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="queuedjob",
                    name="worker",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        help_text="Worker currently processing this job",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="assigned_jobs",
                        to="sqlery.worker",
                    ),
                ),
                migrations.AddIndex(
                    model_name="queuedjob",
                    index=models.Index(
                        condition=models.Q(("worker__isnull", False)),
                        fields=["worker"],
                        name=_WORKER_INDEX,
                    ),
                ),
            ],
            database_operations=[migrations.RunPython(partial_worker_index, full_worker_index)],
        ),
    ]
