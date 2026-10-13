"""Framework-agnostic HMAC signature helpers (SMOD-03 / Phase 2 D).

Moved from :mod:`sqlery.django_sqlery.signature` per CONTEXT decision D —
the original file had no Django imports (it's pure ``hmac`` / ``hashlib``)
so the move is a pure relocation. The old path keeps a dated stub that
re-exports everything (Phase 1 stub-don't-delete policy).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)


def _message(timestamp_str: str, body: bytes | None) -> bytes:
    """Bytes covered by the HMAC.

    Legacy (``body is None``): timestamp only. Version 2: ``"<ts>.<sha256(body)>"`` so a
    captured signature cannot be replayed with a different payload.
    """
    if body is None:
        return timestamp_str.encode()
    return f"{timestamp_str}.{hashlib.sha256(body).hexdigest()}".encode()


def generate_signature(
    secret: str, timestamp: int | None = None, body: bytes | None = None
) -> tuple[str, str]:
    """Generate HMAC-SHA256 signature for internal request authentication.

    Args:
        secret: Shared secret.
        timestamp: Optional fixed timestamp (defaults to now).
        body: Request body to bind into the signature (v2). ``None`` keeps the legacy
            timestamp-only signature.

    Returns:
        Tuple of (signature, timestamp_str).
    """
    if timestamp is None:
        timestamp = int(time.time())
    timestamp_str = str(timestamp)
    message = _message(timestamp_str, body)
    signature = base64.b64encode(
        hmac.new(secret.encode(), message, hashlib.sha256).digest()
    ).decode()
    return signature, timestamp_str


def verify_signature(
    signature: str,
    timestamp_str: str,
    secret: str,
    max_age: int = 5,
    body: bytes | None = None,
) -> bool:
    """Verify HMAC signature and timestamp freshness (constant-time compare).

    Pass ``body`` to verify a v2 (body-bound) signature.
    """
    try:
        timestamp = int(timestamp_str)
        age = abs(time.time() - timestamp)
        if age > max_age:
            logger.warning(f"Signature expired: age={age}s, max_age={max_age}s")
            return False
        message = _message(timestamp_str, body)
        expected = base64.b64encode(
            hmac.new(secret.encode(), message, hashlib.sha256).digest()
        ).decode()
        is_valid = hmac.compare_digest(expected, signature)
        if not is_valid:
            logger.warning("Invalid signature received")
        return is_valid
    except (ValueError, TypeError) as e:
        logger.warning(f"Signature verification error: {e}")
        return False


def make_signed_request_headers(secret: str, body: bytes | None = None) -> dict[str, str]:
    """Build (X-Signature, X-Timestamp) header pair for an outbound request.

    With ``body`` the signature is body-bound and ``X-Signature-Version: 2`` is added.
    """
    signature, timestamp = generate_signature(secret, body=body)
    headers = {"X-Signature": signature, "X-Timestamp": timestamp}
    if body is not None:
        headers["X-Signature-Version"] = "2"
    return headers


__all__ = ["generate_signature", "verify_signature", "make_signed_request_headers"]
