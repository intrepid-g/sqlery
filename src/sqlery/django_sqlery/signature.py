"""HMAC signature utilities for internal worker authentication."""

import hmac
import hashlib
import base64
import time
import logging

logger = logging.getLogger(__name__)


def generate_signature(secret: str, timestamp: int | None = None) -> tuple[str, str]:
    """Generate HMAC-SHA256 signature for internal request authentication.

    Args:
        secret: Shared secret key
        timestamp: Unix timestamp (defaults to current time)

    Returns:
        Tuple of (signature, timestamp_str)
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
    """Verify HMAC signature for internal request.

    Args:
        signature: Base64-encoded signature from request
        timestamp_str: Timestamp string from request
        secret: Shared secret key
        max_age: Maximum age of signature in seconds (default: 5)

    Returns:
        True if signature is valid and fresh
    """
    try:
        # Check timestamp freshness
        timestamp = int(timestamp_str)
        age = abs(time.time() - timestamp)

        if age > max_age:
            logger.warning(f"Signature expired: age={age}s, max_age={max_age}s")
            return False

        # Generate expected signature
        message = timestamp_str.encode()
        expected = base64.b64encode(
            hmac.new(secret.encode(), message, hashlib.sha256).digest()
        ).decode()

        # Constant-time comparison to prevent timing attacks
        is_valid = hmac.compare_digest(expected, signature)

        if not is_valid:
            logger.warning("Invalid signature received")

        return is_valid

    except (ValueError, TypeError) as e:
        logger.warning(f"Signature verification error: {e}")
        return False


def make_signed_request_headers(secret: str) -> dict[str, str]:
    """Create headers for signed internal request.

    Args:
        secret: Shared secret key

    Returns:
        Dictionary with X-Signature and X-Timestamp headers
    """
    signature, timestamp = generate_signature(secret)

    return {
        "X-Signature": signature,
        "X-Timestamp": timestamp,
    }
