"""Tests for direct subprocess trigger middleware."""

import pytest
from unittest.mock import patch, MagicMock, call
from django.test import RequestFactory
from django.core.cache import cache
from sqlery.subprocess_middleware import SubprocessTriggerMiddleware


class TestSubprocessTriggerMiddleware:
    """Test direct subprocess spawning middleware."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear cache before each test."""
        cache.clear()

    def test_middleware_spawns_subprocess_when_due(self, settings):
        """Middleware should spawn subprocess when interval passes."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
            "CHECK_INTERVAL_SECONDS": 60,
        }

        # Create middleware
        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        # Mock subprocess.Popen and get_manage_py_path
        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                # Create request
                factory = RequestFactory()
                request = factory.get("/")

                # Process request
                middleware(request)

                # Should have spawned subprocess
                mock_popen.assert_called_once()

                # Verify command
                args = mock_popen.call_args
                cmd = args[0][0]
                assert "/path/to/manage.py" in cmd
                assert "run_jobs" in cmd
                assert "run_jobs" in cmd

    def test_middleware_respects_throttle(self, settings):
        """Middleware should not spawn on every request (throttled)."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
            "CHECK_INTERVAL_SECONDS": 60,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")

                # First request - should spawn
                middleware(request)
                assert mock_popen.call_count == 1

                # Second request immediately - should NOT spawn (throttled)
                middleware(request)
                assert mock_popen.call_count == 1  # Still 1, not 2

                # Third request - still throttled
                middleware(request)
                assert mock_popen.call_count == 1

    def test_middleware_disabled_when_trigger_mode_wrong(self, settings):
        """Middleware should not spawn when TRIGGER_MODE != 'subprocess'."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "http",  # Wrong mode
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            factory = RequestFactory()
            request = factory.get("/")

            middleware(request)

            # Should NOT spawn
            mock_popen.assert_not_called()

    def test_middleware_disabled_when_trigger_disabled(self, settings):
        """Middleware should not spawn when ENABLE_MIDDLEWARE_TRIGGER=False."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": False,  # Disabled
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            factory = RequestFactory()
            request = factory.get("/")

            middleware(request)

            # Should NOT spawn
            mock_popen.assert_not_called()

    def test_middleware_passes_environment(self, settings):
        """Subprocess should inherit environment variables."""
        import os

        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")

                middleware(request)

                # Verify env was passed
                args, kwargs = mock_popen.call_args
                assert "env" in kwargs
                assert kwargs["env"] == os.environ

    def test_middleware_uses_start_new_session(self, settings):
        """Subprocess should use start_new_session to prevent zombies."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")

                middleware(request)

                # Verify start_new_session=True
                args, kwargs = mock_popen.call_args
                assert kwargs["start_new_session"] is True
                assert kwargs["close_fds"] is True

    def test_middleware_redirects_output(self, settings):
        """Subprocess should redirect stdout/stderr to DEVNULL."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.DEVNULL") as mock_devnull:
                    mock_path.return_value = "/path/to/manage.py"

                    factory = RequestFactory()
                    request = factory.get("/")

                    middleware(request)

                    # Verify output redirected
                    args, kwargs = mock_popen.call_args
                    assert kwargs["stdout"] == mock_devnull
                    assert kwargs["stderr"] == mock_devnull

    def test_middleware_handles_spawn_failure(self, settings):
        """Middleware should handle subprocess spawn failures gracefully."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"
                mock_popen.side_effect = OSError("Resource temporarily unavailable")

                factory = RequestFactory()
                request = factory.get("/")

                # Should not raise exception
                response = middleware(request)

                # Should still return response
                assert response is not None

    def test_middleware_processes_request_first(self, settings):
        """Middleware should process request before spawning subprocess."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock(status_code=200))
        middleware = SubprocessTriggerMiddleware(get_response)

        call_order = []

        def track_get_response(request):
            call_order.append("get_response")
            return MagicMock(status_code=200)

        def track_popen(*args, **kwargs):
            call_order.append("popen")
            return MagicMock()

        middleware.get_response = track_get_response

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen", side_effect=track_popen):
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")

                middleware(request)

                # get_response should be called before subprocess spawn
                assert call_order == ["get_response", "popen"]

    def test_middleware_uses_absolute_path(self, settings):
        """Middleware should use absolute path from get_manage_py_path."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
        }

        get_response = MagicMock(return_value=MagicMock())
        middleware = SubprocessTriggerMiddleware(get_response)

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen") as mock_popen:
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/absolute/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")

                middleware(request)

                # Verify absolute path used
                args = mock_popen.call_args[0][0]
                assert "/absolute/path/to/manage.py" in args
                # Verify get_manage_py_path was called
                mock_path.assert_called_once()
