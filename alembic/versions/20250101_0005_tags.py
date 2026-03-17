"""Add tags to queued_job

Revision ID: 20250101_0005
Revises: 20250101_0004
Create Date: 2025-01-01 00:05:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB

revision = '20250101_0005'
down_revision = '20250101_0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(QUEUED_JOB, sa.Column('tags', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column(QUEUED_JOB, 'tags')
