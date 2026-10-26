"""Create the ready queue: ``sqlery_job_ready`` / ``sqlery_job_blocked`` (issue #139).

Claiming reads a narrow table of claimable entries instead of scanning ``sqlery_queued_job``:

* ``sqlery_job_ready (slot, job_id, queue_name, priority, created_at, scheduled_at)``,
  primary key ``(slot, job_id)``. PostgreSQL: ``PARTITION BY LIST (slot)`` with partitions for
  slots 0, 1 and 2 (only slot 0 is written until rotation is enabled); SQLite: a plain table.
  Claim index ``(queue_name, priority DESC, created_at, job_id)``.
* ``sqlery_job_blocked (job_id, dependency_id, created_at, blocked_at)``: one row per
  dependency of a job whose dependencies are unmet.

Data migration: every queued job gets a ready entry (claims move dependency-blocked jobs out
of the ready table on first sight).

Because claiming no longer reads ``sqlery_queued_job`` by status, the claiming-only partial
index ``sqlery_job_pending_idx`` is dropped (no other query uses it; the ``status``,
``started_at``, ``finished_at``, ``worker_id`` and ``scheduled_at`` indexes stay, other
queries need them). The reverse recreates it and drops both tables.

The DROP INDEX on the partitioned parent takes a brief ACCESS EXCLUSIVE lock; ``lock_timeout``
makes it fail fast (the migration is atomic, rerun later) instead of queueing behind a long
transaction and stalling every worker behind it.

Stop all workers before migrating: a worker started before the tables existed keeps claiming
from ``sqlery_queued_job`` and never sees (or removes) the new entries, and new workers refuse
to start without the tables. Mirrors the Alembic ``20261026_0020`` revision.
"""

import django.utils.timezone
from django.db import migrations, models

from sqlery.core import ready as ready_core


def create_ready_queue(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        statements = ready_core.postgres_ready_ddl()
    else:
        statements = ready_core.sqlite_ready_ddl()
    for statement in statements:
        schema_editor.execute(statement)
    schema_editor.execute(ready_core.backfill_ready_sql())
    # Claiming no longer reads sqlery_queued_job by status.
    if vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
    schema_editor.execute("DROP INDEX IF EXISTS sqlery_job_pending_idx")


def drop_ready_queue(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx "
        "ON sqlery_queued_job (queue_name, priority DESC, created_at) "
        "WHERE status = 'queued'"
    )
    schema_editor.execute(f"DROP TABLE IF EXISTS {ready_core.BLOCKED_TABLE}")
    # Dropping the partitioned parent drops its partitions.
    schema_editor.execute(f"DROP TABLE IF EXISTS {ready_core.READY_TABLE}")


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ("sqlery", "0037_job_attempt"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="JobBlocked",
                    fields=[
                        (
                            "pk",
                            models.CompositePrimaryKey(
                                "job_id",
                                "dependency_id",
                                blank=True,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("job_id", models.BigIntegerField()),
                        ("dependency_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(help_text="The blocked job's created_at"),
                        ),
                        ("blocked_at", models.DateTimeField(default=django.utils.timezone.now)),
                    ],
                    options={"db_table": "sqlery_job_blocked"},
                ),
                migrations.CreateModel(
                    name="JobReady",
                    fields=[
                        (
                            "pk",
                            models.CompositePrimaryKey(
                                "slot",
                                "job_id",
                                blank=True,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("slot", models.SmallIntegerField(default=0)),
                        ("job_id", models.BigIntegerField()),
                        ("queue_name", models.CharField(max_length=50)),
                        ("priority", models.IntegerField(default=0)),
                        (
                            "created_at",
                            models.DateTimeField(
                                help_text="The job's created_at (its partition key)"
                            ),
                        ),
                        ("scheduled_at", models.DateTimeField(blank=True, null=True)),
                    ],
                    options={"db_table": "sqlery_job_ready"},
                ),
                migrations.RemoveIndex(model_name="queuedjob", name="sqlery_job_pending_idx"),
                migrations.AddIndex(
                    model_name="jobblocked",
                    index=models.Index(
                        fields=["dependency_id"], name="sqlery_blocked_dependency_idx"
                    ),
                ),
                migrations.AddIndex(
                    model_name="jobready",
                    index=models.Index(
                        fields=["queue_name", "-priority", "created_at", "job_id"],
                        name="sqlery_ready_claim_idx",
                    ),
                ),
            ],
            database_operations=[migrations.RunPython(create_ready_queue, drop_ready_queue)],
        ),
    ]
