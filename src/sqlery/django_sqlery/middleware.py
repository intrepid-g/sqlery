"""Middleware for triggering scheduled tasks and queue workers on requests."""

from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class ScheduledTaskMiddleware:
    """Middleware to trigger scheduled tasks and queue workers on requests.

    Performs two actions (throttled by cache):
    1. Enqueues jobs for due scheduled tasks
    2. Processes queued jobs
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if we should trigger scheduler and workers
        self.maybe_trigger_scheduler()
        self.maybe_trigger_workers()

        response = self.get_response(request)
        return response

    def maybe_trigger_scheduler(self):
        """Check for due scheduled tasks and enqueue jobs (throttled)."""
        from .settings import get_setting
        from sqlery.triggers import trigger_due_tasks

        # Check if enabled
        if not get_setting("ENABLE_MIDDLEWARE_TRIGGER", True):
            return

        # Throttle checks (don't check on every request)
        check_interval = get_setting("CHECK_INTERVAL_SECONDS", 60)
        cache_key = "sqlery:last_scheduler_check"

        if cache.get(cache_key):
            return  # Already checked recently

        # Set cache for next interval
        cache.set(cache_key, True, check_interval)

        # Trigger scheduler asynchronously
        try:
            trigger_due_tasks()
        except Exception as e:
            logger.error(f"Failed to trigger scheduler: {e}")

    def maybe_trigger_workers(self):
        """Process queued jobs (throttled)."""
        from .settings import get_setting
        from sqlery.triggers import trigger_queue_workers

        # Check if enabled
        if not get_setting("ENABLE_MIDDLEWARE_TRIGGER", True):
            return

        # Throttle checks (separate from scheduler)
        check_interval = get_setting("CHECK_INTERVAL_SECONDS", 60)
        cache_key = "sqlery:last_worker_check"

        if cache.get(cache_key):
            return  # Already checked recently

        # Set cache for next interval
        cache.set(cache_key, True, check_interval)

        # Trigger workers asynchronously
        try:
            trigger_queue_workers()
        except Exception as e:
            logger.error(f"Failed to trigger workers: {e}")
