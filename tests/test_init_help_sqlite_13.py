"""`sqlery init --help` must say SQLite URLs are accepted (#13)."""

from typer.testing import CliRunner

from sqlery.core.cli import app


def test_init_help_mentions_sqlite_not_postgres_only():
    out = " ".join(CliRunner().invoke(app, ["init", "--help"]).output.split())
    assert "SQLite" in out
    assert "PostgreSQL connection URL" not in out
