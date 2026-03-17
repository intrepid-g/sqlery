from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sqlery", "0009_job_dependencies"),
    ]

    operations = [
        migrations.AddField(
            model_name="queuedjob",
            name="webhook_url",
            field=models.URLField(
                max_length=500,
                null=True,
                blank=True,
                help_text="URL to call when job completes (success or failure)",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="webhook_events",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text="Events that trigger webhook: ['success', 'failure'] or subset",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="webhook_status",
            field=models.CharField(
                max_length=20,
                null=True,
                blank=True,
                choices=[
                    ("pending", "Pending"),
                    ("sent", "Sent"),
                    ("failed", "Failed"),
                ],
                help_text="Status of webhook delivery",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="webhook_retries",
            field=models.IntegerField(
                default=0,
                help_text="Number of webhook delivery attempts",
            ),
        ),
        migrations.AddField(
            model_name="queuedjob",
            name="webhook_max_retries",
            field=models.IntegerField(
                default=3,
                help_text="Maximum webhook delivery retry attempts",
            ),
        ),
    ]
