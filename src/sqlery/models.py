# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/
# Remove after 2027-05-14 (12 months from CLEAN-01 stamp date 2026-05-14, per Phase 04 CONTEXT).
# This stub exists for backward compatibility during migration.
# When django-sqlery is extracted to a separate package, this file will be removed.
#
# Re-export from new location for backward compatibility
try:
    from sqlery.django_sqlery.models import (
        QueuedJob,
        ScheduledTask,
        Worker,
        JobRegistry,
        TagLock,
        ConcurrentModificationError,
    )
    # Backward compatibility alias
    TaskExecution = QueuedJob
    __all__ = [
        "QueuedJob",
        "ScheduledTask",
        "Worker",
        "JobRegistry",
        "TagLock",
        "ConcurrentModificationError",
        "TaskExecution",
    ]
except ImportError:
    # Django not installed or not configured
    pass
