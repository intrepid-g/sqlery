"""Database session management for standalone mode.

Provides SQLAlchemy session management and database initialization.
Also provides async engine + AsyncSession factory for the async backend (ASYN-03).
"""

from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.pool import QueuePool, StaticPool
from sqlmodel import Session, create_engine, SQLModel

from ..core import models as _models  # noqa: F401  # ensures SQLModel.metadata is populated

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


def init_database(database_url: str, **kwargs):
    """Initialize database engine and create tables.

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

    # Create all tables
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
