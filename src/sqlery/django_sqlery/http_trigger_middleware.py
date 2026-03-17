"""Middleware for triggering workers via signed HTTP request after response."""

import threading
import logging
import httpx
from django.core.cache import cache
from .settings import get_setting
from .signature import make_signed_request_headers

logger = logging.getLogger(__name__)


class HttpTriggerMiddleware:
    """Middleware to trigger workers via signed internal HTTP request.

    This middleware:
    1. Processes the response normally (no blocking)
    2. After response, spawns thread to make signed HTTP request
    3. Internal endpoint receives request, spawns subprocess for workers
    4. Works with ASGI/uvicorn for true async (1min+ jobs won't block)

    Advantages over direct middleware execution:
    - True process isolation (subprocess via HTTP endpoint)
    - Works with async views (ASGI-compatible)
    - Long-running jobs don't block event loop
    - Proper zombie prevention via start_new_session

    Settings:
    - TRIGGER_MODE: 'http', 'middleware', or 'disabled'
    - INTERNAL_BASE_URL: Base URL for internal requests (e.g., http://127.0.0.1:8000)
    - INTERNAL_SECRET: Shared secret for HMAC signatures
    - CHECK_INTERVAL_SECONDS: Throttle interval (default: 60)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Process request and get response
        response = self.get_response(request)

        # Trigger worker after response (non-blocking)
        self.maybe_trigger_worker()

        return response

    def maybe_trigger_worker(self):
        """Trigger worker via HTTP request (throttled)."""
        trigger_mode = get_setting("TRIGGER_MODE", "middleware")

        # Only trigger if mode is 'http'
        if trigger_mode != "http":
            return

        # Check if enabled
        if not get_setting("ENABLE_MIDDLEWARE_TRIGGER", True):
            return

        # Throttle checks (don't trigger on every request)
        check_interval = get_setting("CHECK_INTERVAL_SECONDS", 60)
        cache_key = "sqlery:last_http_trigger"

        if cache.get(cache_key):
            return  # Already triggered recently

        # Set cache for next interval
        cache.set(cache_key, True, check_interval)

        # Spawn thread to make HTTP request (non-blocking)
        thread = threading.Thread(
            target=self._trigger_worker_http,
            daemon=True,
        )
        thread.start()

    def _trigger_worker_http(self):
        """Make signed HTTP request to internal worker endpoint."""
        try:
            base_url = get_setting("INTERNAL_BASE_URL")
            secret = get_setting("INTERNAL_SECRET")

            if not base_url:
                logger.error("INTERNAL_BASE_URL not configured")
                return

            if not secret:
                logger.error("INTERNAL_SECRET not configured")
                return

            # Generate signed headers
            headers = make_signed_request_headers(secret)

            # Make request (short timeout since endpoint returns immediately)
            url = f"{base_url}/_internal/worker"

            with httpx.Client(timeout=2.0) as client:
                response = client.post(url, headers=headers)

            if response.status_code == 200:
                logger.info("Worker triggered successfully via HTTP")
            else:
                logger.error(
                    f"Worker trigger failed: status={response.status_code}, "
                    f"body={response.text[:100]}"
                )

        except httpx.TimeoutException:
            logger.error("Worker trigger HTTP request timed out")
        except Exception as e:
            logger.error(f"Failed to trigger worker via HTTP: {e}")
