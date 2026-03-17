"""Add total_busy_seconds field to Worker for parent-tracked utilization."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0018_queuedjob_child_pid"),
    ]

    operations = [
        migrations.AddField(
            model_name="worker",
            name="total_busy_seconds",
            field=models.FloatField(
                default=0.0,
                help_text="Cumulative seconds spent executing jobs (tracked by parent process)",
            ),
        ),
    ]
