# Migration for v0.13: schedule_type, interval, once, and task_kwargs fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqlery', '0013_worker_unique_worker_per_node_pid'),
    ]

    operations = [
        # Add schedule_type field (default='cron' for backward compat)
        migrations.AddField(
            model_name='scheduledtask',
            name='schedule_type',
            field=models.CharField(
                max_length=10,
                choices=[
                    ('cron', 'Cron'),
                    ('interval', 'Interval'),
                    ('once', 'Once'),
                ],
                default='cron',
                help_text='Type of schedule: cron, interval, or once',
            ),
        ),
        # Add task_kwargs field
        migrations.AddField(
            model_name='scheduledtask',
            name='task_kwargs',
            field=models.JSONField(
                default=dict, blank=True,
                help_text='Keyword arguments to pass to the task callable on each run',
            ),
        ),
        # Make cron_expression nullable (required only for cron type)
        migrations.AlterField(
            model_name='scheduledtask',
            name='cron_expression',
            field=models.CharField(
                max_length=100, null=True, blank=True,
                help_text='Cron expression (e.g., \'0 2 * * *\' for 2 AM daily). Required for cron type.',
            ),
        ),
        # Add interval fields
        migrations.AddField(
            model_name='scheduledtask',
            name='interval',
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text='Interval amount (e.g., 5 for \'every 5 minutes\'). Required for interval type.',
            ),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='interval_unit',
            field=models.CharField(
                max_length=10,
                choices=[
                    ('seconds', 'Seconds'),
                    ('minutes', 'Minutes'),
                    ('hours', 'Hours'),
                    ('days', 'Days'),
                    ('weeks', 'Weeks'),
                ],
                null=True, blank=True, default='minutes',
                help_text='Interval unit: seconds, minutes, hours, days, or weeks',
            ),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='repeat',
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text='Number of times to repeat (null = indefinitely). For interval type.',
            ),
        ),
        # Add scheduled_time for once type
        migrations.AddField(
            model_name='scheduledtask',
            name='scheduled_time',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Exact datetime to run the task once. Required for once type.',
            ),
        ),
        # Add index for schedule_type
        migrations.AddIndex(
            model_name='scheduledtask',
            index=models.Index(
                fields=['schedule_type', 'enabled'],
                name='sqlery_sche_schedul_idx',
            ),
        ),
    ]
