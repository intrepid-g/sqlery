"""Unit tests for ``sqlery.security.ssrf``.

Covers every denylisted range, the hostname denylist, scheme rejection,
DNS-rebinding defense (any-match denial across multiple resolved addresses),
``allow_loopback`` opt-in, and a happy-path public URL.

All DNS resolution is monkeypatched — no real network access in this file.
"""

from __future__ import annotations

import logging
import socket
from unittest.mock import MagicMock

import pytest

from sqlery.security.ssrf import (
    BLOCKED_HOSTNAMES,
    BLOCKED_V4_NETS,
    BLOCKED_V6_NETS,
    WebhookURLBlocked,
    validate_webhook_url,
)


# --- Helpers ---------------------------------------------------------------

def _addrinfo_v4(ip: str) -> tuple:
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))


def _addrinfo_v6(ip: str) -> tuple:
    return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))


@pytest.fixture
def public_resolver(monkeypatch):
    """getaddrinfo returns a single benign public IPv4 (1.2.3.4)."""
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **kw: [_addrinfo_v4("1.2.3.4")]
    )


# --- Subclass relationship -------------------------------------------------

def test_webhook_url_blocked_is_value_error():
    assert issubclass(WebhookURLBlocked, ValueError)


# --- Scheme rejection ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://10.0.0.1/x",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "ldap://internal/x",
])
def test_blocks_non_http_schemes(url):
    with pytest.raises(WebhookURLBlocked, match="scheme"):
        validate_webhook_url(url)


def test_missing_host_rejected():
    with pytest.raises(WebhookURLBlocked, match="missing host"):
        validate_webhook_url("http:///path")


# --- Hostname denylist (pre-DNS) ------------------------------------------

@pytest.mark.parametrize("host", [
    "localhost",
    "LOCALHOST",
    "metadata.google.internal",
    "Metadata.Google.Internal",
    "metadata",
])
def test_hostname_denylist(host):
    with pytest.raises(WebhookURLBlocked, match="hostname blocked"):
        validate_webhook_url(f"http://{host}/x")


def test_all_documented_hostnames_in_denylist():
    # Sanity: contract with callers about which hostnames are pre-DNS blocked.
    assert "localhost" in BLOCKED_HOSTNAMES
    assert "metadata.google.internal" in BLOCKED_HOSTNAMES


# --- Literal IPv4 denylist -------------------------------------------------

@pytest.mark.parametrize("ip,label", [
    ("127.0.0.1", "loopback"),
    ("127.255.255.254", "loopback range"),
    ("10.0.0.1", "rfc1918 10/8"),
    ("10.255.255.1", "rfc1918 10/8 high"),
    ("172.16.0.1", "rfc1918 172.16/12"),
    ("172.31.255.1", "rfc1918 172.16/12 high"),
    ("192.168.1.1", "rfc1918 192.168/16"),
    ("169.254.169.254", "AWS metadata / link-local"),
    ("169.254.0.1", "link-local generic"),
    ("0.0.0.0", "unspecified"),
    ("0.1.2.3", "unspecified range"),
    ("100.64.0.1", "CGNAT"),
    ("100.127.255.1", "CGNAT high"),
])
def test_blocks_literal_ipv4(ip, label):
    with pytest.raises(WebhookURLBlocked, match="ip blocked"):
        validate_webhook_url(f"http://{ip}/x")


# --- Literal IPv6 denylist -------------------------------------------------

@pytest.mark.parametrize("ip,label", [
    ("::1", "v6 loopback"),
    ("::", "v6 unspecified"),
    ("fe80::1", "v6 link-local"),
    ("fe80::dead:beef", "v6 link-local generic"),
    ("fc00::1", "v6 ULA"),
    ("fd00:ec2::254", "AWS v6 metadata (ULA)"),
])
def test_blocks_literal_ipv6(ip, label):
    with pytest.raises(WebhookURLBlocked, match="ip blocked"):
        validate_webhook_url(f"http://[{ip}]/x")


# --- DNS rebinding defense (any-match denial) -----------------------------

