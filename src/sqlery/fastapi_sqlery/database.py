"""Database session management for standalone mode.

Provides SQLAlchemy session management and database initialization.
"""

from contextlib import contextmanager
from sqlmodel import Session, create_engine, SQLModel
from sqlalchemy.pool import QueuePool

# Global engine instance
_engine = None


def init_database(database_url: str, **kwargs):
    """Initialize database engine and create tables.

    Args:
        database_url: Database connection URL (PostgreSQL or SQLite)
        **kwargs: Additional engine configuration
    """
    global _engine

    if database_url.startswith('sqlite'):
        from sqlalchemy.pool import StaticPool
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
    from ..core import models as _models  # noqa: F401

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
