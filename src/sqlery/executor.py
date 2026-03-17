# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/
# This stub exists for backward compatibility during migration.
# When django-sqlery is extracted to a separate package, this file will be removed.
#
# Re-export from new location for backward compatibility
try:
    from sqlery.django_sqlery.executor import TaskExecutor
    __all__ = ["TaskExecutor"]
except ImportError:
    # Django not installed or not configured
    pass
