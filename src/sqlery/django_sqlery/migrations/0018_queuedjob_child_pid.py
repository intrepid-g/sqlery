"""Add child_pid field to QueuedJob for fork-per-job process tracking."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0017_callbacks_and_ttl"),
    ]

    operations = [
        migrations.AddField(
            model_name="queuedjob",
            name="child_pid",
            field=models.IntegerField(
                blank=True,
                help_text="PID of forked child executing this job",
                null=True,
            ),
        ),
    ]
