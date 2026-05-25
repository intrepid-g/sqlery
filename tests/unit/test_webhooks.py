"""Unit tests for `sqlery.webhooks` (TEST-10).

Covers:
- HMAC-SHA256 signature generation (determinism, header naming, body-sensitivity).
- HTTP delivery success / 4xx / 5xx / network-error paths.
- Retry/backoff bookkeeping in ``send_webhook_with_retry``.
- Event filtering (skip when event not in ``webhook_events``).
- Payload shape (documented fields are present).

No real network calls — ``sqlery.webhooks.requests`` is patched.
``time.sleep`` is patched defensively (the current module does not call it
in-process, but the patch protects against future regressions if backoff
is added inline).

Mocking strategy: stdlib ``unittest.mock`` only — ``pytest-mock`` is NOT a
declared dependency in ``pyproject.toml`` and MUST NOT be imported here.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# `sqlery.webhooks` (the shim at src/sqlery/webhooks.py) has two pre-existing
# import quirks tracked elsewhere (Phase 4 cleanup):
#   1. `from .settings import get_setting` — `sqlery/settings.py` is an empty
#      backward-compat stub that no longer re-exports `get_setting`.
#   2. `import requests` — not declared in `pyproject.toml` deps.
# To unit-test the module in isolation without modifying production code,
# we install a minimal stub for `sqlery.settings.get_setting` BEFORE the
# `from sqlery import webhooks` line. The `requests` import is wrapped in
# try/except inside the module itself, so we don't need to stub it (we
# patch it per-test via `unittest.mock.patch`).
import sys as _sys
import sqlery.settings as _sqlery_settings  # empty stub module

if not hasattr(_sqlery_settings, "get_setting"):
    def _stub_get_setting(name, default=None):  # pragma: no cover - test shim
        return default

    _sqlery_settings.get_setting = _stub_get_setting

from sqlery import webhooks as webhooks_mod  # noqa: E402
from sqlery.webhooks import (  # noqa: E402
    generate_webhook_signature,
    send_webhook,
    send_webhook_with_retry,
)


@pytest.fixture(autouse=True)
def _bypass_ssrf_validation():
    """Bypass SSRF validation in unit tests.

    The SSRF module resolves DNS for the webhook URL before ``requests.post``
    is called.  Test URLs use ``example.test`` which cannot be resolved, so
    the validation blocks all deliveries.  We patch it to a no-op for the
    unit-test module where ``requests`` is already fully mocked.
    """
    with patch("sqlery.security.ssrf.validate_webhook_url", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_job(**overrides):
    """Build a MagicMock that walks and quacks like a ``QueuedJob`` row.

    Uses ``MagicMock`` (not a real Django model) so tests stay pure unit
    tests — no DB required. ``job.save(...)`` is recorded as a call on
    the mock, which lets us assert state transitions without touching
    the ORM.
    """
    defaults = dict(
        id=42,
        webhook_url="https://example.test/hook",
        webhook_events=["success", "failure"],
        webhook_status="pending",
        webhook_retries=0,
        webhook_max_retries=3,
        task_path="tests.dummy.task",
        status="success",
        queue_name="default",
        priority=0,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        started_at=datetime(2026, 1, 1, 12, 0, 1),
        finished_at=datetime(2026, 1, 1, 12, 0, 2),
        duration_seconds=1.0,
        output={"ok": True},
        error=None,
        retry_count=0,
        tags=["a", "b"],
    )
    defaults.update(overrides)
    job = MagicMock(spec_set=list(defaults.keys()) + ["save"])
    for k, v in defaults.items():
        setattr(job, k, v)
    return job


# ---------------------------------------------------------------------------
# TestHMACSigning
# ---------------------------------------------------------------------------


class TestHMACSigning:
    def test_deterministic_signature(self):
        """Same payload + secret -> same signature (every time)."""
        payload = {"event": "success", "job_id": 1}
        sig1 = generate_webhook_signature(payload, "topsecret")
        sig2 = generate_webhook_signature(payload, "topsecret")
        assert sig1 == sig2
        # SHA-256 hex = 64 chars
        assert len(sig1) == 64
        assert all(c in "0123456789abcdef" for c in sig1)

    def test_signature_pins_known_vector(self):
        """Pin the algorithm: payload {} signed with 'k' has a fixed hex digest.

        Mitigates T-03-09 (tampering of HMAC scheme): if anyone changes the
        algorithm/encoding silently, this assertion fails.
        """
        import hmac
        import hashlib

        secret = "k"
        payload = {}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        assert generate_webhook_signature(payload, secret) == expected

    def test_different_bodies_produce_different_signatures(self):
        sig_a = generate_webhook_signature({"x": 1}, "s")
        sig_b = generate_webhook_signature({"x": 2}, "s")
        assert sig_a != sig_b

    def test_empty_payload_signs_cleanly(self):
        assert generate_webhook_signature({}, "s") is not None

    def test_no_secret_returns_none(self):
        assert generate_webhook_signature({"x": 1}, None) is None
        assert generate_webhook_signature({"x": 1}, "") is None

    def test_signature_header_format(self):
        """Header is emitted as ``X-Sqlery-Signature: sha256=<hex>``."""
        job = make_job()
        fake_resp = MagicMock(status_code=200, text="OK")
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting"
        ) as fake_setting:
            fake_setting.side_effect = lambda name, default=None: {
                "WEBHOOK_SECRET": "topsecret",
                "WEBHOOK_TIMEOUT": 10,
            }.get(name, default)
            fake_requests.post.return_value = fake_resp
            assert send_webhook(job, event="success") is True
            _, kwargs = fake_requests.post.call_args
            headers = kwargs["headers"]
            assert "X-Sqlery-Signature" in headers
            assert headers["X-Sqlery-Signature"].startswith("sha256=")
            # Hex body of 64 chars after the prefix.
            assert len(headers["X-Sqlery-Signature"].split("=", 1)[1]) == 64


# ---------------------------------------------------------------------------
# TestHTTPDelivery
# ---------------------------------------------------------------------------


class TestHTTPDelivery:
    def _settings(self, name, default=None):
        return {"WEBHOOK_SECRET": None, "WEBHOOK_TIMEOUT": 5}.get(name, default)

    def test_post_2xx_marks_sent(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            assert send_webhook(job, event="success") is True
            assert job.webhook_status == "sent"
            job.save.assert_called_with(update_fields=["webhook_status"])

    @pytest.mark.parametrize("code", [201, 202, 204])
    def test_accepts_other_2xx_codes(self, code):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=code, text="")
            assert send_webhook(job, event="success") is True

    def test_4xx_returns_false_without_save(self):
        """4xx is a delivery failure but the policy here does NOT auto-retry."""
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=404, text="nope")
            assert send_webhook(job, event="success") is False
            # No 'sent' transition.
            assert job.webhook_status != "sent"

    def test_5xx_returns_false(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=503, text="busy")
            assert send_webhook(job, event="success") is False

    def test_timeout_returns_false(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            # Build a fake `requests.exceptions.Timeout` that satisfies the
            # `except requests.exceptions.Timeout` clause in webhooks.py.
            class _Timeout(Exception):
                pass

            class _ReqExc(Exception):
                pass

            fake_requests.exceptions = SimpleNamespace(
                Timeout=_Timeout, RequestException=_ReqExc
            )
            fake_requests.post.side_effect = _Timeout()
            assert send_webhook(job, event="success") is False

    def test_connection_error_returns_false(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            class _Timeout(Exception):
                pass

            class _ReqExc(Exception):
                pass

            fake_requests.exceptions = SimpleNamespace(
                Timeout=_Timeout, RequestException=_ReqExc
            )
            fake_requests.post.side_effect = _ReqExc("conn refused")
            assert send_webhook(job, event="success") is False

    def test_unexpected_exception_returns_false(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            class _Timeout(Exception):
                pass

            class _ReqExc(Exception):
                pass

            fake_requests.exceptions = SimpleNamespace(
                Timeout=_Timeout, RequestException=_ReqExc
            )
            fake_requests.post.side_effect = RuntimeError("boom")
            assert send_webhook(job, event="success") is False

    def test_requests_unavailable_returns_false(self):
        """If the `requests` import failed at module load, delivery returns False."""
        job = make_job()
        with patch.object(webhooks_mod, "requests", None), patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            assert send_webhook(job, event="success") is False

    def test_no_webhook_url_short_circuits(self):
        job = make_job(webhook_url="")
        with patch.object(webhooks_mod, "requests") as fake_requests:
            assert send_webhook(job, event="success") is True
            fake_requests.post.assert_not_called()


# ---------------------------------------------------------------------------
# TestRetryBackoff
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    def _settings(self, name, default=None):
        return {"WEBHOOK_SECRET": None, "WEBHOOK_TIMEOUT": 5}.get(name, default)

    def test_no_url_short_circuits_with_retry(self):
        job = make_job(webhook_url=None)
        assert send_webhook_with_retry(job, event="success") is True

    def test_retry_increments_counter_on_failure(self):
        job = make_job(webhook_retries=0, webhook_max_retries=3)
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ), patch("time.sleep") as fake_sleep:
            fake_requests.post.return_value = MagicMock(status_code=500, text="err")
            result = send_webhook_with_retry(job, event="success")
            assert result is False
            assert job.webhook_retries == 1
            assert job.webhook_status == "pending"
            # The current implementation does not call time.sleep inline; we
            # patch it defensively so any future inline backoff is captured
            # without actually sleeping in the test.
            assert fake_sleep.called is False or all(
                args[0][0] >= 0 for args in fake_sleep.call_args_list
            )

    def test_retry_caps_at_max(self):
        """When retries == max, status flips to 'failed' and we stop."""
        job = make_job(webhook_retries=2, webhook_max_retries=3)
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ), patch("time.sleep"):
            fake_requests.post.return_value = MagicMock(status_code=500, text="err")
            result = send_webhook_with_retry(job, event="success")
            assert result is False
            assert job.webhook_retries == 3
            assert job.webhook_status == "failed"

    def test_first_attempt_marks_pending(self):
        job = make_job(webhook_retries=0, webhook_max_retries=3)
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            assert send_webhook_with_retry(job, event="success") is True
            # First call into the function marks status=pending before delivery.
            statuses = [c.kwargs.get("update_fields") for c in job.save.call_args_list]
            assert ["webhook_status"] in statuses

    def test_success_short_circuits_retry_path(self):
        job = make_job(webhook_retries=1, webhook_max_retries=3)
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            assert send_webhook_with_retry(job, event="success") is True
            # On success, the retry counter MUST NOT advance.
            assert job.webhook_retries == 1


# ---------------------------------------------------------------------------
# TestEventFiltering
# ---------------------------------------------------------------------------


class TestEventFiltering:
    def _settings(self, name, default=None):
        return {"WEBHOOK_SECRET": None, "WEBHOOK_TIMEOUT": 5}.get(name, default)

    def test_event_not_subscribed_is_skipped(self):
        job = make_job(webhook_events=["success"])  # not subscribed to 'failure'
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            assert send_webhook(job, event="failure") is True
            fake_requests.post.assert_not_called()

    def test_event_subscribed_triggers_post(self):
        job = make_job(webhook_events=["success", "failure"])
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            assert send_webhook(job, event="failure") is True
            fake_requests.post.assert_called_once()

    def test_empty_events_list_skips_all(self):
        job = make_job(webhook_events=[])
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            assert send_webhook(job, event="success") is True
            fake_requests.post.assert_not_called()


# ---------------------------------------------------------------------------
# TestPayloadShape
# ---------------------------------------------------------------------------


class TestPayloadShape:
    EXPECTED_FIELDS = {
        "event",
        "job_id",
        "task_path",
        "status",
        "queue_name",
        "priority",
        "created_at",
        "started_at",
        "finished_at",
        "duration_seconds",
        "output",
        "error",
        "retry_count",
        "tags",
    }

    def _settings(self, name, default=None):
        return {"WEBHOOK_SECRET": None, "WEBHOOK_TIMEOUT": 5}.get(name, default)

    def test_payload_contains_documented_fields(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            send_webhook(job, event="success")
            _, kwargs = fake_requests.post.call_args
            body = json.loads(kwargs["data"])
            assert self.EXPECTED_FIELDS.issubset(body.keys())
            assert body["event"] == "success"
            assert body["job_id"] == 42
            assert body["status"] == "success"

    def test_payload_handles_none_timestamps(self):
        job = make_job(started_at=None, finished_at=None)
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            send_webhook(job, event="success")
            _, kwargs = fake_requests.post.call_args
            body = json.loads(kwargs["data"])
            assert body["started_at"] is None
            assert body["finished_at"] is None

    def test_user_agent_header_set(self):
        job = make_job()
        with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
            send_webhook(job, event="success")
            _, kwargs = fake_requests.post.call_args
            assert kwargs["headers"]["User-Agent"].startswith("Sqlery-Webhooks/")
            assert kwargs["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# SSRF handoff (SEC-02, Phase 4)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestSafeEncoder — JSON encoder branches
# ---------------------------------------------------------------------------


class TestSafeEncoder:
    """Cover `_SafeEncoder.default` for every supported type."""

    def _encode(self, value):
        return json.dumps(value, cls=webhooks_mod._SafeEncoder)

    def test_encodes_uuid(self):
        import uuid

        u = uuid.uuid4()
        assert str(u) in self._encode({"u": u})

    def test_encodes_datetime_date_time(self):
        from datetime import date, time

        d = datetime(2026, 1, 1, 12, 0, 0)
        assert "2026-01-01T12:00:00" in self._encode({"d": d})
        assert "2026-01-01" in self._encode({"d": date(2026, 1, 1)})
        assert "12:00:00" in self._encode({"t": time(12, 0, 0)})

    def test_encodes_timedelta_as_seconds(self):
        from datetime import timedelta

        assert "60.0" in self._encode({"td": timedelta(seconds=60)})

    def test_encodes_decimal_as_float(self):
        from decimal import Decimal

        body = json.loads(self._encode({"d": Decimal("1.5")}))
        assert body["d"] == 1.5

    def test_encodes_set_and_frozenset(self):
        body = json.loads(self._encode({"s": {1, 2}, "f": frozenset({3})}))
        assert sorted(body["s"]) == [1, 2]
        assert body["f"] == [3]

    def test_encodes_bytes_as_utf8(self):
        body = json.loads(self._encode({"b": b"hello"}))
        assert body["b"] == "hello"

    def test_unsupported_type_falls_through_to_typeerror(self):
        class Weird:
            pass

        with pytest.raises(TypeError):
            self._encode({"x": Weird()})


# ---------------------------------------------------------------------------
# TestRetryFailedWebhooks — batch retry helper
# ---------------------------------------------------------------------------


class TestRetryFailedWebhooks:
    """Exercise the `retry_failed_webhooks` batch loop without a real DB.

    We patch `QueuedJob.objects` at the module level so the function sees a
    deterministic queryset of mock job rows.
    """

    def _settings(self, name, default=None):
        return {"WEBHOOK_SECRET": None, "WEBHOOK_TIMEOUT": 5}.get(name, default)

    def test_empty_queryset_returns_zeros(self):
        from sqlery.webhooks import retry_failed_webhooks

        with patch.object(webhooks_mod, "QueuedJob") as fake_qj:
            fake_qj.objects.filter.return_value = []
            stats = retry_failed_webhooks()
            assert stats == {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    def test_counts_success_failure_skipped(self):
        from sqlery.webhooks import retry_failed_webhooks

        # success: a 200 will go through send_webhook_with_retry -> True
        success_job = make_job(status="success", webhook_url="https://example.test/a")
        # failed-event job: send_webhook returns False -> retry_failed_webhooks counts as 'failed'
        failed_job = make_job(
            status="failed",
            webhook_url="https://example.test/b",
            webhook_events=["failure"],
        )
        # skipped: status not 'success' / 'failed' (e.g. still running)
        skipped_job = make_job(status="running", webhook_url="https://example.test/c")

        with patch.object(webhooks_mod, "QueuedJob") as fake_qj, patch.object(
            webhooks_mod, "requests"
        ) as fake_requests, patch.object(
            webhooks_mod, "get_setting", side_effect=self._settings
        ):
            fake_qj.objects.filter.return_value = [success_job, failed_job, skipped_job]

            # success_job -> 200, failed_job -> 500
            def post(url, **kw):
                if url.endswith("/a"):
                    return MagicMock(status_code=200, text="OK")
                return MagicMock(status_code=500, text="err")

            fake_requests.post.side_effect = post

            stats = retry_failed_webhooks()
            assert stats["total"] == 3
            assert stats["success"] == 1
            assert stats["failed"] == 1
            assert stats["skipped"] == 1


@pytest.mark.xfail(reason="SEC-02 SSRF mitigation deferred to Phase 4", strict=False)
def test_ssrf_blocks_loopback_destinations():
    """Future: refuse to POST to loopback / link-local / private nets.

    Today the module accepts any URL. Marked xfail to flag the hand-off
    rather than silently passing.
    """
    job = make_job(webhook_url="http://127.0.0.1:80/hook")
    with patch.object(webhooks_mod, "requests") as fake_requests, patch.object(
        webhooks_mod, "get_setting", side_effect=lambda n, d=None: d
    ):
        fake_requests.post.return_value = MagicMock(status_code=200, text="OK")
        assert send_webhook(job, event="success") is False
        fake_requests.post.assert_not_called()
