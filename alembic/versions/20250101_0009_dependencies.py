"""Add job dependencies

Revision ID: 20250101_0009
Revises: 20250101_0008
Create Date: 2025-01-01 00:09:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB

revision = '20250101_0009'
down_revision = '20250101_0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(QUEUED_JOB, sa.Column('dependencies', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column(QUEUED_JOB, 'dependencies')
