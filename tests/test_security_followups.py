"""IP-pinned webhook delivery, log redaction, optional Lambda event signature."""

import http.server
import logging
import socket
import threading
from unittest.mock import patch

import pytest

from sqlery.core.lambda_core import process_event, sign_event, verify_event_auth
from sqlery.core.utils import describe_task_args
from sqlery.security.ssrf import (
    ResolvedTarget,
    WebhookURLBlocked,
    pin_url,
    resolve_webhook_url,
)


# ---- helpers ---------------------------------------------------------------------------
@pytest.fixture
def server():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append({"path": self.path, "host": self.headers.get("Host"),
                         "body": self.rfile.read(n)})
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], seen
    srv.shutdown()


@pytest.fixture
def no_proxy(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")


# ---- SSRF pinning ----------------------------------------------------------------------
def test_request_goes_to_validated_ip_with_original_host_and_no_dns(server, no_proxy):
    from sqlery.django_sqlery import webhooks

    port, seen = server
    target = ResolvedTarget("http", "hook.invalid", port, "127.0.0.1")
    queried = []
    real = socket.getaddrinfo

    def spy(host, *a, **k):
        queried.append(host)
        return real(host, *a, **k)

    with patch("socket.getaddrinfo", spy):
        r = webhooks._post_pinned(url=f"http://hook.invalid:{port}/x", target=target,
                                  body='{"a":1}', headers={"Content-Type": "application/json"},
                                  timeout=5)
    assert r.status_code == 200
    assert seen[0]["host"] == f"hook.invalid:{port}" and seen[0]["body"] == b'{"a":1}'
    assert "hook.invalid" not in queried, "client re-resolved the hostname (rebinding window)"


def test_redirects_are_not_followed(server, no_proxy):
    from sqlery.django_sqlery import webhooks

    port, seen = server
    target = ResolvedTarget("http", "hook.invalid", port, "127.0.0.1")
    r = webhooks._post_pinned(url=f"http://hook.invalid:{port}/redirect", target=target,
                              body="{}", headers={}, timeout=5)
    assert r.status_code == 302 and len(seen) == 1


def test_https_sets_sni_to_original_hostname():
    from sqlery.django_sqlery import webhooks

    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured["client"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            captured.update(url=url, **kw)

    with patch.object(webhooks.httpx, "Client", FakeClient):
        webhooks._post_pinned(url="https://hooks.example.com/p?q=1",
                              target=ResolvedTarget("https", "hooks.example.com", None, "93.184.216.34"),
                              body="{}", headers={"X": "1"}, timeout=3)
    assert captured["url"] == "https://93.184.216.34/p?q=1"
    assert captured["extensions"] == {"sni_hostname": "hooks.example.com"}
    assert captured["headers"]["Host"] == "hooks.example.com"
    assert captured["client"]["follow_redirects"] is False


def test_pin_url_handles_ipv6_port_and_userinfo():
    t = ResolvedTarget("https", "h.example", 8443, "2001:db8::1")
    assert pin_url("https://u:p@h.example:8443/a?b=1", t) == (
        "https://u:p@[2001:db8::1]:8443/a?b=1", "h.example:8443")


def test_resolve_returns_a_public_address_and_rejects_any_private(monkeypatch):
    def fake(host, port, *a, **k):
        return [(2, 1, 6, "", (ip, 0)) for ip in fake.ips]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    fake.ips = ["93.184.216.34", "93.184.216.35"]
    t = resolve_webhook_url("https://hooks.example.com/x")
    assert t.ip == "93.184.216.34" and t.host == "hooks.example.com" and t.scheme == "https"
    fake.ips = ["93.184.216.34", "10.0.0.5"]  # mixed answers: reject
    with pytest.raises(WebhookURLBlocked):
        resolve_webhook_url("https://hooks.example.com/x")
    for bad in ("http://localhost/x", "http://169.254.169.254/", "ftp://example.com/x",
                "https://hooks.example.com:notaport/x"):
        with pytest.raises(WebhookURLBlocked):
            resolve_webhook_url(bad)


# ---- log redaction ---------------------------------------------------------------------
def test_describe_task_args_hides_values_by_default(monkeypatch):
    assert "hunter2" not in describe_task_args(("hunter2",), {"token": "hunter2"})
    assert "token" in describe_task_args((), {"token": "hunter2"})
    import sqlery.compat as compat

    monkeypatch.setattr(compat, "get_config", lambda n, d=None: True if n == "LOG_TASK_ARGUMENTS" else d)
    assert "hunter2" in describe_task_args((), {"token": "hunter2"})


def test_executor_does_not_log_secret_kwargs(tmp_path, caplog):
    from sqlery.core.job import job
    from sqlery.core.worker import JobExecutor
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'l.db'}")
    b = SQLAlchemyBackend()
    j = b.create_job(task_path="tests.test_security_followups.uses_secret",
                     kwargs={"api_key": "s3cr3t-value"}, queue_name="default", priority=0,
                     scheduled_at=None, max_retries=0, retry_backoff=1.0, allow_parallel=False,
                     timeout_seconds=None)
    with caplog.at_level(logging.DEBUG):
        JobExecutor(backend=b).execute_job(b.claim_job_by_id(j.id, "w"))
    assert "s3cr3t-value" not in caplog.text
    assert b.get_job_by_id(j.id).status == "success"


def uses_secret(api_key):
    return len(api_key)


# ---- Lambda event signature ------------------------------------------------------------
class _FakeBackend:
    def claim_job(self, *a, **k):
        return None

    def claim_job_by_id(self, *a, **k):
        return None


def test_lambda_auth_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SQLERY_LAMBDA_SECRET", raising=False)
    assert verify_event_auth({"action": "process_queue"}) is None


def test_lambda_auth_rejects_unsigned_tampered_and_stale_events(monkeypatch):
    monkeypatch.setenv("SQLERY_LAMBDA_SECRET", "s3cret")
    event = {"action": "process_queue", "queue_name": "default"}
    assert process_event(event, _FakeBackend())["statusCode"] == 403          # unsigned

    signed = sign_event(event, "s3cret")
    ok = process_event(signed, _FakeBackend())
    assert ok.get("statusCode") != 403 and ok["processed"] == []              # accepted

    tampered = {**signed, "queue_name": "billing"}
    assert verify_event_auth(tampered) == "invalid event signature"
    assert verify_event_auth(sign_event(event, "wrong-secret")) == "invalid event signature"

    stale = sign_event(event, "s3cret")
    stale["auth"]["timestamp"] = str(int(stale["auth"]["timestamp"]) - 3600)
    assert verify_event_auth(stale) == "invalid event signature"
