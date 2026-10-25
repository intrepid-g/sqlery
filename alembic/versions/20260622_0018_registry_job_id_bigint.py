"""Widen sqlery_registry.job_id to BIGINT

Standalone job ids are 62-bit (UUIDv7-derived); an INTEGER column made every
add_job_to_registry() fail with "integer out of range" on PostgreSQL.

Revision ID: 20260622_0018
Revises: 20260620_0017
Create Date: 2026-06-22 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = '20260622_0018'
down_revision = '20260620_0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('sqlery_registry') as batch_op:
        batch_op.alter_column(
            'job_id',
            type_=sa.BigInteger(),
            existing_type=sa.Integer(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('sqlery_registry') as batch_op:
        batch_op.alter_column(
            'job_id',
            type_=sa.Integer(),
            existing_type=sa.BigInteger(),
            existing_nullable=False,
        )
