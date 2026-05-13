"""Add 'shutting_down' status choice and widen QueuedJob.status to max_length=20.

Prerequisite for ASYN-05 drain-with-deadline semantics: the AsyncWorker writes the
transient 'shutting_down' state during shutdown. The string is 13 chars, so the
column is widened from 10 to 20 to leave slack for future status names.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0025_daemoncommand"),
    ]

    operations = [
        migrations.AlterField(
            model_name="queuedjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                    ("archived", "Archived"),
                    ("shutting_down", "Shutting Down"),
                ],
                db_index=True,
                default="queued",
                max_length=20,
            ),
        ),
    ]
