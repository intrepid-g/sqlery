"""Django integration for sqlery.

This module contains all Django-specific code for sqlery.

Usage:
    from sqlery.django_sqlery.models import QueuedJob, ScheduledTask
    from sqlery.django_sqlery.decorators import job
    from sqlery.django_sqlery.job_queue import enqueue, enqueue_at

Note: This is a Django app package. Import specific modules directly.
Do not import from this __init__.py to avoid circular dependencies during app loading.
"""

__version__ = "0.11.0"

# Minimal Django app package - no eager imports to avoid circular dependencies
# Import from specific modules like:
#   from sqlery.django_sqlery.models import QueuedJob
#   from sqlery.django_sqlery.decorators import job

# default_app_config = 'sqlery.django_sqlery.apps.DjangoSqleryConfig'  # Removed: deprecated in Django 3.2+, breaks Django 6
