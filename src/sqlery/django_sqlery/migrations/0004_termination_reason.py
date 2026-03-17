# Generated migration for termination_reason field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sqlery', '0003_jobregistry'),
    ]

    operations = [
        migrations.AddField(
            model_name='queuedjob',
            name='termination_reason',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text='Reason for job termination (signal, timeout, user action, etc.)',
            ),
        ),
    ]
