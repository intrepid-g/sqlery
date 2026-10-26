"""Move terminal jobs to an insert-only history (issue #140)

A job that reaches a terminal state (success / failed / archived) leaves sqlery_queued_job
for sqlery_job_history in the transaction that records the outcome (INSERT .. SELECT +
DELETE, fenced like the attempt close; see sqlery.core.history). The live table then holds
only queued / running jobs, and history is never updated: only retention removes rows.

* sqlery_job_history: every column of sqlery_queued_job, primary key (finished_at, id).
  PostgreSQL: PARTITION BY RANGE (finished_at) when the live table is partitioned, with a
  DEFAULT partition and daily partitions (the daemon keeps creating them), so retention
  drops whole partitions; SQLite: a plain table.
* sqlery_job_all: a view over the live table and history for readers that must see a job
  whatever its state.
* sqlery_job_attempt becomes PARTITION BY RANGE (claimed_at) on PostgreSQL, so the attempts
  of jobs past retention are reclaimed by DROP together with their history. The id keeps a
  sequence default; the primary key is (claimed_at, id).
* Data migration: already finished jobs move to history in batches of 1000, each batch in its
  own transaction. A failed job that still owes a retry (retry_pending marker) stays live.
  Resumable: run it again after an interruption.

Stop all workers before upgrading: a worker started before the history table existed
finishes jobs in the live table, and new workers refuse to start without it. Mirrors the
Django 0039_job_history / 0040_move_terminal_jobs migrations.

Revision ID: 20261026_0021
Revises: 20261026_0020
Create Date: 2026-10-26 00:21:00.000000
"""

from datetime import date, timedelta

import sqlalchemy as sa

from alembic import op
from sqlery.core import history
from sqlery.core.models import JOB_COLUMNS, JobHistory
from sqlery.tables import JOB_ATTEMPT, JOB_HISTORY, QUEUED_JOB

revision = "20261026_0021"
down_revision = "20261026_0020"
branch_labels = None
depends_on = None

_ATTEMPT_COLUMNS = (
    "id, job_id, token, worker_id, worker_pid, child_pid, claimed_at, finished_at, outcome"
)


def _relkind(bind, table):
    return bind.execute(
        sa.text(
            "SELECT relkind FROM pg_class WHERE relname = :n "
            "AND relnamespace = current_schema()::regnamespace"
        ),
        {"n": table},
    ).scalar()


def _statements(statements):
    for statement in statements:
        op.execute(sa.text(statement))


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)
    # Standalone start-up (init_database) creates the missing objects with the identical
    # definitions, so a process that started on the new code before this revision ran has
    # already done everything but, possibly, the data move: every step is idempotent.
    if not inspector.has_table(JOB_HISTORY):
        if dialect == "postgresql":
            partitioned = _relkind(bind, QUEUED_JOB) == "p"
            _statements(history.postgres_history_ddl(partitioned))
            if partitioned:
                oldest = bind.execute(
                    sa.text(
                        "SELECT min(COALESCE(finished_at, started_at, created_at)) "
                        f"FROM {QUEUED_JOB} WHERE status IN ('success', 'failed', 'archived')"
                    )
                ).scalar()
                today = date.today()
                first_day = min(today, oldest.date()) if oldest is not None else today
                _statements(
                    history.daily_partition_ddl(
                        JOB_HISTORY, first_day, today + timedelta(days=history.PREMAKE_DAYS + 1)
                    )
                )
        else:
            JobHistory.__table__.create(bind, checkfirst=True)

    if dialect == "postgresql" and _relkind(bind, JOB_ATTEMPT) == "r":
        old = f"{JOB_ATTEMPT}_unpartitioned"
        op.execute(sa.text(f"ALTER TABLE {JOB_ATTEMPT} RENAME TO {old}"))
        # Index / constraint names are schema-wide: free them for the new table.
        op.execute(sa.text("DROP INDEX IF EXISTS sqlery_attempt_job_idx"))
        op.execute(sa.text("DROP INDEX IF EXISTS sqlery_attempt_open_worker_idx"))
        for (pk_name,) in bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'p'"
            ),
            {"t": old},
        ):
            op.execute(sa.text(f"ALTER INDEX {pk_name} RENAME TO {old}_pkey"))
        _statements(history.postgres_attempt_partitioned_ddl())
        oldest = bind.execute(sa.text(f"SELECT min(claimed_at) FROM {old}")).scalar()
        today = date.today()
        first_day = min(today, oldest.date()) if oldest is not None else today
        _statements(
            history.daily_partition_ddl(
                JOB_ATTEMPT, first_day, today + timedelta(days=history.PREMAKE_DAYS + 1)
            )
        )
        op.execute(
            sa.text(
                f"INSERT INTO {JOB_ATTEMPT} ({_ATTEMPT_COLUMNS}) "
                f"SELECT {_ATTEMPT_COLUMNS} FROM {old}"
            )
        )
        op.execute(
            sa.text(
                f"SELECT setval('{history.ATTEMPT_ID_SEQUENCE}', "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {JOB_ATTEMPT}), 1), "
                f"(SELECT COUNT(*) > 0 FROM {JOB_ATTEMPT}))"
            )
        )
        op.execute(sa.text(f"DROP TABLE {old}"))

    if history.JOB_ALL_VIEW not in sa.inspect(bind).get_view_names():
        _statements(history.view_columns_ddl(JOB_COLUMNS, dialect))

    _move_rows(history.move_terminal_rows)


