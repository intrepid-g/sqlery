"""Alembic environment configuration for sqlery.

This module configures Alembic to work with sqlery in standalone mode.
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add the src directory to the path so we can import sqlery
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import SQLModel and models
from sqlmodel import SQLModel
from sqlery.core.models import QueuedJob, ScheduledTask, JobRegistry, Worker, DaemonLease

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = SQLModel.metadata


def get_database_url() -> str:
    """Get database URL from environment or config."""
    # Try environment variable first
    db_url = os.environ.get('SQLERY_DATABASE_URL')

    if not db_url:
        # Try to get from sqlery config
        try:
            from sqlery.compat import get_config
            db_url = get_config('DATABASE_URL')
        except Exception:
            pass

    if not db_url:
        # Fall back to alembic.ini config
        db_url = config.get_main_option("sqlalchemy.url")

    if not db_url:
        raise ValueError(
            "Database URL not configured. Set SQLERY_DATABASE_URL environment variable "
            "or configure via sqlery.initialize()"
        )

    return db_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_database_url()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    # Get database URL
    url = get_database_url()

    # Override the sqlalchemy.url in the config
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = url

    # Create engine
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