def test_dns_rebinding_mixed_public_then_private(monkeypatch):
    """getaddrinfo returns a public IP AND a private IP → reject."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **kw: [_addrinfo_v4("1.2.3.4"), _addrinfo_v4("10.0.0.5")],
    )
    with pytest.raises(WebhookURLBlocked, match="resolved address blocked"):
        validate_webhook_url("http://attacker.example/x")


def test_dns_rebinding_mixed_private_then_public(monkeypatch):
    """Reject order-independence: private first still trips."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **kw: [_addrinfo_v4("10.0.0.5"), _addrinfo_v4("1.2.3.4")],
    )
    with pytest.raises(WebhookURLBlocked, match="resolved address blocked"):
        validate_webhook_url("http://attacker.example/x")


def test_dns_rebinding_mixed_v4_v6(monkeypatch):
    """One public v4 + one private v6 → reject."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **kw: [_addrinfo_v4("1.2.3.4"), _addrinfo_v6("fc00::1")],
    )
    with pytest.raises(WebhookURLBlocked, match="resolved address blocked"):
        validate_webhook_url("http://attacker.example/x")


def test_dns_resolution_failure_rejected(monkeypatch):
    def boom(*a, **kw):
        raise socket.gaierror("NXDOMAIN")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(WebhookURLBlocked, match="dns failure"):
        validate_webhook_url("http://does-not-exist.invalid/x")


# --- Happy path ------------------------------------------------------------

def test_public_url_succeeds(public_resolver):
    # Returns None on success.
    assert validate_webhook_url("https://hooks.example.com/x") is None


def test_public_url_with_port_succeeds(public_resolver):
    assert validate_webhook_url("https://hooks.example.com:8443/x") is None


def test_literal_public_ipv4_succeeds():
    # No DNS; raw public literal.
    assert validate_webhook_url("https://8.8.8.8/x") is None


# --- allow_loopback opt-in -------------------------------------------------

def test_allow_loopback_permits_127_literal():
    assert validate_webhook_url("http://127.0.0.1/x", allow_loopback=True) is None


def test_allow_loopback_permits_v6_loopback_literal():
    assert validate_webhook_url("http://[::1]/x", allow_loopback=True) is None


def test_allow_loopback_still_blocks_rfc1918():
    with pytest.raises(WebhookURLBlocked):
        validate_webhook_url("http://10.0.0.1/x", allow_loopback=True)


def test_allow_loopback_still_blocks_metadata_hostname():
    with pytest.raises(WebhookURLBlocked):
        validate_webhook_url(
            "http://metadata.google.internal/x", allow_loopback=True
        )


def test_allow_loopback_still_blocks_link_local_metadata():
    with pytest.raises(WebhookURLBlocked):
        validate_webhook_url("http://169.254.169.254/x", allow_loopback=True)


# --- Coverage sanity: every constant network exercised --------------------

def test_every_v4_net_has_a_test_case():
    # Defensive: if someone adds a new BLOCKED_V4_NETS entry, force a test.
    expected = {
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "0.0.0.0/8", "100.64.0.0/10",
    }
    assert {str(n) for n in BLOCKED_V4_NETS} == expected


def test_every_v6_net_has_a_test_case():
    expected = {"::1/128", "::/128", "fe80::/10", "fc00::/7"}
    assert {str(n) for n in BLOCKED_V6_NETS} == expected


# --- Task 2: send_webhook integration --------------------------------------

def test_send_webhook_blocks_loopback_url_returns_false(caplog):
    """send_webhook must catch WebhookURLBlocked, log WARNING, return False."""
    from sqlery import webhooks

    job = MagicMock()
    job.id = 1
    job.webhook_url = "http://127.0.0.1/notify"
    job.webhook_events = ["success"]

    caplog.set_level(logging.WARNING, logger=webhooks.logger.name)
    result = webhooks.send_webhook(job, event="success")

    assert result is False
    assert any(
        "blocked by SSRF policy" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), f"Expected SSRF block WARNING, got: {[r.message for r in caplog.records]}"


def test_send_webhook_blocks_metadata_url_returns_false():
    from sqlery import webhooks

    job = MagicMock()
    job.id = 2
    job.webhook_url = "http://169.254.169.254/latest/meta-data/"
    job.webhook_events = ["success"]

    assert webhooks.send_webhook(job, event="success") is False


def test_send_webhook_no_url_short_circuits_true():
    """Unrelated short-circuit must still work — SSRF check skipped when no URL."""
    from sqlery import webhooks

    job = MagicMock()
    job.id = 3
    job.webhook_url = ""
    job.webhook_events = ["success"]

    assert webhooks.send_webhook(job, event="success") is True
