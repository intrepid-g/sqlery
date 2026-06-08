"""Add daemon lease table

Creates sqlery_daemon_lease for standalone queue-scoped scheduler/daemon
ownership leases (DB-backed leader election). Mirrors the Django DaemonLease
model plus a version column for SQLite CAS.

Revision ID: 20260608_0015
Revises: 20260514_0014
Create Date: 2026-06-08 00:15:00.000000
"""
import sqlalchemy as sa
from alembic import op

from sqlery.tables import DAEMON_LEASE

revision = '20260608_0015'
down_revision = '20260514_0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        DAEMON_LEASE,
        sa.Column('queue_name', sa.String(length=255), nullable=False),
        sa.Column('daemon_id', sa.String(length=255), nullable=False),
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('acquired_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('queue_name'),
    )
    op.create_index('ix_sqlery_daemon_lease_expires_at', DAEMON_LEASE, ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_sqlery_daemon_lease_expires_at', table_name=DAEMON_LEASE)
    op.drop_table(DAEMON_LEASE)