def _move_rows(mover) -> None:
    """Run a batched row move with a commit after each batch."""
    bind = op.get_bind()

    def run(sql: str) -> int:
        return bind.execute(sa.text(sql)).rowcount

    with op.get_context().autocommit_block():
        mover(run, JOB_COLUMNS, batch=1000)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)
    if inspector.has_table(JOB_HISTORY):
        _move_rows(history.restore_terminal_rows)
    op.execute(sa.text(history.drop_view_ddl()))
    if dialect == "postgresql" and _relkind(bind, JOB_ATTEMPT) == "p":
        new = f"{JOB_ATTEMPT}_plain"
        op.execute(
            sa.text(
                f"""
                CREATE TABLE {new} (
                    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    job_id      BIGINT       NOT NULL,
                    token       VARCHAR(36)  NOT NULL,
                    worker_id   UUID,
                    worker_pid  INTEGER,
                    child_pid   INTEGER,
                    claimed_at  TIMESTAMPTZ  NOT NULL,
                    finished_at TIMESTAMPTZ,
                    outcome     VARCHAR(10),
                    {history.ATTEMPT_OUTCOME_CHECK}
                )
                """
            )
        )
        op.execute(
            sa.text(
                f"INSERT INTO {new} ({_ATTEMPT_COLUMNS}) SELECT {_ATTEMPT_COLUMNS} FROM {JOB_ATTEMPT}"
            )
        )
        op.execute(
            sa.text(
                f"SELECT setval(pg_get_serial_sequence('{new}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {new}), 1), "
                f"(SELECT COUNT(*) > 0 FROM {new}))"
            )
        )
        op.execute(sa.text(f"DROP TABLE {JOB_ATTEMPT}"))
        op.execute(sa.text(f"DROP SEQUENCE IF EXISTS {history.ATTEMPT_ID_SEQUENCE}"))
        op.execute(sa.text(f"ALTER TABLE {new} RENAME TO {JOB_ATTEMPT}"))
        op.execute(sa.text(f"ALTER INDEX {new}_pkey RENAME TO {JOB_ATTEMPT}_pkey"))
        op.execute(sa.text(f"CREATE INDEX sqlery_attempt_job_idx ON {JOB_ATTEMPT} (job_id)"))
        op.execute(
            sa.text(
                f"CREATE INDEX sqlery_attempt_open_worker_idx ON {JOB_ATTEMPT} (worker_id) "
                f"WHERE finished_at IS NULL"
            )
        )
    if inspector.has_table(JOB_HISTORY):
        # Dropping the partitioned parent drops its partitions.
        op.execute(sa.text(f"DROP TABLE {JOB_HISTORY}"))
