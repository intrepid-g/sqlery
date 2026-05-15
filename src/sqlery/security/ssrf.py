"""SSRF defense for outbound webhook deliveries.

Implements SEC-02: block webhook URLs that resolve into private, link-local,
loopback, CGNAT, unspecified, or cloud-metadata IP ranges.

Pattern: resolve-then-check. We call ``socket.getaddrinfo`` to obtain ALL IP
families/addresses the host resolves to, then check each against the
denylists. Any single private match rejects the URL.

Known v1 limitation: there is a ~50ms DNS-rebinding window between our
validation resolution and the underlying HTTP client's own resolution. A
malicious authoritative resolver can flip the answer in between. v2 hardening
(pinning the resolved IP via a custom ``HTTPAdapter`` that connects to the
already-resolved address) is out of scope.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# --- Concrete denylist constants (per 04-RESEARCH) -------------------------

BLOCKED_V4_NETS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),    # link-local (incl. AWS metadata)
    ipaddress.ip_network("0.0.0.0/8"),         # unspecified / "this network"
    ipaddress.ip_network("100.64.0.0/10"),     # CGNAT (RFC 6598)
)

BLOCKED_V6_NETS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("::1/128"),           # loopback
    ipaddress.ip_network("::/128"),            # unspecified
    ipaddress.ip_network("fe80::/10"),         # link-local
    ipaddress.ip_network("fc00::/7"),          # ULA (incl. fd00:ec2::254 AWS v6 metadata)
)

# Hostnames blocked before DNS resolution (case-insensitive).
BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",  # GCP short alias
})

# Loopback nets that ``allow_loopback=True`` permits.
_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
_LOOPBACK_V6 = ipaddress.ip_network("::1/128")


class WebhookURLBlocked(ValueError):
    """Raised when a webhook URL fails SSRF validation.

    Subclass of ``ValueError`` so existing broad ``except ValueError`` clauses
    (and the webhook caller's generic ``except Exception``) catch it cleanly.
    """


def _ip_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_loopback: bool,
) -> bool:
    """Return True if ``ip`` is in any blocked range.

    Honors ``allow_loopback`` to skip loopback ranges only; all other
    denylists remain in force.
    """
    if isinstance(ip, ipaddress.IPv4Address):
        for net in BLOCKED_V4_NETS:
            if ip in net:
                if allow_loopback and net == _LOOPBACK_V4:
                    continue
                return True
        return False
    else:
        for net in BLOCKED_V6_NETS:
            if ip in net:
                if allow_loopback and net == _LOOPBACK_V6:
                    continue
                return True
        return False


def validate_webhook_url(url: str, *, allow_loopback: bool = False) -> None:
    """Validate a webhook URL against the SSRF denylist.

    Raises :class:`WebhookURLBlocked` if the URL targets a forbidden host or
    resolves (entirely or partially) into a denylisted IP range.

    Args:
        url: The webhook URL to validate.
        allow_loopback: If True, permit ``127.0.0.0/8`` and ``::1`` (useful
            for local development). All other denylists still enforced.
    """
    parsed = urlparse(url)

    # 1. Scheme allowlist — blocks file://, gopher://, ftp://, etc.
    if parsed.scheme not in ("http", "https"):
        raise WebhookURLBlocked(f"scheme not allowed: {parsed.scheme!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise WebhookURLBlocked("missing host")

    # 2. Hostname denylist (pre-DNS).
    if host in BLOCKED_HOSTNAMES:
        raise WebhookURLBlocked(f"hostname blocked: {host}")

    # 3. Literal IP? Check directly.
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _ip_blocked(literal_ip, allow_loopback=allow_loopback):
            raise WebhookURLBlocked(f"ip blocked: {literal_ip}")
        return

    # 4. Resolve and check ALL returned addresses (defends against single-
    #    resolution DNS rebinding by rejecting if ANY answer is private).
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise WebhookURLBlocked(f"dns failure: {host}: {e}") from e

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # Unparseable address from getaddrinfo — refuse rather than guess.
            raise WebhookURLBlocked(f"unparseable resolved address: {ip_str}")
        if _ip_blocked(ip, allow_loopback=allow_loopback):
            raise WebhookURLBlocked(
                f"resolved address blocked: {host} -> {ip}"
            )
