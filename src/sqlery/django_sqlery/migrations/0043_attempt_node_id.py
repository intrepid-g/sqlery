"""Record the claiming node on each attempt (issue #47).

``sqlery_job_attempt.node_id`` names the node whose process claimed the job. A PID is only
meaningful on that node: the daemonless reaper probes, and the stale-job pass kills, a PID
only when the attempt's node is the current one. Without it, a second web container reaped a
job that was running on the first one (its PID was missing in the second container's PID
namespace) and could signal an unrelated local process that happened to reuse the PID.

Nullable; rows claimed before this migration have no node and are never probed or killed
(they fall back to the time threshold). The column is added only when absent: a PostgreSQL
attempt table created by migration 0039 from the current DDL already has it. Reversible.
Mirrors the Alembic ``20261126_0024`` revision.
"""

from django.db import migrations, models

from sqlery.tables import JOB_ATTEMPT

FIELD = "node_id"


def _has_column(schema_editor) -> bool:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, JOB_ATTEMPT)
    return any(column.name == FIELD for column in description)


def add_column(apps, schema_editor):
    if not _has_column(schema_editor):
        model = apps.get_model("sqlery", "JobAttempt")
        schema_editor.add_field(model, model._meta.get_field(FIELD))


def drop_column(apps, schema_editor):
    if _has_column(schema_editor):
        model = apps.get_model("sqlery", "JobAttempt")
        schema_editor.remove_field(model, model._meta.get_field(FIELD))


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0042_claim_without_update"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="jobattempt",
                    name=FIELD,
                    field=models.CharField(
                        blank=True,
                        help_text=(
                            "Node of the claiming process: its PIDs mean nothing on another "
                            "node (#47)"
                        ),
                        max_length=255,
                        null=True,
                    ),
                ),
            ],
        ),
        # After the state change, so the model the functions read has the field.
        migrations.RunPython(add_column, drop_column),
    ]
