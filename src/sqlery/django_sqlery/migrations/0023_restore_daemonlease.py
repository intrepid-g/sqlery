from django.db import migrations, models


class Migration(migrations.Migration):
    """Restore DaemonLease table dropped by the erroneous DeleteModel in 0022."""

    dependencies = [
        ('sqlery', '0022_delete_daemonlease_alter_jobregistry_metadata_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='DaemonLease',
            fields=[
                ('queue_name', models.CharField(max_length=255, primary_key=True, serialize=False)),
                ('daemon_id', models.CharField(help_text='daemon_{node_id}_{pid}', max_length=255)),
                ('node_id', models.CharField(max_length=255)),
                ('pid', models.IntegerField()),
                ('acquired_at', models.DateTimeField()),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
            options={
                'verbose_name': 'Daemon Lease',
                'verbose_name_plural': 'Daemon Leases',
                'db_table': 'sqlery_daemon_lease',
            },
        ),
    ]
