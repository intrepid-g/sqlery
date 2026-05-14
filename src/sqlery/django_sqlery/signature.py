# #CLEANUP: 2026-05-14 — moved to sqlery.core.signature. Remove after 2026-11-14.
"""Dated stub. The real implementation lives in :mod:`sqlery.core.signature`.

This file existed pre-Phase-2 and is kept as a re-export so existing callers
in this package and in user code continue working through a deprecation
window (Phase 1 stub-don't-delete policy).
"""

from sqlery.core.signature import (  # noqa: F401
    generate_signature,
    make_signed_request_headers,
    verify_signature,
)
