"""Django integration for sqlery.

This module contains all Django-specific code for sqlery.

Usage:
    from sqlery.django_sqlery.models import QueuedJob, ScheduledTask
    from sqlery.django_sqlery.decorators import job
    from sqlery.django_sqlery.job_queue import enqueue, enqueue_at

Note: This is a Django app package. Import specific modules directly.
Do not import from this __init__.py to avoid circular dependencies during app loading.
"""

# Old: __version__ = "0.11.0"  — drifted; until extracted, ships as part of sqlery.
# Derive from the single source (pyproject.toml) via package metadata. Uses importlib.metadata
# only (no parent-package import) to keep Django app loading free of circular imports.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("sqlery")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

# Minimal Django app package - no eager imports to avoid circular dependencies
# Import from specific modules like:
#   from sqlery.django_sqlery.models import QueuedJob
#   from sqlery.django_sqlery.decorators import job

# default_app_config = 'sqlery.django_sqlery.apps.DjangoSqleryConfig'  # Removed: deprecated in Django 3.2+, breaks Django 6


# Lazy export: DjangoAsyncBackend (ASYN-02). Importing eagerly would
# trigger model imports during Django app loading and break the
# "no eager imports" contract documented above. Callers use:
#     from sqlery.django_sqlery import DjangoAsyncBackend
__all__ = ["DjangoAsyncBackend"]


def __getattr__(name):
    if name == "DjangoAsyncBackend":
        from .async_backend import DjangoAsyncBackend

        return DjangoAsyncBackend
    raise AttributeError(f"module 'sqlery.django_sqlery' has no attribute {name!r}")
