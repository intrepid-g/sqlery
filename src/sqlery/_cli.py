"""Console-script launchers: a missing CLI extra gives an install hint, not a traceback (#37).

The scripts import typer/rich/uvicorn at module top, and a bare ``pip install sqlery`` has none
of them. This module imports nothing heavy, so it always starts.
"""

import sys
from importlib import import_module

# Clause CLI_EXTRA_FOR_MODULE: which extra provides each third-party module the CLIs import.
CLI_EXTRA_FOR_MODULE = {
    "typer": "cli",
    "rich": "cli",
    "click": "cli",
    "uvicorn": "standalone",
    "fastapi": "standalone",
    "sqlmodel": "standalone",
    "sqlalchemy": "standalone",
    "alembic": "standalone",
    "jinja2": "standalone",
}


def install_hint(missing_module: str | None) -> str | None:
    """The pip command that provides ``missing_module``, or None if it is not a known extra."""
    extra = CLI_EXTRA_FOR_MODULE.get((missing_module or "").split(".")[0])
    if extra is None:
        return None
    return f'sqlery: the "{missing_module}" package is missing. Install it with: pip install "sqlery[{extra}]"'


def _launch(module: str, func: str):
    try:
        entry = getattr(import_module(module), func)
    except ImportError as exc:
        hint = install_hint(exc.name)
        if hint is None:
            raise
        sys.exit(hint)
    return entry()


def main():
    return _launch("sqlery.core.cli", "main")


def worker_main():
    return _launch("sqlery.fastapi_sqlery.cli", "worker_main")


def web_main():
    return _launch("sqlery.fastapi_sqlery.cli", "web_main")


def daemon_main():
    return _launch("sqlery.core.cli", "daemon_main")


def jobs_main():
    return _launch("sqlery.core.cli", "jobs_main")


def cleanup_main():
    return _launch("sqlery.core.cli", "cleanup_main")


def migrate_main():
    return _launch("sqlery.core.cli", "migrate_main")


def tasks_main():
    return _launch("sqlery.core.cli", "tasks_main")


def queues_main():
    return _launch("sqlery.core.cli", "queues_main")
