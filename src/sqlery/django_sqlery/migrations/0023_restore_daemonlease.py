"""No-op migration — preserves graph node for users who applied earlier
conditional-RunPython versions. The original "Create-if-missing" logic is
unnecessary because 0020 created the table and 0022 never deleted it
(filename intent vs operations list mismatch).

See `.planning/phases/03-testing-ci/03-01-PLAN.md` for the full root cause.

Why we keep this file rather than deleting it: deployed databases may have
already recorded `0023_restore_daemonlease` in `django_migrations`. Removing
the file would break their migration graph. Reducing it to `operations = []`
keeps the graph resolvable on both fresh and previously-migrated databases.
"""
from django.db import migrations


class Migration(migrations.Migration):
    """No-op preserved for backward compatibility (see module docstring)."""

    dependencies = [
        ('sqlery', '0022_delete_daemonlease_alter_jobregistry_metadata_and_more'),
    ]

    operations = []

    # #CLEANUP 2026-05-14: dead — remove after Phase 4.
    # Original unconditional CreateModel that crashed when table already existed,
    # then replaced with a conditional RunPython (which worked for `manage.py
    # migrate` but NOT for pytest-django's `setup_databases`, hence D-02-07-1).
    # Kept here for archaeological reference.
    #
    # def create_daemon_lease_if_missing(apps, schema_editor):
    #     ...
    # operations = [
    #     migrations.CreateModel(
    #         name='DaemonLease',
    #         fields=[
    #             ('queue_name', models.CharField(max_length=255, primary_key=True, serialize=False)),
    #             ('daemon_id', models.CharField(help_text='daemon_{node_id}_{pid}', max_length=255)),
    #             ('node_id', models.CharField(max_length=255)),
    #             ('pid', models.IntegerField()),
    #             ('acquired_at', models.DateTimeField()),
    #             ('expires_at', models.DateTimeField(db_index=True)),
    #         ],
    #         options={
    #             'verbose_name': 'Daemon Lease',
    #             'verbose_name_plural': 'Daemon Leases',
    #             'db_table': 'sqlery_daemon_lease',
    #         },
    #     ),
    #     migrations.RunPython(create_daemon_lease_if_missing, migrations.RunPython.noop),
    # ]
