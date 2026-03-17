"""Rename indexes and alter fields

Revision ID: 20250101_0012
Revises: 20250101_0011
Create Date: 2025-01-01 00:12:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB, SCHEDULED_TASK

revision = '20250101_0012'
down_revision = '20250101_0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop indexes that were added in 0006 and 0008 (will be recreated with different names or removed)
    op.drop_index('idx_queuedjob_rate_limit', table_name=QUEUED_JOB)
    op.drop_index('idx_queuedjob_tags', table_name=QUEUED_JOB)

    # Make ScheduledTask.next_run_at nullable
    op.alter_column(SCHEDULED_TASK, 'next_run_at', existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade() -> None:
    op.alter_column(SCHEDULED_TASK, 'next_run_at', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.create_index('idx_queuedjob_tags', QUEUED_JOB, ['tags'])
    op.create_index('idx_queuedjob_rate_limit', QUEUED_JOB, ['started_at', 'status'])
