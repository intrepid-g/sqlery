"""Claim without an UPDATE of the job row (issue #157)

A claim used to delete the ready entry, UPDATE the job row (status, started_at, worker,
version) and insert an attempt. The UPDATE left a dead version of a wide row per job and
rewrote index entries. Now a claim deletes the entry and inserts the attempt; the job row is
written only at enqueue and at completion. A job is running iff it has an open attempt
(finished_at IS NULL); its row keeps status 'queued'.

* sqlery_job_all is replaced by the attempt-derived view: a queued row with an open attempt
  reads as 'running', with started_at / worker_id / worker_pid / child_pid from the attempt.
* sqlery_attempt_open_job_idx: partial index of the open attempts by job (the running jobs).
* Data: rows stored as 'running' become 'queued' rows with an open attempt (one is built for a
  row that has none).

Stop all workers before upgrading: a worker started before this revision still claims with
the UPDATE and would not be seen as running by the new readers. The downgrade writes the
running rows back (status, start, worker and PIDs from the open attempt).
Mirrors the Django 0042_claim_without_update migration.

Revision ID: 20261026_0023
Revises: 20261026_0022
Create Date: 2026-10-26 00:23:00.000000
"""

import sqlalchemy as sa

from alembic import op
from sqlery.core import history
from sqlery.core.models import JOB_COLUMNS
from sqlery.tables import JOB_ATTEMPT

revision = "20261026_0023"
down_revision = "20261026_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.execute(sa.text(history.open_job_index_ddl()))
    for statement in history.normalize_running_sql(dialect):
        op.execute(sa.text(statement))
    for statement in history.view_columns_ddl(JOB_COLUMNS, dialect, with_attempts=True):
        op.execute(sa.text(statement))


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    for statement in history.view_columns_ddl(JOB_COLUMNS, dialect, with_attempts=False):
        op.execute(sa.text(statement))
    for statement in history.denormalize_running_sql():
        op.execute(sa.text(statement))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {history.ATTEMPT_OPEN_JOB_INDEX}"))
