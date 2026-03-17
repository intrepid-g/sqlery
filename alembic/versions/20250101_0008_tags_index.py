"""Add tags index

Revision ID: 20250101_0008
Revises: 20250101_0007
Create Date: 2025-01-01 00:08:00.000000
"""
from alembic import op
from sqlery.tables import QUEUED_JOB

revision = '20250101_0008'
down_revision = '20250101_0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('idx_queuedjob_tags', QUEUED_JOB, ['tags'])


def downgrade() -> None:
    op.drop_index('idx_queuedjob_tags', table_name=QUEUED_JOB)
