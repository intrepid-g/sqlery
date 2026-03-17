# Migration to add index on tags field for performance
#
# The tags field is queried frequently for:
# - Concurrency limit checks: tags__contains=[tag]
# - Rate limit checks: tags__contains=[tag]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sqlery", "0007_tag_lock_table"),
    ]

    operations = [
        # Note: For PostgreSQL, ideally we'd use GinIndex for better JSON performance
        # For now using a regular index which works across all databases
        migrations.AddIndex(
            model_name='queuedjob',
            index=models.Index(
                fields=['tags'],
                name='idx_queuedjob_tags'
            ),
        ),
    ]
