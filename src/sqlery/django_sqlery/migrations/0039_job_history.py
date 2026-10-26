"""Terminal jobs move to an insert-only history (issue #140): schema.

A job that reaches a terminal state (``success`` / ``failed`` / ``archived``) leaves
``sqlery_queued_job`` for ``sqlery_job_history`` in the transaction that records the outcome
(``INSERT .. SELECT`` + ``DELETE``, fenced like the attempt close; see
``sqlery.core.history``). The live table then holds only queued / running jobs, and history is
never updated: only retention removes rows.

* ``sqlery_job_history``: every column of ``sqlery_queued_job``, primary key
  ``(finished_at, id)``. PostgreSQL: ``PARTITION BY RANGE (finished_at)`` with a DEFAULT
  partition and daily partitions (the daemon keeps creating them), so retention drops whole
  partitions; SQLite: a plain table.
* ``sqlery_job_all``: a view over the live table and history, for readers that must see a
  job whatever its state (unmanaged model ``JobAll``).
* ``sqlery_job_attempt`` becomes ``PARTITION BY RANGE (claimed_at)`` on PostgreSQL, so the
  attempts of jobs past retention are reclaimed by DROP together with their history
  (finished attempts never accumulate). The id keeps a sequence default; the primary key is
  ``(claimed_at, id)``.
* The ``started_at`` / ``finished_at`` DESC indexes on ``sqlery_queued_job`` are dropped:
  queries by finish time or start time of finished jobs now hit history, and a live job is
  found by status.

The rows of already finished jobs are moved by migration 0040 (batched, resumable).

Stop all workers before migrating: a worker started before the history table existed
finishes jobs in the live table, and new workers refuse to start without it. The
DROP INDEX / table swaps take brief ACCESS EXCLUSIVE locks; ``lock_timeout`` makes them fail
fast (the migration is atomic, rerun later) instead of queueing behind a long transaction.
Mirrors the Alembic ``20261026_0021`` revision.
"""

import django.core.serializers.json
from django.db import migrations, models

import sqlery.django_sqlery.models
from sqlery.core import history as history_core
from sqlery.tables import JOB_ATTEMPT, JOB_HISTORY

_ATTEMPT_COLUMNS = (
    "id, job_id, token, worker_id, worker_pid, child_pid, claimed_at, finished_at, outcome"
)


def _live_columns(apps):
    model = apps.get_model("sqlery", "QueuedJob")
    return [f.column for f in model._meta.concrete_fields]


def _relkind(cursor, table):
    cursor.execute(
        "SELECT relkind FROM pg_class WHERE relname = %s "
        "AND relnamespace = current_schema()::regnamespace",
        [table],
    )
    row = cursor.fetchone()
    return row[0] if row else None


def partition_history(apps, schema_editor):
    """PostgreSQL: replace the (still empty) plain history table by a partitioned one."""
    from datetime import date, timedelta

    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        partitioned = _relkind(cur, "sqlery_queued_job") == "p"
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
        schema_editor.execute(f"DROP TABLE IF EXISTS {JOB_HISTORY}")
        for statement in history_core.postgres_history_ddl(partitioned, indexes=False):
            schema_editor.execute(statement)
        if not partitioned:
            return
        cur.execute(
            "SELECT min(COALESCE(finished_at, started_at, created_at)) FROM sqlery_queued_job "
            "WHERE status IN ('success', 'failed', 'archived')"
        )
        oldest = cur.fetchone()[0]
        today = date.today()
        first_day = min(today, oldest.date()) if oldest is not None else today
        for statement in history_core.daily_partition_ddl(
            JOB_HISTORY, first_day, today + timedelta(days=history_core.PREMAKE_DAYS + 1)
        ):
            schema_editor.execute(statement)


