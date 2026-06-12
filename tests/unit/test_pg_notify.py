"""Unit tests for src/sqlery/core/pg_notify.py.

Tests cover:
- sanitize_queue_name_to_channel: all behaviour cases (parametrize)
- notify_queue_django: no-op when vendor != postgresql; on_commit scheduling
- notify_queue_sqlalchemy: no-op when dialect != postgresql; SQL assembly
"""

import pytest
from unittest.mock import MagicMock, patch, call

from sqlery.core.pg_notify import (
    sanitize_queue_name_to_channel,
    notify_queue_django,
    notify_queue_sqlalchemy,
    _fire_django_notify,
)


# ---------------------------------------------------------------------------
# sanitize_queue_name_to_channel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "queue_name, expected",
    [
        # Basic alphanumeric name unchanged
        ("default", "sqlery_job_default"),
        # Hyphens become underscores
        ("my-queue", "sqlery_job_my_queue"),
        # Special characters become underscores
        ("bad!name", "sqlery_job_bad_name"),
        # Numeric-leading queue name: safe because prefix adds "sqlery_job_"
        ("123start", "sqlery_job_123start"),
        # Spaces become underscores
        ("my queue", "sqlery_job_my_queue"),
        # Mixed special characters
        ("a.b:c/d", "sqlery_job_a_b_c_d"),
        # Already clean name
        ("high_priority", "sqlery_job_high_priority"),
    ],
)
def test_sanitize_queue_name_to_channel_basic(queue_name: str, expected: str) -> None:
    """sanitize_queue_name_to_channel produces the expected channel name."""
    assert sanitize_queue_name_to_channel(queue_name) == expected


def test_sanitize_queue_name_to_channel_empty_raises() -> None:
    """sanitize_queue_name_to_channel raises ValueError for empty string."""
    with pytest.raises(ValueError, match="non-empty"):
        sanitize_queue_name_to_channel("")


def test_sanitize_queue_name_to_channel_whitespace_only_raises() -> None:
    """sanitize_queue_name_to_channel raises ValueError for whitespace-only string."""
    with pytest.raises(ValueError, match="non-empty"):
        sanitize_queue_name_to_channel("   ")


def test_sanitize_queue_name_to_channel_truncates_to_63_chars() -> None:
    """sanitize_queue_name_to_channel truncates long names to 63 chars (PG limit)."""
    long_queue = "a" * 200
    result = sanitize_queue_name_to_channel(long_queue)
    assert len(result) <= 63
    assert result.startswith("sqlery_job_")


def test_sanitize_queue_name_to_channel_exactly_63_chars() -> None:
    """Channel truncated at exactly 63 characters."""
    # prefix is "sqlery_job_" = 11 chars; 63 - 11 = 52 chars of the sanitized name
    long_queue = "x" * 200
    result = sanitize_queue_name_to_channel(long_queue)
    assert len(result) == 63


def test_sanitize_queue_name_to_channel_short_name_not_truncated() -> None:
    """Short names are not truncated."""
    result = sanitize_queue_name_to_channel("hi")
    assert result == "sqlery_job_hi"
    assert len(result) < 63


def test_sanitize_queue_name_to_channel_prefix_never_truncated() -> None:
    """WR-02: the 'sqlery_job_' prefix is always intact, only the suffix is cut."""
    long_queue = "z" * 200
    result = sanitize_queue_name_to_channel(long_queue)
    assert result.startswith("sqlery_job_")
    # Suffix is truncated to 52 chars; prefix (11) + 52 == 63.
    assert result == "sqlery_job_" + "z" * 52


def test_sanitize_queue_name_to_channel_no_collision_on_52_char_prefix() -> None:
    """WR-02: queues differing only past 52 sanitized chars still collide.

    This documents the residual limitation: truncation makes the cut visible
    in the suffix, but two adversarial names sharing the same 52-char prefix
    still map to the same channel. The fix prevents the prefix from being
    mangled, not all collisions.
    """
    base = "a" * 52
    ch_x = sanitize_queue_name_to_channel(base + "X")
    ch_y = sanitize_queue_name_to_channel(base + "Y")
    # Both truncate to the same 52-char suffix — still a collision by design.
    assert ch_x == ch_y == "sqlery_job_" + base
    # But names that differ WITHIN the first 52 chars do NOT collide.
    ch_a = sanitize_queue_name_to_channel("a" * 51 + "b")
    ch_c = sanitize_queue_name_to_channel("a" * 51 + "c")
    assert ch_a != ch_c


# ---------------------------------------------------------------------------
# notify_queue_django — no-op paths
# ---------------------------------------------------------------------------


def test_notify_queue_django_noop_when_not_postgresql() -> None:
    """notify_queue_django does nothing when Django connection vendor is not postgresql."""
    mock_connection = MagicMock()
    mock_connection.vendor = "sqlite"
    mock_transaction = MagicMock()

    with (
        patch("sqlery.core.pg_notify._django_transaction", mock_transaction),
        # Phase 18 (IN-01): code now reads the module-level _django_connection.
        patch("sqlery.core.pg_notify._django_connection", mock_connection),
    ):
        notify_queue_django("default")
        # on_commit must NOT have been scheduled
        mock_transaction.on_commit.assert_not_called()


