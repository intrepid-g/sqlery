# Generated migration for optimistic locking version field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqlery', '0010_webhooks'),
    ]

    operations = [
        migrations.AddField(
            model_name='queuedjob',
            name='version',
            field=models.IntegerField(
                default=0,
                help_text='Optimistic locking version for atomic job claiming (increments on each update)'
            ),
        ),
    ]
