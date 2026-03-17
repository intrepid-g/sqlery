"""Add unique constraint on Worker (node_id, pid)

Revision ID: 20250101_0013
Revises: 20250101_0012
Create Date: 2025-01-01 00:13:00.000000
"""
from alembic import op
from sqlery.tables import WORKER

revision = '20250101_0013'
down_revision = '20250101_0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint('unique_worker_per_node_pid', WORKER, ['node_id', 'pid'])


def downgrade() -> None:
    op.drop_constraint('unique_worker_per_node_pid', WORKER, type_='unique')
