# Backward compatibility stub - re-exports from core.worker
# Import from sqlery.core.worker for new code

from .core.worker import JobExecutor, WorkerProcess

# Alias for backward compatibility
Worker = WorkerProcess

__all__ = [
    "Worker",
    "WorkerProcess",
    "JobExecutor",
]
