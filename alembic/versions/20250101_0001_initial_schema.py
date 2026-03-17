"""Initial schema for sqlery

Revision ID: 20250101_0001
Revises:
Create Date: 2025-01-01 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa
# from sqlalchemy.dialects import postgresql
from sqlery.tables import QUEUED_JOB, SCHEDULED_TASK, WORKER, REGISTRY

# revision identifiers, used by Alembic.
revision = '20250101_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all sqlery tables."""

    # Create queued_job table
    op.create_table(
        # 'sqlery_queued_job',
        QUEUED_JOB,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_path', sa.String(length=255), nullable=False),
        # sa.Column('kwargs', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('kwargs', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('queue_name', sa.String(length=100), nullable=False, server_default='default'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='queued'),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('retry_backoff', sa.Float(), nullable=False, server_default='30.0'),
        sa.Column('allow_parallel', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('timeout_seconds', sa.Integer(), nullable=True),
        sa.Column('worker_pid', sa.Integer(), nullable=True),
        sa.Column('output', sa.Text(), nullable=False, server_default=''),
        sa.Column('error', sa.Text(), nullable=False, server_default=''),
        sa.Column('traceback', sa.Text(), nullable=False, server_default=''),
        sa.Column('termination_reason', sa.String(length=100), nullable=False, server_default=''),
        # sa.Column('runs', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('runs', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('scheduled_task_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_queued_job_status', 'status'),
        sa.Index('idx_queued_job_queue_status', 'queue_name', 'status'),
        sa.Index('idx_queued_job_scheduled_at', 'scheduled_at'),
        sa.Index('idx_queued_job_priority_created', 'priority', 'created_at'),
    )

    # Create scheduled_task table
    op.create_table(
        # 'sqlery_scheduled_task',
        SCHEDULED_TASK,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('task_path', sa.String(length=255), nullable=False),
        sa.Column('cron_expression', sa.String(length=100), nullable=False),
        sa.Column('queue_name', sa.String(length=100), nullable=False, server_default='default'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.Index('idx_scheduled_task_enabled_next_run', 'enabled', 'next_run_at'),
    )

    # Create registry table
    op.create_table(
        # 'sqlery_registry',
        REGISTRY,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('registry_type', sa.String(length=50), nullable=False),
        sa.Column('entered_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('exited_at', sa.DateTime(timezone=True), nullable=True),
        # sa.Column('metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('metadata', sa.JSON(), nullable=False, server_default='{}'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_registry_job_type', 'job_id', 'registry_type'),
        sa.Index('idx_registry_type_exited', 'registry_type', 'exited_at'),
    )

    # Create worker table
    op.create_table(
        # 'sqlery_worker',
        WORKER,
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='idle'),
        # sa.Column('queues', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'),
        sa.Column('queues', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('current_job_id', sa.Integer(), nullable=True),
        sa.Column('last_heartbeat', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('jobs_processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_worker_last_heartbeat', 'last_heartbeat'),
    )


def downgrade() -> None:
    """Drop all sqlery tables."""
    # op.drop_table('sqlery_worker')
    # op.drop_table('sqlery_registry')
    # op.drop_table('sqlery_scheduled_task')
    # op.drop_table('sqlery_queued_job')
    op.drop_table(WORKER)
    op.drop_table(REGISTRY)
    op.drop_table(SCHEDULED_TASK)
    op.drop_table(QUEUED_JOB)
