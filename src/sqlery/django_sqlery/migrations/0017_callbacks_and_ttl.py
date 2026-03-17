"""Add callback paths and TTL fields to QueuedJob."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0016_add_worker_paused_until"),
    ]

    operations = [
        migrations.AddField(
            model_name="queuedjob",
            name="on_success_path",
            field=models.CharField(
                blank=True, default="", max_length=500,
                help_text="Import path for success callback",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="on_failure_path",
            field=models.CharField(
                blank=True, default="", max_length=500,
                help_text="Import path for failure callback",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="ttl",
            field=models.IntegerField(
                null=True, blank=True,
                help_text="Seconds job can stay queued before expiring (None = no limit)",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="result_ttl",
            field=models.IntegerField(
                null=True, blank=True,
                help_text="Seconds to keep successful result (-1 = forever, None = use global)",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="failure_ttl",
            field=models.IntegerField(
                null=True, blank=True,
                help_text="Seconds to keep failed job data (None = use global)",
            ),
        ),
    ]
