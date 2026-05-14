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


def generate_signature(secret: str, timestamp: int | None = None) -> tuple[str, str]:
    """Generate HMAC-SHA256 signature for internal request authentication.

    Returns:
        Tuple of (signature, timestamp_str).
    """
    if timestamp is None:
        timestamp = int(time.time())
    timestamp_str = str(timestamp)
    message = timestamp_str.encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), message, hashlib.sha256).digest()
    ).decode()
    return signature, timestamp_str


def verify_signature(
    signature: str, timestamp_str: str, secret: str, max_age: int = 5
) -> bool:
    """Verify HMAC signature and timestamp freshness (constant-time compare)."""
    try:
        timestamp = int(timestamp_str)
        age = abs(time.time() - timestamp)
        if age > max_age:
            logger.warning(f"Signature expired: age={age}s, max_age={max_age}s")
            return False
        message = timestamp_str.encode()
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


def make_signed_request_headers(secret: str) -> dict[str, str]:
    """Build (X-Signature, X-Timestamp) header pair for an outbound request."""
    signature, timestamp = generate_signature(secret)
    return {"X-Signature": signature, "X-Timestamp": timestamp}


__all__ = ["generate_signature", "verify_signature", "make_signed_request_headers"]
