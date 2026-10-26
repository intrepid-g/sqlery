"""Create ``sqlery_job_attempt``: one fenced row per execution attempt (issue #138).

``job_id`` is a plain indexed column, not a foreign key: on partitioned PostgreSQL the
``sqlery_queued_job`` primary key is ``(created_at, id)``. Pure ``CreateModel``, so the
reverse drops the table.

Stop all workers before migrating: a worker started before this table existed does not
record attempts, and new workers refuse to start without it.
"""

import django
import django.utils.timezone
from django.db import migrations, models

# CheckConstraint's ``check`` argument became ``condition`` in Django 5.1.
_OUTCOME_Q = models.Q(
    ("outcome__isnull", True), ("outcome__in", ["success", "failed", "abandoned"]), _connector="OR"
)
_OUTCOME_CHECK = models.CheckConstraint(
    name="sqlery_attempt_outcome_chk",
    **{("condition" if django.VERSION >= (5, 1) else "check"): _OUTCOME_Q},
)


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0036_drop_redundant_job_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobAttempt",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("job_id", models.BigIntegerField(help_text="Claimed job (no FK: partitioned PK)")),
                (
                    "token",
                    models.CharField(
                        help_text="Random fencing token of this attempt", max_length=36
                    ),
                ),
                (
                    "worker_id",
                    models.UUIDField(
                        blank=True, help_text="Claiming worker, if registered", null=True
                    ),
                ),
                (
                    "worker_pid",
                    models.IntegerField(
                        blank=True, help_text="PID of the claiming process", null=True
                    ),
                ),
                (
                    "child_pid",
                    models.IntegerField(blank=True, help_text="PID of the forked child", null=True),
                ),
                ("claimed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "outcome",
                    models.CharField(
                        blank=True,
                        help_text="success / failed / abandoned",
                        max_length=10,
                        null=True,
                    ),
                ),
            ],
            options={
                "db_table": "sqlery_job_attempt",
                "indexes": [
                    models.Index(fields=["job_id"], name="sqlery_attempt_job_idx"),
                    models.Index(
                        condition=models.Q(("finished_at__isnull", True)),
                        fields=["worker_id"],
                        name="sqlery_attempt_open_worker_idx",
                    ),
                ],
                "constraints": [_OUTCOME_CHECK],
            },
        ),
    ]
