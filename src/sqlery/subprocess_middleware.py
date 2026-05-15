# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/
# Remove after 2027-05-14 (12 months from CLEAN-01 stamp date 2026-05-14, per Phase 04 CONTEXT).
# This stub exists for backward compatibility during migration.
# Re-export from new location for backward compatibility
try:
    from sqlery.django_sqlery.subprocess_middleware import SubprocessTriggerMiddleware
    __all__ = ["SubprocessTriggerMiddleware"]
except ImportError:
    pass
