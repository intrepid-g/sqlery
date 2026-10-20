"""Widen sqlery_worker.current_job_id to BIGINT

Standalone job ids are 62-bit (UUIDv7-derived); an INTEGER column made every busy
heartbeat fail with "integer out of range" on PostgreSQL.

Revision ID: 20260620_0017
Revises: 20260612_0016
Create Date: 2026-06-20 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = '20260620_0017'
down_revision = '20260612_0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('sqlery_worker') as batch_op:
        batch_op.alter_column(
            'current_job_id',
            type_=sa.BigInteger(),
            existing_type=sa.Integer(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('sqlery_worker') as batch_op:
        batch_op.alter_column(
            'current_job_id',
            type_=sa.Integer(),
            existing_type=sa.BigInteger(),
            existing_nullable=True,
        )
