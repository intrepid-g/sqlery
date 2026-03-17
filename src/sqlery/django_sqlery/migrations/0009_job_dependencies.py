from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sqlery", "0008_add_tags_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="queuedjob",
            name="dependencies",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text="List of job IDs that must complete successfully before this job can run",
            ),
        ),
    ]
