"""Database session management for standalone mode.

Provides SQLAlchemy session management and database initialization.
Also provides async engine + AsyncSession factory for the async backend (ASYN-03).
"""

from contextlib import contextmanager
from datetime import date, timedelta

from sqlalchemy import event, text
from sqlalchemy.pool import QueuePool, StaticPool
from sqlmodel import Session, create_engine, SQLModel

from ..core import models as _models  # noqa: F401  # ensures SQLModel.metadata is populated
# IN-01: unused — the table name "sqlery_queued_job" is interpolated inline in the
# partition DDL builders, never via this constant. Commented out (not deleted) per
# project convention; remove in a future cleanup pass.
# from ..tables import QUEUED_JOB

# Global engine instance
_engine = None

# Global async engine + session factory (lazily initialized)
_async_engine = None
_async_session_factory = None


def _to_async_url(url: str) -> str:
    """Translate a sync database URL to its async-driver equivalent.

    Mapping (RESEARCH §2):
      sqlite:///foo.db          -> sqlite+aiosqlite:///foo.db
      postgresql://...          -> postgresql+psycopg://...     (psycopg3-async)
      postgresql+psycopg://...  -> unchanged
      sqlite+aiosqlite://...    -> unchanged

    psycopg2 has no async support; raise a clear error.
    """
    if url.startswith("postgresql+psycopg2"):
        raise ValueError(
            "psycopg2 driver is not async-capable. "
            "Use 'postgresql://' (auto-translated to psycopg3-async) "
            "or 'postgresql+psycopg://...' explicitly."
        )
    if url.startswith("sqlite+aiosqlite"):
        return url
    if url.startswith("sqlite"):
        # sqlite:/// -> sqlite+aiosqlite:///
        return "sqlite+aiosqlite" + url[len("sqlite"):]
    if url.startswith("postgresql+psycopg"):
        return url
    if url.startswith("postgresql"):
        # postgresql:// -> postgresql+psycopg://
        return "postgresql+psycopg" + url[len("postgresql"):]
    return url


def _build_partitioned_jobs_ddl() -> str:
    """Return the CREATE TABLE ... PARTITION BY RANGE DDL for sqlery_queued_job.

    Column list is derived from a hard-coded canonical set that mirrors
    QueuedJob in core/models.py. Using static SQL avoids fragility from
    SQLModel metadata introspection and is byte-identical to Django 0030 S2
    for a fresh PG install.

    Returns:
        SQL string for CREATE TABLE IF NOT EXISTS ... PARTITION BY RANGE.
    """
    return """
    CREATE TABLE IF NOT EXISTS sqlery_queued_job (
        id                  BIGINT      NOT NULL DEFAULT nextval('sqlery_job_id_seq'),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        task_path           VARCHAR(500) NOT NULL,
        kwargs              JSON        NOT NULL DEFAULT '{}'::json,
        queue_name          VARCHAR(50)  NOT NULL DEFAULT 'default',
        priority            INTEGER      NOT NULL DEFAULT 0,
        status              VARCHAR(20)  NOT NULL DEFAULT 'queued',
        parent_job_id       BIGINT,
        retry_count         INTEGER      NOT NULL DEFAULT 0,
        max_retries         INTEGER      NOT NULL DEFAULT 0,
        retry_backoff       DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        allow_parallel      BOOLEAN      NOT NULL DEFAULT false,
        timeout_seconds     INTEGER,
        worker_pid          INTEGER,
        child_pid           INTEGER,
        job_name            VARCHAR(255),
        tags                JSON         NOT NULL DEFAULT '[]'::json,
        retry_intervals     JSON,
        meta                JSON,
        dependencies        JSON         NOT NULL DEFAULT '[]'::json,
        on_success_path     VARCHAR(500) NOT NULL DEFAULT '',
        on_failure_path     VARCHAR(500) NOT NULL DEFAULT '',
        ttl                 INTEGER,
        result_ttl          INTEGER,
        failure_ttl         INTEGER,
        version             INTEGER      NOT NULL DEFAULT 0,
        runs                JSON         NOT NULL DEFAULT '[]'::json,
        scheduled_task_id   INTEGER,
        worker_id           UUID,
        scheduled_at        TIMESTAMPTZ,
        started_at          TIMESTAMPTZ,
        finished_at         TIMESTAMPTZ,
        duration_seconds    DOUBLE PRECISION,
        output              TEXT         NOT NULL DEFAULT '',
        error               TEXT         NOT NULL DEFAULT '',
        traceback           TEXT         NOT NULL DEFAULT '',
        termination_reason  VARCHAR(100) NOT NULL DEFAULT '',
        PRIMARY KEY (created_at, id)
    ) PARTITION BY RANGE (created_at);
    """


