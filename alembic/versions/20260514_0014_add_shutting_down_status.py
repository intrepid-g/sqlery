"""Widen QueuedJob.status to String(20) for 'shutting_down' state

Prerequisite for ASYN-05 drain-with-deadline semantics: the AsyncWorker writes
the transient 'shutting_down' state (13 chars) during shutdown. The status
column is widened from 10 to 20 to leave slack for future status names.

Revision ID: 20260514_0014
Revises: 20250101_0013
Create Date: 2026-05-14 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from sqlery.tables import QUEUED_JOB

revision = '20260514_0014'
down_revision = '20250101_0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use batch mode so SQLite (which lacks native ALTER COLUMN TYPE) can
    # apply this via copy-and-move; Postgres handles it natively.
    with op.batch_alter_table(QUEUED_JOB) as batch_op:
        batch_op.alter_column(
            'status',
            type_=sa.String(20),
            existing_type=sa.String(10),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table(QUEUED_JOB) as batch_op:
        batch_op.alter_column(
            'status',
            type_=sa.String(10),
            existing_type=sa.String(20),
            existing_nullable=False,
        )
