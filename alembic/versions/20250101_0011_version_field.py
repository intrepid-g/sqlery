"""Add version field for optimistic locking

Revision ID: 20250101_0011
Revises: 20250101_0010
Create Date: 2025-01-01 00:11:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB

revision = '20250101_0011'
down_revision = '20250101_0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(QUEUED_JOB, sa.Column('version', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column(QUEUED_JOB, 'version')
