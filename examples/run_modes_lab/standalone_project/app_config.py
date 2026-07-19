"""Standalone mode configuration and database initialization for the lab.

This module initializes the sqlery backend when imported, ensuring the database
and tables are ready before any jobs are executed or the web UI is accessed.

The module:
1. Reads database configuration from environment variables
2. Calls sqlery.compat.initialize() to create the database engine and tables
3. Can be invoked as a script for one-shot schema validation: python app_config.py
"""

import os
import sys
import logging

logger = logging.getLogger(__name__)


def initialize_standalone_backend():
    """Initialize the standalone sqlery backend and create tables if needed.

    Reads configuration from environment variables:
    - SQLERY_DATABASE_URL: PostgreSQL connection URL (required)
    - SQLERY_POOL_SIZE: Connection pool size (default: 5)
    - SQLERY_MAX_OVERFLOW: Max overflow connections (default: 10)
    - SQLERY_POOL_TIMEOUT: Pool timeout in seconds (default: 30)
    - SQLERY_POOL_RECYCLE: Connection recycle time in seconds (default: 1800)
    """
    from sqlery.compat import initialize

    # Read configuration from environment
    database_url = os.getenv("SQLERY_DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "SQLERY_DATABASE_URL not set. Please configure the database URL "
            "as an environment variable before starting sqlery services."
        )

    pool_size = int(os.getenv("SQLERY_POOL_SIZE", "5"))
    max_overflow = int(os.getenv("SQLERY_MAX_OVERFLOW", "10"))
    pool_timeout = int(os.getenv("SQLERY_POOL_TIMEOUT", "30"))
    pool_recycle = int(os.getenv("SQLERY_POOL_RECYCLE", "1800"))

    logger.info(f"Initializing standalone sqlery backend: {database_url}")

    try:
        initialize(
            database_url=database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
        )
        logger.info("Standalone backend initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize standalone backend: {e}", exc_info=True)
        raise


def main():
    """One-shot schema validation / initialization (can be called as: python app_config.py)."""
    logging.basicConfig(level=logging.INFO)
    initialize_standalone_backend()
    print("✓ Standalone backend initialized and tables created")


# Initialize backend when this module is imported (so sqlery-web and sqlery-worker
# can use the backend immediately without explicit initialization in their code)
try:
    initialize_standalone_backend()
except RuntimeError as e:
    # SQLERY_DATABASE_URL not set — this is OK if running in Django mode or
    # if initialization will happen elsewhere. Log and continue.
    logger.debug(f"Standalone backend not initialized (expected in some modes): {e}")
except Exception:
    # Unexpected error during initialization — re-raise so we fail fast
    raise


if __name__ == "__main__":
    main()
