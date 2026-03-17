"""Add parent_job_id field and archived status to QueuedJob."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqlery', '0020_daemon_lease'),
    ]

    operations = [
        migrations.AddField(
            model_name='queuedjob',
            name='parent_job_id',
            field=models.IntegerField(
                blank=True,
                db_index=True,
                help_text='ID of the failed job this retry was created from (links retry chain)',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='queuedjob',
            name='status',
            field=models.CharField(
                choices=[
                    ('queued', 'Queued'),
                    ('running', 'Running'),
                    ('success', 'Success'),
                    ('failed', 'Failed'),
                    ('archived', 'Archived'),
                ],
                db_index=True,
                default='queued',
                max_length=10,
            ),
        ),
    ]
