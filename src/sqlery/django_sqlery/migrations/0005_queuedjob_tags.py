# Generated migration for tag-based concurrency feature

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sqlery", "0004_termination_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="queuedjob",
            name="tags",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Tags for concurrency limiting (e.g., ['acme-api', 'rate-limited'])",
            ),
        ),
    ]
