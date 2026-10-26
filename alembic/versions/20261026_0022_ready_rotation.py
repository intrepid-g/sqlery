"""Sequences for the opt-in ready-queue rotation (issue #141, PostgreSQL only)

``sqlery_ready_gen_seq`` (generation; ``% 3`` is the writers' slot) and two timestamp
sequences for the health lag. Nothing changes until READY_ROTATION_ENABLED is set.
Reversible; no-op on SQLite. Mirrors the Django 0041_ready_rotation migration.

Revision ID: 20261026_0022
Revises: 20261026_0021
Create Date: 2026-10-26 00:22:00.000000
"""

import sqlalchemy as sa

from alembic import op
from sqlery.core import rotation

revision = "20261026_0022"
down_revision = "20261026_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in rotation.postgres_rotation_ddl():
        op.execute(sa.text(statement))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in rotation.drop_rotation_ddl():
        op.execute(sa.text(statement))
