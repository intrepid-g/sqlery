# Migration for v0.15: add meta, job_name, and retry_intervals fields to QueuedJob

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqlery', '0014_schedule_types'),
    ]

    operations = [
        # Add meta field for free-form metadata dict
        migrations.AddField(
            model_name='queuedjob',
            name='meta',
            field=models.JSONField(
                null=True, blank=True, default=None,
                help_text='Free-form metadata dict for task functions (persisted to DB)',
            ),
        ),
        # Add job_name for optional unique string identifiers
        migrations.AddField(
            model_name='queuedjob',
            name='job_name',
            field=models.CharField(
                max_length=255, null=True, blank=True, unique=True, db_index=True,
                help_text="Optional unique string identifier (e.g. 'send-invoice-123')",
            ),
        ),
        # Add retry_intervals for fixed retry delay lists
        migrations.AddField(
            model_name='queuedjob',
            name='retry_intervals',
            field=models.JSONField(
                null=True, blank=True, default=None,
                help_text='Fixed retry delay list in seconds [5, 10, 60]. Overrides exponential backoff when set.',
            ),
        ),
    ]
