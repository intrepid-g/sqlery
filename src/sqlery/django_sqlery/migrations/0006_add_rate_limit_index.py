# Migration to add database index for rate limit performance
#
# This index optimizes the rate limit check query:
# - started_at: Used for time window filtering (WHERE started_at >= threshold)
# - status: Used for filtering job states (WHERE status IN ['running', 'success', 'failed'])

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sqlery", "0005_queuedjob_tags"),
    ]

    operations = [
        migrations.AddIndex(
            model_name='queuedjob',
            index=models.Index(
                fields=['started_at', 'status'],
                name='idx_queuedjob_rate_limit'
            ),
        ),
    ]
