"""Add sqlery_job_attempt (fenced execution attempts, issue #138)

One row per claim of a job. A completion closes exactly its own attempt with a
compare-and-set on (id, token) where finished_at IS NULL; zero rows means the
completion is stale. job_id is a plain indexed column, not a foreign key: on
partitioned PostgreSQL the sqlery_queued_job primary key is (created_at, id).
Mirrors the Django 0037_job_attempt migration.

Stop all workers before upgrading: workers started before this table existed do
not record attempts, and new workers refuse to start without it.

Revision ID: 20261026_0019
Revises: 20260622_0018
Create Date: 2026-10-26 00:19:00.000000
"""

import sqlalchemy as sa
from alembic import op

from sqlery.tables import JOB_ATTEMPT

revision = "20261026_0019"
down_revision = "20260622_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Standalone start-up (init_database -> create_all) creates missing tables, so a process
    # that started on the new code before this revision ran has already created the table
    # with the identical definition: nothing left to do then.
    if sa.inspect(op.get_bind()).has_table(JOB_ATTEMPT):
        return
    op.create_table(
        JOB_ATTEMPT,
        # Integer on SQLite so the primary key auto-increments (rowid alias).
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(length=36), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=True),
        sa.Column("worker_pid", sa.Integer(), nullable=True),
        sa.Column("child_pid", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=10), nullable=True),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('success', 'failed', 'abandoned')",
            name="sqlery_attempt_outcome_chk",
        ),
    )
    op.create_index("sqlery_attempt_job_idx", JOB_ATTEMPT, ["job_id"])
    op.create_index(
        "sqlery_attempt_open_worker_idx",
        JOB_ATTEMPT,
        ["worker_id"],
        postgresql_where=sa.text("finished_at IS NULL"),
        sqlite_where=sa.text("finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("sqlery_attempt_open_worker_idx", table_name=JOB_ATTEMPT)
    op.drop_index("sqlery_attempt_job_idx", table_name=JOB_ATTEMPT)
    op.drop_table(JOB_ATTEMPT)
