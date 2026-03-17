"""Add job registry table

Revision ID: 20250101_0003
Revises: 20250101_0002
Create Date: 2025-01-01 00:03:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlery.tables import REGISTRY, QUEUED_JOB

revision = '20250101_0003'
down_revision = '20250101_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        REGISTRY,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('registry_type', sa.String(length=20), nullable=False),
        sa.Column('entered_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('exited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=False, server_default='{}'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['job_id'], [f'{QUEUED_JOB}.id'], ondelete='CASCADE'),
        sa.Index('idx_registry_job_type', 'job_id', 'registry_type'),
        sa.Index('idx_registry_type_entered', 'registry_type', 'entered_at'),
        sa.Index('idx_registry_type_exited', 'registry_type', 'exited_at'),
    )


def downgrade() -> None:
    op.drop_table(REGISTRY)
