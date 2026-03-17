# Migration to add TagLock table for race-condition-free rate limiting
#
# This table provides coordination points for tag-based constraints.
# Workers acquire locks on tag rows before checking rate limits,
# ensuring atomic check-and-claim operations.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sqlery", "0006_add_rate_limit_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="TagLock",
            fields=[
                (
                    "tag",
                    models.CharField(
                        max_length=255,
                        primary_key=True,
                        serialize=False,
                        help_text="Tag name used in job.tags field",
                    ),
                ),
            ],
            options={
                "db_table": "sqlery_tag_lock",
                "verbose_name": "Tag Lock",
                "verbose_name_plural": "Tag Locks",
            },
        ),
    ]
