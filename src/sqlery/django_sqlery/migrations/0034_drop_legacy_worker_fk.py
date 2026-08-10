"""Drop all outgoing FK constraints from sqlery_queued_job_legacy.

Migration 0030's S0 step drops FK constraints that *reference*
sqlery_queued_job (i.e. constraints on other tables pointing at it, such as
JobRegistry.job_id and Worker.current_job_id) before the rename to
sqlery_queued_job_legacy. It does not touch the opposite direction: FK
constraints where queued_job is the *referencing* table, e.g.
sqlery_queued_job_worker_id_fkey (-> sqlery_worker) and
sqlery_queued_job_scheduled_task_id_fkey (-> sqlery_scheduled_task). Those
constraints survive the rename intact and end up on
sqlery_queued_job_legacy, still pointing at their target tables.

sqlery_queued_job_legacy is dormant after cutover (D3 soak period) -- no code
path reads or writes it, and 0030's backward() rebuilds an unpartitioned table
straight from the live partitioned sqlery_queued_job, never touching legacy.
So these FKs serve no runtime purpose. But because the legacy table has no
Django model (it was created by raw SQL), it is invisible to Django's flush/
TRUNCATE table discovery -- yet Postgres still enforces the constraints. Any
TRUNCATE of sqlery_worker or sqlery_scheduled_task (e.g. Django test-DB flush
between tests) then fails with FeatureNotSupported: cannot truncate a table
referenced in a foreign key constraint. That failed flush leaks rows (notably
Worker rows keyed by real hostname+pid) across tests.

Dropping the constraints here (root cause) removes the enforcement without
touching the legacy table's data or its columns -- rollback/soak inspection of
sqlery_queued_job_legacy is unaffected. All outgoing FKs are dropped
generically (not hard-coded to worker/scheduled_task) so any other legacy FK
survivors are covered too.

PostgreSQL only. SQLite never executed 0030's rename, so there is no legacy
table there.
"""

from django.db import migrations


def drop_legacy_outgoing_fks(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            DO $$
            DECLARE
                r RECORD;
            BEGIN
                FOR r IN
                    SELECT tc.constraint_name
                    FROM information_schema.table_constraints tc
                    WHERE tc.table_name = 'sqlery_queued_job_legacy'
                      AND tc.constraint_type = 'FOREIGN KEY'
                LOOP
                    EXECUTE format(
                        'ALTER TABLE sqlery_queued_job_legacy DROP CONSTRAINT IF EXISTS %I',
                        r.constraint_name
                    );
                END LOOP;
            END $$;
            """
        )


def noop_backward(apps, schema_editor):
    # Constraints are not recreated on reverse -- they served no runtime
    # purpose and their absence does not affect 0030's backward() rebuild path.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0033_widen_queuedjob_parent_job_id"),
    ]

    operations = [
        migrations.RunPython(drop_legacy_outgoing_fks, noop_backward, atomic=False),
    ]