def _init_partitioned_pg(engine) -> None:
    """Create partitioned sqlery_queued_job schema on PostgreSQL (fresh install).

    Idempotent — safe to call multiple times (all DDL uses IF NOT EXISTS or
    catalog guards). Steps:
      1. Create all non-jobs tables via SQLModel.metadata.create_all.
      2. Create shared sequence sqlery_job_id_seq (IF NOT EXISTS).
      3. Inspect pg_class: fresh (no table) → create partitioned directly;
         plain table (relkind='r') → inline cutover (rename → partition → copy);
         already partitioned (relkind='p') → skip table creation.
      4. Create DEFAULT partition.
      5. Create today's daily partition + PREMAKE-day lookahead window.
      6. Create partial pending index sqlery_job_pending_idx (byte-identical to
         Django migration 0028 / D7 invariant).
    """
    # Step 1 — create the jobs table first (partitioned) so that other tables
    # referencing sqlery_queued_job.id via FK can resolve the FK target.
    # We cannot use create_all for non-jobs tables first because sqlery_worker
    # carries a FK to sqlery_queued_job and SQLAlchemy cannot sort the cycle.
    with engine.connect() as conn:
        # Step 1a — shared sequence (idempotent — must exist before table creation).
        conn.execute(text("CREATE SEQUENCE IF NOT EXISTS sqlery_job_id_seq"))

        # Step 1b — inspect current state of sqlery_queued_job in pg_class.
        row = conn.execute(
            text("SELECT relkind FROM pg_class WHERE relname = 'sqlery_queued_job'")
        ).fetchone()
        relkind = row[0] if row else None

        if relkind == "p":
            # Already partitioned — nothing to do for the table itself.
            pass
        elif relkind == "r":
            # Plain (unpartitioned) table exists — perform inline cutover:
            # S0: drop FK constraints referencing sqlery_queued_job
            conn.execute(text("""
                DO $$
                DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN
                        SELECT tc.table_name, tc.constraint_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.referential_constraints rc
                          ON tc.constraint_name = rc.constraint_name
                        JOIN information_schema.constraint_column_usage ccu
                          ON rc.unique_constraint_name = ccu.constraint_name
                        WHERE ccu.table_name = 'sqlery_queued_job'
                          AND tc.constraint_type = 'FOREIGN KEY'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
                            r.table_name, r.constraint_name
                        );
                    END LOOP;
                END $$;
            """))
            # S1: rename to legacy
            conn.execute(text("""
                DO $$ BEGIN
                    IF to_regclass('public.sqlery_queued_job_legacy') IS NULL
                       AND to_regclass('public.sqlery_queued_job') IS NOT NULL THEN
                        ALTER TABLE sqlery_queued_job RENAME TO sqlery_queued_job_legacy;
                    END IF;
                END $$;
            """))
            # S2: create partitioned table LIKE legacy
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sqlery_queued_job (
                    LIKE sqlery_queued_job_legacy
                    INCLUDING DEFAULTS INCLUDING STORAGE
                ) PARTITION BY RANGE (created_at);
            """))
            # S3: add composite PK (catalog-guarded)
            pk_count = conn.execute(text("""
                SELECT COUNT(*) FROM pg_constraint
                WHERE conrelid = 'sqlery_queued_job'::regclass AND contype = 'p'
            """)).scalar()
            if not pk_count:
                conn.execute(text(
                    "ALTER TABLE sqlery_queued_job ADD PRIMARY KEY (created_at, id);"
                ))
            # S3b: drop job_name unique constraint
            conn.execute(text("""
                ALTER TABLE sqlery_queued_job
                    DROP CONSTRAINT IF EXISTS sqlery_queued_job_job_name_key;
            """))
            # S6: recreate pending index (drop from legacy first — mirrors 0030 S6)
            conn.execute(text("DROP INDEX IF EXISTS sqlery_job_pending_idx;"))
            # S8: bulk copy
            conn.execute(text("""
                INSERT INTO sqlery_queued_job
                    SELECT * FROM sqlery_queued_job_legacy
                    ON CONFLICT DO NOTHING;
            """))
            # S9: seed shared sequence past max id
            conn.execute(text(
                "SELECT setval('sqlery_job_id_seq',"
                " GREATEST((SELECT COALESCE(MAX(id), 0) FROM sqlery_queued_job), 1),"
                " (SELECT COUNT(*) > 0 FROM sqlery_queued_job))"
            ))
        else:
            # relkind is None — fresh install, no table yet.
            conn.execute(text(_build_partitioned_jobs_ddl()))

        # Step 2 — DEFAULT partition (catch-all).
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sqlery_queued_job_default
                PARTITION OF sqlery_queued_job DEFAULT;
        """))

        # Step 3 — daily partition window: today and PREMAKE days ahead.
        # Partition names and date literals are derived from Python date objects
        # (strftime/isoformat — digits, dashes only) so f-string interpolation
        # is safe here. DDL statements cannot use parameterized binds for
        # FOR VALUES FROM/TO because PostgreSQL cannot infer the type.
        PREMAKE = 7
        today = date.today()
        for i in range(PREMAKE + 1):
            day = today + timedelta(days=i)
            next_day = day + timedelta(days=1)
            partition_name = "sqlery_queued_job_" + day.strftime("%Y%m%d")
            from_ts = day.isoformat()
            to_ts = next_day.isoformat()
            conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS {partition_name}"
                " PARTITION OF sqlery_queued_job"
                f" FOR VALUES FROM ('{from_ts}') TO ('{to_ts}')"
            ))

        # Step 4 — partial pending index (byte-identical to Django 0028 / D7 invariant).
        # For fresh installs (no legacy table) we can use IF NOT EXISTS safely.
        # For the inline-cutover path the DROP above cleared the old name first.
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS sqlery_job_pending_idx
                ON sqlery_queued_job (queue_name, priority DESC, created_at)
                WHERE status = 'queued';
        """))

        conn.commit()

    # Step 5 — create all other tables.
    # sqlery_queued_job already exists as a partitioned table, so SQLAlchemy's
    # CREATE TABLE will silently skip it (checkfirst=True).
    #
    # PostgreSQL partitioned tables cannot be referenced by a single-column FK
    # (only the composite PK (created_at, id) is unique, not id alone).  D4
    # decision demotes these FKs to plain BIGINT at application level.
    # We therefore skip sqlery_worker and sqlery_registry in the SQLModel
    # create_all pass and create them manually without FK constraints.
    _FK_DEMOTED_TABLES = {"sqlery_worker", "sqlery_registry"}
    safe_tables = [
        t for t in SQLModel.metadata.sorted_tables
        if t.name not in _FK_DEMOTED_TABLES
    ]
    SQLModel.metadata.create_all(engine, tables=safe_tables, checkfirst=True)

    # Create sqlery_worker and sqlery_registry without FK constraints (D4).
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sqlery_worker (
                id          UUID        NOT NULL,
                node_id     VARCHAR(255) NOT NULL,
                pid         INTEGER      NOT NULL,
                status      VARCHAR(10)  NOT NULL DEFAULT 'idle',
                current_job_id BIGINT,
                queues      JSON         NOT NULL DEFAULT '[]'::json,
                last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
                started_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
                jobs_processed INTEGER   NOT NULL DEFAULT 0,
                PRIMARY KEY (id)
            );
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS sqlery_worker_node_id_status_idx"
            " ON sqlery_worker (node_id, status);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS sqlery_worker_status_last_heartbeat_idx"
            " ON sqlery_worker (status, last_heartbeat);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sqlery_worker_node_id"
            " ON sqlery_worker (node_id);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sqlery_worker_status"
            " ON sqlery_worker (status);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sqlery_worker_last_heartbeat"
            " ON sqlery_worker (last_heartbeat);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sqlery_worker_current_job_id"
            " ON sqlery_worker (current_job_id);"
        ))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sqlery_registry (
                id          SERIAL      NOT NULL,
                job_id      BIGINT      NOT NULL,
                registry_type VARCHAR(20) NOT NULL,
                entered_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
                exited_at   TIMESTAMPTZ,
                metadata    JSON         NOT NULL DEFAULT '{}'::json,
                PRIMARY KEY (id)
            );
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sqlery_registry_registry_type"
            " ON sqlery_registry (registry_type);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS sqlery_regi_reg_type_entered_idx"
            " ON sqlery_registry (registry_type, entered_at);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS sqlery_regi_job_id_404819_idx"
            " ON sqlery_registry (job_id, registry_type);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS sqlery_regi_reg_type_exited_idx"
            " ON sqlery_registry (registry_type, exited_at);"
        ))
        conn.commit()


def init_database(database_url: str, **kwargs):
    """Initialize database engine and create tables.

    On PostgreSQL this emits partitioned DDL for sqlery_queued_job (D8:
    fresh installs are partitioned by default). On SQLite the plain
    SQLModel.metadata.create_all path is kept unchanged (D6).

    Args:
        database_url: Database connection URL (PostgreSQL or SQLite)
        **kwargs: Additional engine configuration
    """
    global _engine

    if database_url.startswith('sqlite'):
        # from sqlalchemy.pool import StaticPool  # moved to top-level
        _engine = create_engine(
            database_url,
            poolclass=StaticPool,
            connect_args={'check_same_thread': False},
            echo=kwargs.get('echo', False),
        )
    else:
        # Create engine with connection pooling (PostgreSQL and other dialects)
        _engine = create_engine(
            database_url,
            poolclass=QueuePool,
            pool_size=kwargs.get('pool_size', 5),
            max_overflow=kwargs.get('max_overflow', 10),
            pool_timeout=kwargs.get('pool_timeout', 30),
            pool_recycle=kwargs.get('pool_recycle', 1800),
            pool_pre_ping=True,  # Verify connections before using
            echo=kwargs.get('echo', False),
        )

    # Import models so SQLModel.metadata is populated before create_all
    # from ..core import models as _models  # noqa: F401  # moved to top-level

    if database_url.startswith('sqlite'):
        # D6 — SQLite: keep plain create_all (no partitioning)
        SQLModel.metadata.create_all(_engine)
    elif _engine.dialect.name == "postgresql":
        # D8 — PostgreSQL fresh install: create partitioned sqlery_queued_job
        # Old: SQLModel.metadata.create_all(_engine)
        _init_partitioned_pg(_engine)
    else:
        # Other dialects (e.g. MySQL future): plain create_all fallback
        SQLModel.metadata.create_all(_engine)


def get_engine():
    """Get the database engine.

    Returns:
        SQLAlchemy engine instance

    Raises:
        RuntimeError: If database not initialized
    """
    if _engine is None:
        raise RuntimeError(
            "Database not initialized. Call init_database() first or use initialize()."
        )

    return _engine


def get_async_engine(database_url: str | None = None, **kwargs):
    """Build (or return cached) async SQLAlchemy engine.

    Args:
        database_url: Sync or async-form URL. Translated via :func:`_to_async_url`.
            If None, a previously-built async engine is returned.
        **kwargs: Forwarded to ``create_async_engine``.

    Returns:
        sqlalchemy.ext.asyncio.AsyncEngine

    Raises:
        RuntimeError: If no URL is given and no engine has been built yet.
    """
    global _async_engine
    if _async_engine is not None and database_url is None:
        return _async_engine

    if database_url is None:
        raise RuntimeError(
            "Async engine not initialized. Pass database_url on first call."
        )

    from sqlalchemy.ext.asyncio import create_async_engine

    async_url = _to_async_url(database_url)

    connect_args = {}
    if async_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_async_engine(async_url, connect_args=connect_args, **kwargs)

    # WAL pragma listener for SQLite (RESEARCH §3, CONTEXT open-question 2).
    if async_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
            finally:
                cur.close()

    _async_engine = engine
    return engine


def get_async_session_factory(database_url: str | None = None):
    """Return cached async sessionmaker bound to the async engine.

    Lazily builds the engine + factory on first call.
    """
    global _async_session_factory
    if _async_session_factory is not None and database_url is None:
        return _async_session_factory

    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    engine = get_async_engine(database_url)
    _async_session_factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    return _async_session_factory


def reset_async_engine():
    """Dispose & clear the cached async engine + factory.

    Intended for tests that switch URLs between cases.
    """
    global _async_engine, _async_session_factory
    _async_engine = None
    _async_session_factory = None


@contextmanager
def get_session():
    """Get a database session context manager.

    Yields:
        SQLModel Session instance

    Example:
        >>> with get_session() as session:
        ...     job = session.get(QueuedJob, 1)
        ...     session.commit()
    """
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.close()
