from django.db import migrations, models


def create_daemon_lease_if_missing(apps, schema_editor):
    """Create DaemonLease table only if it doesn't already exist.

    Migration 0022 was generated with a DeleteModel for DaemonLease in the
    filename but the actual operation was removed before commit. The table
    therefore still exists from 0020 and this migration is a no-op.
    """
    connection = schema_editor.connection
    # Use cursor directly to avoid introspection cache returning stale results
    # during a migration run (table_names() may miss tables created earlier
    # in the same migrate session on SQLite in-memory databases).
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlery_daemon_lease'"
            if connection.vendor == 'sqlite'
            else "SELECT 1 FROM information_schema.tables WHERE table_name='sqlery_daemon_lease'"
        )
        table_exists = cursor.fetchone() is not None
    except Exception:
        table_exists = 'sqlery_daemon_lease' in connection.introspection.table_names()
    if not table_exists:
        schema_editor.create_model(apps.get_model('sqlery', 'DaemonLease'))


class Migration(migrations.Migration):
    """Restore DaemonLease table if it was dropped by 0022 (conditional)."""

    dependencies = [
        ('sqlery', '0022_delete_daemonlease_alter_jobregistry_metadata_and_more'),
    ]

    operations = [
        # # Old: unconditional CreateModel that crashes when table already exists
        # migrations.CreateModel(
        #     name='DaemonLease',
        #     fields=[
        #         ('queue_name', models.CharField(max_length=255, primary_key=True, serialize=False)),
        #         ('daemon_id', models.CharField(help_text='daemon_{node_id}_{pid}', max_length=255)),
        #         ('node_id', models.CharField(max_length=255)),
        #         ('pid', models.IntegerField()),
        #         ('acquired_at', models.DateTimeField()),
        #         ('expires_at', models.DateTimeField(db_index=True)),
        #     ],
        #     options={
        #         'verbose_name': 'Daemon Lease',
        #         'verbose_name_plural': 'Daemon Leases',
        #         'db_table': 'sqlery_daemon_lease',
        #     },
        # ),
        migrations.RunPython(
            create_daemon_lease_if_missing,
            migrations.RunPython.noop,
        ),
    ]
