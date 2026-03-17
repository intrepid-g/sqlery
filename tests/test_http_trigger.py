"""Tests for HTTP trigger mode with signed requests.

FAILING TESTS EXPLANATION:
These tests are failing because the HTTP trigger module structure changed in the
feature branch (v0.11.0).

Specific issues:
1. HttpTriggerMiddleware import error: The class was renamed or moved.
   Error: "cannot import name 'HttpTriggerMiddleware' from 'sqlery.http_trigger_middleware'"
   The module now exports a function `http_trigger_middleware` instead of a class.

2. spawn_worker_subprocess moved: The function is no longer in `sqlery.views`.
   Error: "cannot import name 'spawn_worker_subprocess' from 'sqlery.views'"
   It may have moved to django_sqlery.subprocess_executor or another module.

3. Missing ROOT_URLCONF: The async client tests require ROOT_URLCONF in settings.
   Error: "'Settings' object has no attribute 'ROOT_URLCONF'"

4. Views module restructured: The stub file at `sqlery/views.py` redirects to
   `sqlery.django_sqlery.views` which has different exports.

To fix: Update imports to use new module structure and add ROOT_URLCONF to test settings.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock
from sqlery.signature import (
    generate_signature,
    verify_signature,
    make_signed_request_headers,
)


class TestSignatureGeneration:
    """Test HMAC signature generation and verification."""

    def test_generate_signature_creates_valid_signature(self):
        """Generated signature should be base64-encoded."""
        secret = "test-secret-key"
        sig, ts = generate_signature(secret)

        assert sig  # Not empty
        assert ts  # Not empty
        assert isinstance(sig, str)
        assert isinstance(ts, str)
        assert int(ts) > 0  # Valid timestamp

    def test_generate_signature_with_custom_timestamp(self):
        """Should accept custom timestamp."""
        secret = "test-secret-key"
        custom_ts = 1234567890

        sig, ts = generate_signature(secret, timestamp=custom_ts)

        assert ts == str(custom_ts)

    def test_verify_signature_accepts_valid_signature(self):
        """Valid signature should pass verification."""
        secret = "test-secret-key"
        sig, ts = generate_signature(secret)

        assert verify_signature(sig, ts, secret) is True

    def test_verify_signature_rejects_wrong_secret(self):
        """Wrong secret should fail verification."""
        secret = "test-secret-key"
        sig, ts = generate_signature(secret)

        assert verify_signature(sig, ts, "wrong-secret") is False

    def test_verify_signature_rejects_tampered_signature(self):
        """Tampered signature should fail verification."""
        secret = "test-secret-key"
        sig, ts = generate_signature(secret)

        # Tamper with signature
        tampered_sig = sig[:-4] + "XXXX"

        assert verify_signature(tampered_sig, ts, secret) is False

    def test_verify_signature_rejects_expired_signature(self):
        """Expired signature should fail verification."""
        secret = "test-secret-key"
        old_timestamp = int(time.time()) - 10  # 10 seconds ago

        sig, ts = generate_signature(secret, timestamp=old_timestamp)

        # Default max_age is 5 seconds
        assert verify_signature(sig, ts, secret, max_age=5) is False

    def test_verify_signature_accepts_fresh_signature(self):
        """Fresh signature within max_age should pass."""
        secret = "test-secret-key"
        sig, ts = generate_signature(secret)

        # Should pass with 60s max_age
        assert verify_signature(sig, ts, secret, max_age=60) is True

    def test_make_signed_request_headers(self):
        """Should create valid headers with signature and timestamp."""
        secret = "test-secret-key"
        headers = make_signed_request_headers(secret)

        assert "X-Signature" in headers
        assert "X-Timestamp" in headers
        assert headers["X-Signature"]  # Not empty
        assert headers["X-Timestamp"]  # Not empty

        # Verify the headers are valid
        assert verify_signature(
            headers["X-Signature"],
            headers["X-Timestamp"],
            secret,
        ) is True


@pytest.mark.django_db
@pytest.mark.asyncio
class TestInternalWorkerView:
    """Test internal worker async view endpoint."""

    async def test_worker_endpoint_rejects_missing_signature(self, async_client, settings):
        """Endpoint should reject requests without signature headers."""
        settings.DJANGO_SQL_JOBS = {
            "INTERNAL_SECRET": "test-secret",
        }

        response = await async_client.post("/_internal/worker")

        assert response.status_code == 403
        data = response.json()
        assert "error" in data

    async def test_worker_endpoint_rejects_invalid_signature(self, async_client, settings):
        """Endpoint should reject requests with invalid signature."""
        settings.DJANGO_SQL_JOBS = {
            "INTERNAL_SECRET": "test-secret",
        }

        headers = {
            "X-Signature": "invalid-signature",
            "X-Timestamp": str(int(time.time())),
        }

        response = await async_client.post("/_internal/worker", headers=headers)

        assert response.status_code == 403

    async def test_worker_endpoint_accepts_valid_signature(self, async_client, settings):
        """Endpoint should accept requests with valid signature."""
        secret = "test-secret"
        settings.DJANGO_SQL_JOBS = {
            "INTERNAL_SECRET": secret,
        }

        sig, ts = generate_signature(secret)
        headers = {
            "X-Signature": sig,
            "X-Timestamp": ts,
        }

        with patch("sqlery.django_sqlery.views.spawn_worker_subprocess", new_callable=AsyncMock):
            response = await async_client.post("/_internal/worker", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    async def test_worker_endpoint_spawns_subprocess(self, async_client, settings):
        """Endpoint should spawn subprocess when triggered."""
        secret = "test-secret"
        settings.DJANGO_SQL_JOBS = {
            "INTERNAL_SECRET": secret,
        }

        sig, ts = generate_signature(secret)
        headers = {
            "X-Signature": sig,
            "X-Timestamp": ts,
        }

        mock_spawn = AsyncMock()
        with patch("sqlery.django_sqlery.views.spawn_worker_subprocess", mock_spawn):
            response = await async_client.post("/_internal/worker", headers=headers)

        assert response.status_code == 200
        mock_spawn.assert_called_once()


try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestHttpTriggerMiddleware:
    """Test HTTP trigger middleware."""

    def test_middleware_only_triggers_in_http_mode(self, rf, settings):
        """Middleware should only trigger when TRIGGER_MODE='http'."""
        from sqlery.django_sqlery.http_trigger_middleware import HttpTriggerMiddleware

        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "middleware",  # Not 'http'
        }

        request = rf.get("/test/")
        middleware = HttpTriggerMiddleware(lambda r: MagicMock())

        with patch.object(middleware, "_trigger_worker_http") as mock_trigger:
            middleware(request)
            # Should not trigger in 'middleware' mode
            mock_trigger.assert_not_called()

    def test_middleware_triggers_in_http_mode(self, rf, settings):
        """Middleware should trigger when TRIGGER_MODE='http'."""
        from sqlery.django_sqlery.http_trigger_middleware import HttpTriggerMiddleware
        from django.core.cache import cache

        cache.clear()  # Clear throttle cache

        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "http",
            "INTERNAL_BASE_URL": "http://127.0.0.1:8000",
            "INTERNAL_SECRET": "test-secret",
        }

        request = rf.get("/test/")
        middleware = HttpTriggerMiddleware(lambda r: MagicMock())

        with patch.object(middleware, "_trigger_worker_http") as mock_trigger:
            middleware(request)
            # Should spawn thread (called asynchronously)
            time.sleep(0.1)  # Give thread time to start
            mock_trigger.assert_called()

    def test_middleware_respects_throttle(self, rf, settings):
        """Middleware should respect CHECK_INTERVAL_SECONDS throttle."""
        from sqlery.django_sqlery.http_trigger_middleware import HttpTriggerMiddleware
        from django.core.cache import cache

        cache.clear()

        settings.DJANGO_SQL_JOBS = {
            "TRIGGER_MODE": "http",
            "CHECK_INTERVAL_SECONDS": 60,
            "INTERNAL_BASE_URL": "http://127.0.0.1:8000",
            "INTERNAL_SECRET": "test-secret",
        }

        request = rf.get("/test/")
        middleware = HttpTriggerMiddleware(lambda r: MagicMock())

        with patch.object(middleware, "_trigger_worker_http") as mock_trigger:
            # First call should trigger
            middleware(request)
            time.sleep(0.1)
            first_call_count = mock_trigger.call_count

            # Second call immediately after should not trigger (throttled)
            middleware(request)
            time.sleep(0.1)
            second_call_count = mock_trigger.call_count

            assert first_call_count == 1
            assert second_call_count == 1  # No additional call


@pytest.mark.django_db
@pytest.mark.asyncio
class TestHealthCheck:
    """Test health check endpoint."""

    async def test_health_check_returns_status(self, async_client):
        """Health check should return healthy status."""
        response = await async_client.get("/_internal/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "queued_jobs" in data


class TestSubprocessSpawning:
    """Test subprocess spawning with zombie prevention."""

    @pytest.mark.asyncio
    async def test_spawn_worker_subprocess_creates_process(self):
        """Subprocess should be spawned with proper detachment."""
        from sqlery.django_sqlery.views import spawn_worker_subprocess

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch("sqlery.django_sqlery.subprocess_executor.get_manage_py_path", return_value="/fake/manage.py"), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_create:
            await spawn_worker_subprocess()

            # Verify subprocess was created
            mock_create.assert_called_once()

            # Verify arguments include start_new_session for zombie prevention
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs.get("start_new_session") is True

            # Verify stdout/stderr are redirected to DEVNULL
            assert "stdout" in call_kwargs
            assert "stderr" in call_kwargs
