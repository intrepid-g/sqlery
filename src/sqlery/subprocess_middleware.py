# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/
# This stub exists for backward compatibility during migration.
# Re-export from new location for backward compatibility
try:
    from sqlery.django_sqlery.subprocess_middleware import SubprocessTriggerMiddleware
    __all__ = ["SubprocessTriggerMiddleware"]
except ImportError:
    pass
