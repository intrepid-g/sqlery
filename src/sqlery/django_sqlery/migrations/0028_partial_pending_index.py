"""Migration 0028: concurrent index swap on sqlery_queued_job.

Removes the full composite index on (queue_name, status, priority, created_at)
and adds a partial index covering only rows where status='queued'.

atomic = False is required by AddIndexConcurrently and RemoveIndexConcurrently.

SQLite note: AddIndexConcurrently / RemoveIndexConcurrently pass a `concurrently`
keyword to the schema editor, which SQLite's backend (Django 6.x) does not accept.
The custom SafeAddIndexConcurrently and SafeRemoveIndexConcurrently wrappers
below guard the postgres-only operations behind a vendor check so SQLite CI
rails stay clean.
"""

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models


class SafeAddIndexConcurrently(AddIndexConcurrently):
    """AddIndexConcurrently that skips silently on non-PostgreSQL databases.

    Django 6.x removed the implicit SQLite guard from postgres-specific
    operations; this subclass restores safe behaviour for SQLite CI rails.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class SafeRemoveIndexConcurrently(RemoveIndexConcurrently):
    """RemoveIndexConcurrently that skips silently on non-PostgreSQL databases.

    See SafeAddIndexConcurrently for rationale.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('sqlery', '0027_rename_sqlery_daem_status_created_idx_sqlery_daem_status_8c3bf3_idx_and_more'),
    ]

    operations = [
        SafeAddIndexConcurrently(
            model_name='queuedjob',
            index=models.Index(
                fields=['queue_name', '-priority', 'created_at'],
                name='sqlery_job_pending_idx',
                condition=models.Q(status='queued'),
            ),
        ),
        SafeRemoveIndexConcurrently(
            model_name='queuedjob',
            name='sqlery_queu_queue_n_5c87d6_idx',
        ),
    ]
