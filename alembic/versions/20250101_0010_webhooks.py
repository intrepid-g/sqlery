"""Add webhook fields

Revision ID: 20250101_0010
Revises: 20250101_0009
Create Date: 2025-01-01 00:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB

revision = '20250101_0010'
down_revision = '20250101_0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(QUEUED_JOB, sa.Column('webhook_url', sa.String(length=500), nullable=True))
    op.add_column(QUEUED_JOB, sa.Column('webhook_events', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column(QUEUED_JOB, sa.Column('webhook_status', sa.String(length=20), nullable=True))
    op.add_column(QUEUED_JOB, sa.Column('webhook_retries', sa.Integer(), nullable=False, server_default='0'))
    op.add_column(QUEUED_JOB, sa.Column('webhook_max_retries', sa.Integer(), nullable=False, server_default='3'))


def downgrade() -> None:
    op.drop_column(QUEUED_JOB, 'webhook_max_retries')
    op.drop_column(QUEUED_JOB, 'webhook_retries')
    op.drop_column(QUEUED_JOB, 'webhook_status')
    op.drop_column(QUEUED_JOB, 'webhook_events')
    op.drop_column(QUEUED_JOB, 'webhook_url')
