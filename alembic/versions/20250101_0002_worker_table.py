"""Add worker table and worker FK to queued_job

Revision ID: 20250101_0002
Revises: 20250101_0001
Create Date: 2025-01-01 00:02:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import QUEUED_JOB, WORKER

revision = '20250101_0002'
down_revision = '20250101_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        WORKER,
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='idle'),
        sa.Column('queues', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('current_job_id', sa.Integer(), nullable=True),
        sa.Column('last_heartbeat', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('jobs_processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_worker_node_id_status', 'node_id', 'status'),
        sa.Index('idx_worker_status_heartbeat', 'status', 'last_heartbeat'),
        sa.Index('idx_worker_last_heartbeat', 'last_heartbeat'),
    )

    op.add_column(QUEUED_JOB, sa.Column('worker_id', sa.String(length=36), nullable=True))
    op.create_foreign_key('fk_job_worker', QUEUED_JOB, WORKER, ['worker_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_job_worker', QUEUED_JOB, type_='foreignkey')
    op.drop_column(QUEUED_JOB, 'worker_id')
    op.drop_table(WORKER)
