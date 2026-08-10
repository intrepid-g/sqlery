"""Shared helper: translate a raw Postgres URL to the psycopg3 SQLAlchemy dialect.

SQLAlchemy maps a bare ``postgresql://`` (or explicit ``postgresql+psycopg2://``)
URL to the psycopg2 driver. This project standardizes on psycopg3
(``psycopg`` >= 3.1) and does not install psycopg2, so every test that builds a
SQLAlchemy engine from ``SQLERY_TEST_PG_URL`` / ``DATABASE_URL`` must translate
the scheme to ``postgresql+psycopg://`` first.
"""

from __future__ import annotations


def sqlalchemy_pg_url(raw: str) -> str:
    """Translate ``postgresql://`` / ``postgresql+psycopg2://`` to ``postgresql+psycopg://``.

    Any other scheme (including an already-translated ``+psycopg``/``+asyncpg``
    URL, or an empty string) passes through unchanged.
    """
    if raw.startswith("postgresql://") or raw.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg" + raw[raw.index("://") :]
    return raw
