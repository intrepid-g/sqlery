# Backward compatibility stub - re-exports from django_sqlery.decorators
# Import from sqlery.django_sqlery.decorators for new code

try:
    from .django_sqlery.decorators import job, async_job, JobFunction, AsyncJobFunction
    # Backward compatibility alias
    JobWrapper = JobFunction
    __all__ = ["job", "async_job", "JobFunction", "AsyncJobFunction", "JobWrapper"]
except ImportError:
    # Django not installed or not configured
    __all__ = []
