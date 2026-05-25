"""Tests for ScheduledTaskMiddleware."""

import pytest
import time
from unittest.mock import patch, MagicMock
from django.core.cache import cache
from sqlery.middleware import ScheduledTaskMiddleware

# Patch target is where the import happens in the middleware module
TRIGGER_DUE_TASKS_PATH = "sqlery.django_sqlery.middleware.trigger_due_tasks"
TRIGGER_QUEUE_WORKERS_PATH = "sqlery.django_sqlery.middleware.trigger_queue_workers"


class TestScheduledTaskMiddleware:
    """Test ScheduledTaskMiddleware behavior."""

    def test_middleware_calls_response_handler(self, rf):
        """Test that middleware properly calls get_response."""
        request = rf.get("/test/")
        response = MagicMock()

        get_response = MagicMock(return_value=response)
        middleware = ScheduledTaskMiddleware(get_response)

        result = middleware(request)

        get_response.assert_called_once_with(request)
        assert result == response

    def test_middleware_triggers_scheduler(self, rf, settings):
        """Test that middleware triggers scheduler."""
        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        request = rf.get("/test/")
        middleware = ScheduledTaskMiddleware(lambda r: MagicMock())

        with patch(TRIGGER_DUE_TASKS_PATH) as mock_trigger:
            middleware(request)
            # Give thread time to start
            time.sleep(0.1)
            # Verify trigger was called with no arguments
            mock_trigger.assert_called_once_with()

    def test_middleware_triggers_workers(self, rf, settings):
        """Test that middleware triggers queue workers."""
        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        request = rf.get("/test/")
        middleware = ScheduledTaskMiddleware(lambda r: MagicMock())

        with patch(TRIGGER_QUEUE_WORKERS_PATH) as mock_trigger:
            middleware(request)
            # Give thread time to start
            time.sleep(0.1)
            # Verify worker trigger was called (may have queue_name=None)
            mock_trigger.assert_called_once()

    def test_middleware_respects_enable_setting(self, rf, settings):
        """Test that middleware can be disabled via settings."""
        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "ENABLE_MIDDLEWARE_TRIGGER": False,
        }

        request = rf.get("/test/")
        middleware = ScheduledTaskMiddleware(lambda r: MagicMock())

        with patch(TRIGGER_DUE_TASKS_PATH) as mock_scheduler:
            with patch(TRIGGER_QUEUE_WORKERS_PATH) as mock_worker:
                middleware(request)
                time.sleep(0.1)

                # Should not trigger
                mock_scheduler.assert_not_called()
                mock_worker.assert_not_called()

    def test_middleware_respects_throttle_interval(self, rf, settings):
        """Test that middleware respects CHECK_INTERVAL_SECONDS."""
        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "ENABLE_MIDDLEWARE_TRIGGER": True,
            "CHECK_INTERVAL_SECONDS": 60,
        }

        request = rf.get("/test/")
        middleware = ScheduledTaskMiddleware(lambda r: MagicMock())

        with patch(TRIGGER_DUE_TASKS_PATH) as mock_trigger:
            # First request should trigger
            middleware(request)
            time.sleep(0.1)
            first_call_count = mock_trigger.call_count

            # Second request immediately after should not trigger (throttled)
            middleware(request)
            time.sleep(0.1)
            second_call_count = mock_trigger.call_count

            assert first_call_count == 1
            assert second_call_count == 1  # No additional call

    def test_middleware_handles_errors_gracefully(self, rf, settings):
        """Test that middleware doesn't crash if trigger fails."""
        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        request = rf.get("/test/")
        response = MagicMock()
        middleware = ScheduledTaskMiddleware(lambda r: response)

        with patch(TRIGGER_DUE_TASKS_PATH, side_effect=Exception("Test error")):
            # Should not raise exception
            result = middleware(request)

            # Should still return response
            assert result == response

    def test_middleware_separate_throttles_for_scheduler_and_worker(self, rf, settings):
        """Test that scheduler and worker have separate throttle keys."""
        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "ENABLE_MIDDLEWARE_TRIGGER": True,
            "CHECK_INTERVAL_SECONDS": 60,
        }

        request = rf.get("/test/")
        middleware = ScheduledTaskMiddleware(lambda r: MagicMock())

        # First call should set both throttles
        middleware(request)
        time.sleep(0.1)

        # Check that both cache keys are set
        scheduler_key = cache.get("sqlery:last_scheduler_check")
        worker_key = cache.get("sqlery:last_worker_check")

        assert scheduler_key is not None
        assert worker_key is not None
