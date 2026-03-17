"""FastAPI integration for sqlery.

This module contains all FastAPI-specific code for sqlery. Eventually, this will be
extracted to a separate `fastapi-sqlery` package that depends on the core `sqlery` package.

For now, all FastAPI functionality lives here and can be imported directly:

    from sqlery.fastapi_sqlery import create_app
    from sqlery.fastapi_sqlery.backend import FastAPIBackend
    from sqlery.fastapi_sqlery.cli import app as cli_app

Note: The folder is named fastapi_sqlery (with underscore) to match the future package
import name when extracted. The PyPI package will be fastapi-sqlery (with hyphen).

Backward compatibility imports are provided below for the transition period.
"""

# Re-export key FastAPI components for convenience
try:
    from .app import create_app, app as default_app
    from .backend import FastAPIBackend
    from .cli import app as cli_app

    __all__ = [
        'create_app',
        'default_app',
        'FastAPIBackend',
        'cli_app',
    ]

# except ImportError as e:  # Too narrow — starlette raises AssertionError when jinja2 is missing
except Exception as e:
    # FastAPI or its dependencies not fully installed - this is expected in standalone/Django-only mode
    import warnings
    warnings.warn(
        f"FastAPI integration not available: {e}. "
        "Install FastAPI to use sqlery's FastAPI integration: pip install 'sqlery[fastapi]', "
        "or use Django mode or standalone mode instead.",
        ImportWarning
    )
    __all__ = []


# Version info
__version__ = "0.11.0"  # Will become 1.0.0 when extracted to fastapi-sqlery


# Future migration note
def _migration_note():
    """
    MIGRATION ROADMAP:

    This fastapi_sqlery/ subfolder will eventually become the fastapi-sqlery package:

    Before (current - monorepo):
        pip install sqlery[fastapi]
        from sqlery.fastapi_sqlery import create_app

    After (future - separate package):
        pip install fastapi-sqlery  # Automatically installs sqlery core
        from fastapi_sqlery import create_app  # Just drop "sqlery." prefix

    The core sqlery package will remain framework-agnostic and work with
    any Python application (Django, Flask, etc.)

    Package naming:
        PyPI package: fastapi-sqlery (with hyphen)
        Import name: fastapi_sqlery (with underscore)
    """
    pass
