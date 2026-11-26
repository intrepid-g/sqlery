"""Record the claiming node on each attempt (issue #47)

sqlery_job_attempt.node_id names the node whose process claimed the job. A PID is only
meaningful on that node, so PID probes and kills are limited to attempts claimed on the
current node. Nullable; older rows have no node. Added only when absent (a PostgreSQL attempt
table created from the current DDL already has it). Reversible.
Mirrors the Django 0043_attempt_node_id migration.

Revision ID: 20261126_0024
Revises: 20261026_0023
Create Date: 2026-11-26 00:24:00.000000
"""

import sqlalchemy as sa

from alembic import op
from sqlery.tables import JOB_ATTEMPT

revision = "20261126_0024"
down_revision = "20261026_0023"
branch_labels = None
depends_on = None

COLUMN = "node_id"


def _has_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(JOB_ATTEMPT)
    return any(column["name"] == COLUMN for column in columns)


def upgrade() -> None:
    if not _has_column():
        op.add_column(JOB_ATTEMPT, sa.Column(COLUMN, sa.String(length=255), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column(JOB_ATTEMPT, COLUMN)
