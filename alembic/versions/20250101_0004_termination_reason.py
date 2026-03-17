"""Add termination_reason to queued_job

Revision ID: 20250101_0004
Revises: 20250101_0003
Create Date: 2025-01-01 00:04:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB

revision = '20250101_0004'
down_revision = '20250101_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(QUEUED_JOB, sa.Column('termination_reason', sa.String(length=100), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column(QUEUED_JOB, 'termination_reason')