def test_notify_queue_django_noop_when_django_not_available() -> None:
    """notify_queue_django does nothing when _django_transaction is None."""
    with patch("sqlery.core.pg_notify._django_transaction", None):
        # Should not raise; simply return
        notify_queue_django("default")


def test_notify_queue_django_schedules_on_commit_for_postgresql() -> None:
    """notify_queue_django schedules on_commit when connection is PostgreSQL."""
    mock_connection = MagicMock()
    mock_connection.vendor = "postgresql"
    mock_transaction = MagicMock()

    with (
        patch("sqlery.core.pg_notify._django_transaction", mock_transaction),
        # Phase 18 (IN-01): code now reads the module-level _django_connection.
        patch("sqlery.core.pg_notify._django_connection", mock_connection),
    ):
        notify_queue_django("my-queue")
        mock_transaction.on_commit.assert_called_once()
        # Verify the scheduled callback is a callable (lambda)
        scheduled_fn = mock_transaction.on_commit.call_args[0][0]
        assert callable(scheduled_fn)


# ---------------------------------------------------------------------------
# _fire_django_notify — SQL assembly
# ---------------------------------------------------------------------------


def test_fire_django_notify_executes_correct_sql() -> None:
    """_fire_django_notify executes SELECT pg_notify(%s, '') with channel param."""
    mock_cursor = MagicMock()
    mock_connection = MagicMock()
    mock_connection.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_connection.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("sqlery.core.pg_notify._django_connection", mock_connection):
        _fire_django_notify("sqlery_job_default")

    mock_cursor.execute.assert_called_once_with(
        "SELECT pg_notify(%s, '')", ["sqlery_job_default"]
    )


def test_fire_django_notify_swallows_exceptions() -> None:
    """_fire_django_notify logs and swallows exceptions without crashing."""
    mock_connection = MagicMock()
    mock_connection.cursor.side_effect = Exception("DB down")

    with patch("sqlery.core.pg_notify._django_connection", mock_connection):
        # Must not raise
        _fire_django_notify("sqlery_job_default")


# ---------------------------------------------------------------------------
# notify_queue_sqlalchemy — no-op paths
# ---------------------------------------------------------------------------


def _make_autocommit_engine(dialect_name: str):
    """Build a mock engine whose .connect().execution_options() yields a conn.

    Returns (engine, conn) so tests can assert on conn.execute. The connection
    is a context manager (engine.connect() ... `with ... as conn`).
    """
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.dialect.name = dialect_name
    # engine.connect() returns a Connection; .execution_options() returns a
    # Connection that is used as a context manager yielding itself.
    exec_opts_conn = MagicMock()
    exec_opts_conn.__enter__.return_value = mock_conn
    exec_opts_conn.__exit__.return_value = False
    mock_engine.connect.return_value.execution_options.return_value = exec_opts_conn
    return mock_engine, mock_conn


def test_notify_queue_sqlalchemy_noop_when_not_postgresql() -> None:
    """notify_queue_sqlalchemy does nothing when dialect is not postgresql."""
    # CR-01: signature now takes an engine, not a session.
    mock_engine, mock_conn = _make_autocommit_engine("sqlite")

    notify_queue_sqlalchemy("default", mock_engine)

    mock_engine.connect.assert_not_called()
    mock_conn.execute.assert_not_called()


def test_notify_queue_sqlalchemy_noop_when_sa_text_unavailable() -> None:
    """notify_queue_sqlalchemy is a no-op when _sa_text is None."""
    mock_engine, mock_conn = _make_autocommit_engine("postgresql")

    with patch("sqlery.core.pg_notify._sa_text", None):
        notify_queue_sqlalchemy("default", mock_engine)

    mock_conn.execute.assert_not_called()


def test_notify_queue_sqlalchemy_noop_when_engine_none() -> None:
    """notify_queue_sqlalchemy is a no-op when engine is None."""
    # Must not raise.
    notify_queue_sqlalchemy("default", None)


def test_notify_queue_sqlalchemy_executes_pg_notify_for_postgresql() -> None:
    """notify_queue_sqlalchemy executes SELECT pg_notify on an AUTOCOMMIT conn."""
    mock_engine, mock_conn = _make_autocommit_engine("postgresql")

    notify_queue_sqlalchemy("my-queue", mock_engine)

    # AUTOCOMMIT isolation level must be requested.
    mock_engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="AUTOCOMMIT"
    )
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args
    sql_clause = call_args[0][0]
    params = call_args[0][1]
    assert "pg_notify" in str(sql_clause)
    assert params == {"ch": "sqlery_job_my_queue"}


def test_notify_queue_sqlalchemy_swallows_execute_exceptions() -> None:
    """notify_queue_sqlalchemy logs and swallows execute exceptions."""
    mock_engine, mock_conn = _make_autocommit_engine("postgresql")
    mock_conn.execute.side_effect = Exception("PG error")

    # Must not raise
    notify_queue_sqlalchemy("default", mock_engine)


def test_notify_queue_sqlalchemy_noop_when_dialect_access_fails() -> None:
    """notify_queue_sqlalchemy does nothing when reading engine.dialect raises."""
    mock_engine = MagicMock()
    type(mock_engine).dialect = property(
        lambda self: (_ for _ in ()).throw(Exception("no dialect"))
    )

    # Must not raise, and must not attempt to connect.
    notify_queue_sqlalchemy("default", mock_engine)
    mock_engine.connect.assert_not_called()