def partition_attempts(apps, schema_editor):
    """PostgreSQL: swap the plain attempt table for one partitioned by ``claimed_at``."""
    from datetime import date, timedelta

    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        if _relkind(cur, JOB_ATTEMPT) != "r":
            return  # already partitioned (or missing: the model state creates it)
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
        old = f"{JOB_ATTEMPT}_unpartitioned"
        schema_editor.execute(f"ALTER TABLE {JOB_ATTEMPT} RENAME TO {old}")
        # Index / constraint names are schema-wide: free them for the new table.
        schema_editor.execute("DROP INDEX IF EXISTS sqlery_attempt_job_idx")
        schema_editor.execute("DROP INDEX IF EXISTS sqlery_attempt_open_worker_idx")
        cur.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'p'",
            [old],
        )
        for (pk_name,) in cur.fetchall():
            schema_editor.execute(f"ALTER INDEX {pk_name} RENAME TO {old}_pkey")
        for statement in history_core.postgres_attempt_partitioned_ddl():
            schema_editor.execute(statement)
        cur.execute(f"SELECT min(claimed_at) FROM {old}")
        oldest = cur.fetchone()[0]
        today = date.today()
        first_day = min(today, oldest.date()) if oldest is not None else today
        for statement in history_core.daily_partition_ddl(
            JOB_ATTEMPT, first_day, today + timedelta(days=history_core.PREMAKE_DAYS + 1)
        ):
            schema_editor.execute(statement)
        schema_editor.execute(
            f"INSERT INTO {JOB_ATTEMPT} ({_ATTEMPT_COLUMNS}) "
            f"SELECT {_ATTEMPT_COLUMNS} FROM {old}"
        )
        schema_editor.execute(
            f"SELECT setval('{history_core.ATTEMPT_ID_SEQUENCE}', "
            f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {JOB_ATTEMPT}), 1), "
            f"(SELECT COUNT(*) > 0 FROM {JOB_ATTEMPT}))"
        )
        schema_editor.execute(f"DROP TABLE {old}")


def unpartition_attempts(apps, schema_editor):
    """Reverse: back to a plain attempt table (identity id, single-column primary key)."""
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        if _relkind(cur, JOB_ATTEMPT) != "p":
            return
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
        new = f"{JOB_ATTEMPT}_plain"
        schema_editor.execute(
            f"""
            CREATE TABLE {new} (
                id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                job_id      BIGINT       NOT NULL,
                token       VARCHAR(36)  NOT NULL,
                worker_id   UUID,
                worker_pid  INTEGER,
                child_pid   INTEGER,
                claimed_at  TIMESTAMPTZ  NOT NULL,
                finished_at TIMESTAMPTZ,
                outcome     VARCHAR(10),
                {history_core.ATTEMPT_OUTCOME_CHECK}
            )
            """
        )
        schema_editor.execute(
            f"INSERT INTO {new} ({_ATTEMPT_COLUMNS}) SELECT {_ATTEMPT_COLUMNS} FROM {JOB_ATTEMPT}"
        )
        schema_editor.execute(
            f"SELECT setval(pg_get_serial_sequence('{new}', 'id'), "
            f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {new}), 1), "
            f"(SELECT COUNT(*) > 0 FROM {new}))"
        )
        schema_editor.execute(f"DROP TABLE {JOB_ATTEMPT}")
        schema_editor.execute(f"DROP SEQUENCE IF EXISTS {history_core.ATTEMPT_ID_SEQUENCE}")
        schema_editor.execute(f"ALTER TABLE {new} RENAME TO {JOB_ATTEMPT}")
        schema_editor.execute(f"ALTER INDEX {new}_pkey RENAME TO {JOB_ATTEMPT}_pkey")
        schema_editor.execute(f"CREATE INDEX sqlery_attempt_job_idx ON {JOB_ATTEMPT} (job_id)")
        schema_editor.execute(
            f"CREATE INDEX sqlery_attempt_open_worker_idx ON {JOB_ATTEMPT} (worker_id) "
            f"WHERE finished_at IS NULL"
        )


def create_view(apps, schema_editor):
    for statement in history_core.view_columns_ddl(
        _live_columns(apps), schema_editor.connection.vendor
    ):
        schema_editor.execute(statement)


def drop_view(apps, schema_editor):
    schema_editor.execute(history_core.drop_view_ddl())


# Indexes on the live table that only served finished jobs. PostgreSQL: the ``sqlery_qj_*``
# indexes created by 0031 on the partitioned parent; SQLite: the ones Django created itself.
_PG_DROPPED = [
    ("sqlery_qj_finished_desc_idx", "(finished_at DESC)"),
    ("sqlery_qj_started_desc_idx", "(started_at DESC)"),
]
_SQLITE_DROPPED = [
    ("sqlery_job_finished_desc", "(finished_at DESC)"),
    ("sqlery_job_started_desc", "(started_at DESC)"),
]


def _dropped(schema_editor):
    vendor = schema_editor.connection.vendor
    return _PG_DROPPED if vendor == "postgresql" else _SQLITE_DROPPED if vendor == "sqlite" else []


