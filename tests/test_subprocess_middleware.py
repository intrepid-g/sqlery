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


@pytest.mark.django_db
class TestSubprocessTriggerLifecycle:
    """Integration tests for the full middleware → command → executor lifecycle.

    Instead of mocking Popen entirely, these tests replace it with
    a synchronous call_command('run_jobs') to exercise the real executor
    path while keeping tests deterministic.
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear cache before each test."""
        cache.clear()

    def _make_middleware(self, settings):
        """Configure settings and return a wired-up middleware."""
        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "subprocess",
            "ENABLE_MIDDLEWARE_TRIGGER": True,
            "CHECK_INTERVAL_SECONDS": 60,
        }
        get_response = MagicMock(return_value=MagicMock(status_code=200))
        return SubprocessTriggerMiddleware(get_response)

    def test_full_lifecycle_queued_to_success(self, settings):
        """End-to-end: middleware triggers subprocess → run_jobs → job succeeds."""
        from django.core.management import call_command
        from sqlery.models import QueuedJob

        middleware = self._make_middleware(settings)

        # Create a queued job pointing at a real task function
        job = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="default",
            status="queued",
        )

        # Replace Popen with synchronous call_command to simulate the subprocess
        def fake_popen(cmd, **kwargs):
            call_command("run_jobs")
            return MagicMock()

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen", side_effect=fake_popen):
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")
                middleware(request)

        # Verify job transitioned to success
        job.refresh_from_db()
        assert job.status == "success"
        assert job.output == "Success"
        assert job.started_at is not None
        assert job.finished_at is not None

    def test_full_lifecycle_failing_job(self, settings):
        """End-to-end: middleware triggers subprocess → run_jobs → job fails."""
        from django.core.management import call_command
        from sqlery.models import QueuedJob

        middleware = self._make_middleware(settings)

        job = QueuedJob.objects.create(
            task_path="tests.test_executor.failing_task",
            queue_name="default",
            status="queued",
        )

        def fake_popen(cmd, **kwargs):
            call_command("run_jobs")
            return MagicMock()

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen", side_effect=fake_popen):
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")
                middleware(request)

        job.refresh_from_db()
        assert job.status == "failed"
        assert "Task failed" in job.error
        assert job.started_at is not None
        assert job.finished_at is not None

    def test_full_lifecycle_processes_one_job_only(self, settings):
        """Subprocess invocation processes exactly one job, leaving others queued."""
        from django.core.management import call_command
        from sqlery.models import QueuedJob

        middleware = self._make_middleware(settings)

        # Create two queued jobs
        job1 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="default",
            status="queued",
            priority=10,  # higher priority, processed first
        )
        job2 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="default",
            status="queued",
            priority=0,
        )

        popen_count = 0

        def fake_popen(cmd, **kwargs):
            nonlocal popen_count
            popen_count += 1
            # Only run the first call_command (the middleware trigger)
            # Ignore any chained worker spawn from _spawn_next_worker
            if popen_count == 1:
                call_command("run_jobs")
            return MagicMock()

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen", side_effect=fake_popen):
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")
                middleware(request)

        job1.refresh_from_db()
        job2.refresh_from_db()

        # Higher-priority job processed, lower-priority job still queued
        assert job1.status == "success"
        assert job2.status == "queued"

    def test_full_lifecycle_empty_queue(self, settings):
        """Middleware triggers with no queued jobs — no crash, no side effects."""
        from django.core.management import call_command
        from sqlery.models import QueuedJob

        middleware = self._make_middleware(settings)

        def fake_popen(cmd, **kwargs):
            call_command("run_jobs")
            return MagicMock()

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen", side_effect=fake_popen):
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")
                response = middleware(request)

        assert response is not None
        assert QueuedJob.objects.count() == 0

    def test_throttle_recovery_after_cache_expiry(self, settings):
        """After cache key expires, the next request re-triggers subprocess spawn."""
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

                # First request — spawns
                middleware(request)
                assert mock_popen.call_count == 1

                # Second request — throttled
                middleware(request)
                assert mock_popen.call_count == 1

                # Expire the cache key (simulates CHECK_INTERVAL_SECONDS passing)
                cache.delete("sqlery:last_subprocess_trigger")

                # Third request — should spawn again
                middleware(request)
                assert mock_popen.call_count == 2

    def test_throttle_recovery_spawns_for_new_job(self, settings):
        """After throttle expires, a new job is picked up by the re-triggered subprocess."""
        from django.core.management import call_command
        from sqlery.models import QueuedJob

        middleware = self._make_middleware(settings)

        # First cycle: create and process job1
        job1 = QueuedJob.objects.create(
            task_path="tests.test_executor.dummy_task",
            queue_name="default",
            status="queued",
        )

        call_count = 0

        def fake_popen(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            call_command("run_jobs")
            return MagicMock()

        with patch("sqlery.django_sqlery.subprocess_middleware.subprocess.Popen", side_effect=fake_popen):
            with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path") as mock_path:
                mock_path.return_value = "/path/to/manage.py"

                factory = RequestFactory()
                request = factory.get("/")

                # First request processes job1
                middleware(request)
                assert call_count >= 1

                job1.refresh_from_db()
                assert job1.status == "success"

                # Throttled — second request does nothing
                job2 = QueuedJob.objects.create(
                    task_path="tests.test_executor.dummy_task",
                    queue_name="default",
                    status="queued",
                )
                prev_count = call_count
                middleware(request)
                job2.refresh_from_db()
                assert job2.status == "queued"  # not yet processed

                # Expire throttle
                cache.delete("sqlery:last_subprocess_trigger")

                # Third request — picks up job2
                middleware(request)
                job2.refresh_from_db()
                assert job2.status == "success"
