"""Add rate limit index

Revision ID: 20250101_0006
Revises: 20250101_0005
Create Date: 2025-01-01 00:06:00.000000
"""
from alembic import op
from sqlery.tables import QUEUED_JOB

revision = '20250101_0006'
down_revision = '20250101_0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('idx_queuedjob_rate_limit', QUEUED_JOB, ['started_at', 'status'])


def downgrade() -> None:
    op.drop_index('idx_queuedjob_rate_limit', table_name=QUEUED_JOB)