def drop_live_indexes(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
    for name, _cols in _dropped(schema_editor):
        schema_editor.execute(f"DROP INDEX IF EXISTS {name}")


def restore_live_indexes(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
    for name, cols in _dropped(schema_editor):
        schema_editor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON sqlery_queued_job {cols}")


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ("sqlery", "0038_job_ready_queue"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobHistory",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=sqlery.django_sqlery.models._generate_job_id, editable=False
                    ),
                ),
                (
                    "task_path",
                    models.CharField(
                        help_text="Python path to callable (e.g., 'myapp.tasks.my_function')",
                        max_length=500,
                    ),
                ),
                (
                    "kwargs",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Keyword arguments to pass to task function",
                    ),
                ),
                (
                    "queue_name",
                    models.CharField(
                        default="default", help_text="Queue name for job routing", max_length=50
                    ),
                ),
                (
                    "priority",
                    models.IntegerField(default=0, help_text="Priority (higher = sooner)"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                            ("archived", "Archived"),
                            ("shutting_down", "Shutting Down"),
                        ],
                        default="queued",
                        max_length=20,
                    ),
                ),
                (
                    "version",
                    models.IntegerField(
                        default=0,
                        help_text="Optimistic locking version for atomic job claiming (increments on each update)",
                    ),
                ),
                (
                    "parent_job_id",
                    models.BigIntegerField(
                        blank=True,
                        help_text="ID of the failed job this retry was created from (links retry chain)",
                        null=True,
                    ),
                ),
                (
                    "retry_count",
                    models.IntegerField(
                        default=0, help_text="Current retry attempt number (0 = first attempt)"
                    ),
                ),
                (
                    "max_retries",
                    models.IntegerField(
                        default=0, help_text="Maximum number of retry attempts (0 = no retries)"
                    ),
                ),
                (
                    "retry_backoff",
                    models.FloatField(
                        default=1.0,
                        help_text="Exponential backoff multiplier (seconds between retries)",
                    ),
                ),
                (
                    "allow_parallel",
                    models.BooleanField(
                        default=False,
                        help_text="Allow multiple jobs from same queue to run in parallel (default: false for safety)",
                    ),
                ),
                (
                    "tags",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Tags for concurrency limiting (e.g., ['acme-api', 'rate-limited'])",
                    ),
                ),
                (
                    "dependencies",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="List of job IDs that must complete successfully before this job can run",
                    ),
                ),
                (
                    "webhook_url",
                    models.URLField(
                        blank=True,
                        help_text="URL to call when job completes (success or failure)",
                        max_length=500,
                        null=True,
                    ),
                ),
                (
                    "webhook_events",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Events that trigger webhook: ['success', 'failure'] or subset",
                    ),
                ),
                (
                    "webhook_status",
                    models.CharField(
                        blank=True,
                        choices=[("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")],
                        help_text="Status of webhook delivery",
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "webhook_retries",
                    models.IntegerField(default=0, help_text="Number of webhook delivery attempts"),
                ),
                (
                    "webhook_max_retries",
                    models.IntegerField(
                        default=3, help_text="Maximum webhook delivery retry attempts"
                    ),
                ),
                (
                    "timeout_seconds",
                    models.IntegerField(
                        blank=True,
                        help_text="Maximum execution time in seconds (None = no timeout)",
                        null=True,
                    ),
                ),
                (
                    "worker_pid",
                    models.IntegerField(
                        blank=True,
                        help_text="Process ID of worker executing this job (for external kill on timeout)",
                        null=True,
                    ),
                ),
                (
                    "child_pid",
                    models.IntegerField(
                        blank=True, help_text="PID of forked child executing this job", null=True
                    ),
                ),
                (
                    "runs",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="History of all execution attempts",
                    ),
                ),
                (
                    "meta",
                    models.JSONField(
                        blank=True,
                        default=None,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Free-form metadata dict for task functions (persisted to DB)",
                        null=True,
                    ),
                ),
                (
                    "job_name",
                    models.CharField(
                        blank=True,
                        help_text="Optional unique string identifier (e.g. 'send-invoice-123')",
                        max_length=255,
                        null=True,
                    ),
                ),
                (
                    "retry_intervals",
                    models.JSONField(
                        blank=True,
                        default=None,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Fixed retry delay list in seconds [5, 10, 60]. Overrides exponential backoff when set.",
                        null=True,
                    ),
                ),
                (
                    "on_success_path",
                    models.CharField(
                        blank=True, help_text="Import path for success callback", max_length=500
                    ),
                ),
                (
                    "on_failure_path",
                    models.CharField(
                        blank=True, help_text="Import path for failure callback", max_length=500
                    ),
                ),
                (
                    "ttl",
                    models.IntegerField(
                        blank=True,
                        help_text="Seconds job can stay queued before expiring (None = no limit)",
                        null=True,
                    ),
                ),
                (
                    "result_ttl",
                    models.IntegerField(
                        blank=True,
                        help_text="Seconds to keep successful result (-1 = forever, None = use global)",
                        null=True,
                    ),
                ),
                (
                    "failure_ttl",
                    models.IntegerField(
                        blank=True,
                        help_text="Seconds to keep failed job data (-1 = forever, None = use global)",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(help_text="When job was enqueued")),
                (
                    "scheduled_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When job should run (NULL = run immediately)",
                        null=True,
                    ),
                ),
                (
                    "started_at",
                    models.DateTimeField(blank=True, help_text="When execution began", null=True),
                ),
                ("finished_at", models.DateTimeField(help_text="When execution completed")),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("output", models.TextField(blank=True, help_text="Task return value or stdout")),
                ("error", models.TextField(blank=True, help_text="Error message if failed")),
                ("traceback", models.TextField(blank=True, help_text="Full traceback if failed")),
                (
                    "termination_reason",
                    models.CharField(
                        blank=True,
                        help_text="Reason for job termination (signal, timeout, user action, etc.)",
                        max_length=100,
                    ),
                ),
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "finished_at",
                        "id",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("scheduled_task_id", models.IntegerField(blank=True, null=True)),
                ("worker_id", models.UUIDField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Finished job",
                "verbose_name_plural": "Job history",
                "db_table": "sqlery_job_history",
                "ordering": ["-finished_at", "-id"],
            },
        ),
        # PostgreSQL: the table just created is empty; make it partitioned (indexes follow).
        migrations.RunPython(partition_history, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="jobhistory",
            index=models.Index(fields=["id"], name="sqlery_hist_id_idx"),
        ),
        migrations.AddIndex(
            model_name="jobhistory",
            index=models.Index(
                condition=models.Q(("parent_job_id__isnull", False)),
                fields=["parent_job_id"],
                name="sqlery_hist_parent_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="jobhistory",
            index=models.Index(fields=["status", "-finished_at"], name="sqlery_hist_status_idx"),
        ),
        migrations.AddIndex(
            model_name="jobhistory",
            index=models.Index(
                condition=models.Q(("job_name__isnull", False)),
                fields=["job_name"],
                name="sqlery_hist_name_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="jobhistory",
            index=models.Index(fields=["-created_at"], name="sqlery_hist_created_idx"),
        ),
        migrations.RunPython(partition_attempts, unpartition_attempts),
        migrations.CreateModel(
            name="JobAll",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=sqlery.django_sqlery.models._generate_job_id,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "task_path",
                    models.CharField(
                        help_text="Python path to callable (e.g., 'myapp.tasks.my_function')",
                        max_length=500,
                    ),
                ),
                (
                    "kwargs",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Keyword arguments to pass to task function",
                    ),
                ),
                (
                    "queue_name",
                    models.CharField(
                        default="default", help_text="Queue name for job routing", max_length=50
                    ),
                ),
                (
                    "priority",
                    models.IntegerField(default=0, help_text="Priority (higher = sooner)"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                            ("archived", "Archived"),
                            ("shutting_down", "Shutting Down"),
                        ],
                        default="queued",
                        max_length=20,
                    ),
                ),
                (
                    "version",
                    models.IntegerField(
                        default=0,
                        help_text="Optimistic locking version for atomic job claiming (increments on each update)",
                    ),
                ),
                (
                    "parent_job_id",
                    models.BigIntegerField(
                        blank=True,
                        help_text="ID of the failed job this retry was created from (links retry chain)",
                        null=True,
                    ),
                ),
                (
                    "retry_count",
                    models.IntegerField(
                        default=0, help_text="Current retry attempt number (0 = first attempt)"
                    ),
                ),
                (
                    "max_retries",
                    models.IntegerField(
                        default=0, help_text="Maximum number of retry attempts (0 = no retries)"
                    ),
                ),
                (
                    "retry_backoff",
                    models.FloatField(
                        default=1.0,
                        help_text="Exponential backoff multiplier (seconds between retries)",
                    ),
                ),
                (
                    "allow_parallel",
                    models.BooleanField(
                        default=False,
                        help_text="Allow multiple jobs from same queue to run in parallel (default: false for safety)",
                    ),
                ),
                (
                    "tags",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Tags for concurrency limiting (e.g., ['acme-api', 'rate-limited'])",
                    ),
                ),
                (
                    "dependencies",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="List of job IDs that must complete successfully before this job can run",
                    ),
                ),
                (
                    "webhook_url",
                    models.URLField(
                        blank=True,
                        help_text="URL to call when job completes (success or failure)",
                        max_length=500,
                        null=True,
                    ),
                ),
                (
                    "webhook_events",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Events that trigger webhook: ['success', 'failure'] or subset",
                    ),
                ),
                (
                    "webhook_status",
                    models.CharField(
                        blank=True,
                        choices=[("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")],
                        help_text="Status of webhook delivery",
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "webhook_retries",
                    models.IntegerField(default=0, help_text="Number of webhook delivery attempts"),
                ),
                (
                    "webhook_max_retries",
                    models.IntegerField(
                        default=3, help_text="Maximum webhook delivery retry attempts"
                    ),
                ),
                (
                    "timeout_seconds",
                    models.IntegerField(
                        blank=True,
                        help_text="Maximum execution time in seconds (None = no timeout)",
                        null=True,
                    ),
                ),
                (
                    "worker_pid",
                    models.IntegerField(
                        blank=True,
                        help_text="Process ID of worker executing this job (for external kill on timeout)",
                        null=True,
                    ),
                ),
                (
                    "child_pid",
                    models.IntegerField(
                        blank=True, help_text="PID of forked child executing this job", null=True
                    ),
                ),
                (
                    "runs",
                    models.JSONField(
                        blank=True,
                        default=list,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="History of all execution attempts",
                    ),
                ),
                (
                    "meta",
                    models.JSONField(
                        blank=True,
                        default=None,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Free-form metadata dict for task functions (persisted to DB)",
                        null=True,
                    ),
                ),
                (
                    "job_name",
                    models.CharField(
                        blank=True,
                        help_text="Optional unique string identifier (e.g. 'send-invoice-123')",
                        max_length=255,
                        null=True,
                    ),
                ),
                (
                    "retry_intervals",
                    models.JSONField(
                        blank=True,
                        default=None,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                        help_text="Fixed retry delay list in seconds [5, 10, 60]. Overrides exponential backoff when set.",
                        null=True,
                    ),
                ),
                (
                    "on_success_path",
                    models.CharField(
                        blank=True, help_text="Import path for success callback", max_length=500
                    ),
                ),
                (
                    "on_failure_path",
                    models.CharField(
                        blank=True, help_text="Import path for failure callback", max_length=500
                    ),
                ),
                (
                    "ttl",
                    models.IntegerField(
                        blank=True,
                        help_text="Seconds job can stay queued before expiring (None = no limit)",
                        null=True,
                    ),
                ),
                (
                    "result_ttl",
                    models.IntegerField(
                        blank=True,
                        help_text="Seconds to keep successful result (-1 = forever, None = use global)",
                        null=True,
                    ),
                ),
                (
                    "failure_ttl",
                    models.IntegerField(
                        blank=True,
                        help_text="Seconds to keep failed job data (-1 = forever, None = use global)",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(help_text="When job was enqueued")),
                (
                    "scheduled_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When job should run (NULL = run immediately)",
                        null=True,
                    ),
                ),
                (
                    "started_at",
                    models.DateTimeField(blank=True, help_text="When execution began", null=True),
                ),
                (
                    "finished_at",
                    models.DateTimeField(
                        blank=True, help_text="When execution completed", null=True
                    ),
                ),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("output", models.TextField(blank=True, help_text="Task return value or stdout")),
                ("error", models.TextField(blank=True, help_text="Error message if failed")),
                ("traceback", models.TextField(blank=True, help_text="Full traceback if failed")),
                (
                    "termination_reason",
                    models.CharField(
                        blank=True,
                        help_text="Reason for job termination (signal, timeout, user action, etc.)",
                        max_length=100,
                    ),
                ),
                ("scheduled_task_id", models.IntegerField(blank=True, null=True)),
                ("worker_id", models.UUIDField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Job",
                "verbose_name_plural": "Jobs",
                "db_table": "sqlery_job_all",
                "ordering": ["-priority", "created_at"],
                "managed": False,
            },
        ),
        migrations.RunPython(create_view, drop_view),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name="queuedjob", name="sqlery_job_finished_desc"),
                migrations.RemoveIndex(model_name="queuedjob", name="sqlery_job_started_desc"),
            ],
            database_operations=[migrations.RunPython(drop_live_indexes, restore_live_indexes)],
        ),
    ]
