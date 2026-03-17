"""Add standalone timestamp indexes for dashboard time-range queries.

Indexes on -created_at, -finished_at, -started_at improve:
- Activity feed OR-filter (api_views.py) — enables BitmapOr index scans
- dashboard_stats order_by('-created_at')[:50] — index-only scan
- dashboard_stats created_at__gte aggregate — skips old rows via index
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0023_restore_daemonlease"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="queuedjob",
            index=models.Index(fields=["-created_at"], name="sqlery_job_created_desc"),
        ),
        migrations.AddIndex(
            model_name="queuedjob",
            index=models.Index(fields=["-finished_at"], name="sqlery_job_finished_desc"),
        ),
        migrations.AddIndex(
            model_name="queuedjob",
            index=models.Index(fields=["-started_at"], name="sqlery_job_started_desc"),
        ),
    ]
