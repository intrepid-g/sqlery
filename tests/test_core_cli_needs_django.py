"""Regression for #72: ``sqlery.core.cli`` must import in standalone mode (no Django)."""

import importlib.util
import os
import subprocess
import sys

import pytest

_BLOCK_DJANGO = """
import sys

class _BlockDjango:
    def find_spec(self, name, path=None, target=None):
        if name == "django" or name.startswith("django."):
            raise ImportError(f"No module named {name!r} (blocked)")
        return None

sys.meta_path.insert(0, _BlockDjango())
import __MODULE__
"""

_needs_cli_deps = pytest.mark.skipif(
    importlib.util.find_spec("typer") is None or importlib.util.find_spec("rich") is None,
    reason="needs the [cli] extra (typer, rich)",
)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "SQLERY_FORCE_STANDALONE": "1"},
    )


@_needs_cli_deps
@pytest.mark.skipif(
    importlib.util.find_spec("django") is not None, reason="needs an environment without Django"
)
def test_core_cli_imports_without_django():
    out = subprocess.run(
        [sys.executable, "-c", "import sqlery.core.cli"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-300:]


@_needs_cli_deps
@pytest.mark.parametrize("module", ["sqlery.core.cli", "sqlery.fastapi_sqlery.cli"])
def test_cli_imports_with_django_blocked(module):
    out = _run(_BLOCK_DJANGO.replace("__MODULE__", module))
    assert out.returncode == 0, out.stderr[-500:]


@_needs_cli_deps
def test_tasks_run_standalone_exits_cleanly_without_django():
    code = _BLOCK_DJANGO.replace("import __MODULE__", "").strip() + """
from typer.testing import CliRunner
from sqlery.core.cli import app
result = CliRunner().invoke(app, ["tasks", "run"])
assert result.exit_code != 0, result.output
assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
assert "Traceback" not in result.output
"""
    out = _run(code)
    assert out.returncode == 0, out.stderr[-500:]
