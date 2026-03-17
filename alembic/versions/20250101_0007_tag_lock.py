"""Add tag lock table

Revision ID: 20250101_0007
Revises: 20250101_0006
Create Date: 2025-01-01 00:07:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20250101_0007'
down_revision = '20250101_0006'
branch_labels = None
depends_on = None

TAG_LOCK = 'sqlery_tag_lock'


def upgrade() -> None:
    op.create_table(
        TAG_LOCK,
        sa.Column('tag', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('tag'),
    )


def downgrade() -> None:
    op.drop_table(TAG_LOCK)
