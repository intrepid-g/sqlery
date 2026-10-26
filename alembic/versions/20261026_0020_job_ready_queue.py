"""Add the ready queue: sqlery_job_ready / sqlery_job_blocked (issue #139)

Claiming reads a narrow table of claimable entries instead of scanning
sqlery_queued_job:

* sqlery_job_ready (slot, job_id, queue_name, priority, created_at, scheduled_at),
  primary key (slot, job_id). PostgreSQL: PARTITION BY LIST (slot) with partitions for
  slots 0, 1 and 2 (only slot 0 is written until rotation is enabled); SQLite: a plain
  table. Claim index (queue_name, priority DESC, created_at, job_id).
* sqlery_job_blocked (job_id, dependency_id, created_at, blocked_at): one row per
  dependency of a job whose dependencies are unmet.

Data migration: every queued job gets a ready entry (claims move dependency-blocked jobs
out of the ready table on first sight).

Because claiming no longer reads sqlery_queued_job by status, the claiming-only partial
index sqlery_job_pending_idx is dropped (no other query uses it; the status, started_at,
finished_at, worker_id and scheduled_at indexes stay, other queries need them). The
downgrade recreates it and drops both tables.

Stop all workers before upgrading: a worker started before the tables existed keeps
claiming from sqlery_queued_job and never sees (or removes) the new entries, and new
workers refuse to start without the tables. Mirrors the Django 0038_job_ready_queue
migration.

Revision ID: 20261026_0020
Revises: 20261026_0019
Create Date: 2026-10-26 00:20:00.000000
"""

import sqlalchemy as sa
from alembic import op

from sqlery.core import ready
from sqlery.tables import JOB_BLOCKED, JOB_READY

revision = "20261026_0020"
down_revision = "20261026_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)
    # Standalone start-up (init_database) creates missing tables and backfills them, so a
    # process that started on the new code before this revision ran has already done all of
    # it with the identical definitions: the statements below are idempotent.
    if dialect == "postgresql":
        statements = ready.postgres_ready_ddl()
    else:
        statements = ready.sqlite_ready_ddl()
    for statement in statements:
        op.execute(sa.text(statement))
    if not inspector.has_table("sqlery_queued_job"):
        return
    op.execute(sa.text(ready.backfill_ready_sql()))
    # Claiming no longer reads sqlery_queued_job by status.
    op.execute(sa.text("DROP INDEX IF EXISTS sqlery_job_pending_idx"))


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("sqlery_queued_job"):
        # PostgreSQL: the partial index is created on the partitioned parent and cascades.
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx "
                "ON sqlery_queued_job (queue_name, priority DESC, created_at) "
                "WHERE status = 'queued'"
            )
        )
    op.execute(sa.text(f"DROP TABLE IF EXISTS {JOB_BLOCKED}"))
    # Dropping the partitioned parent drops its partitions.
    op.execute(sa.text(f"DROP TABLE IF EXISTS {JOB_READY}"))
