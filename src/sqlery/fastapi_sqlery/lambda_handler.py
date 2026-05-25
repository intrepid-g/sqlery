"""Standalone AWS Lambda handler (SMOD-04).

This module is the no-Django Lambda entry point for sqlery jobs running on
the standalone (SQLAlchemy + FastAPI) integration. The Django twin lives at
:mod:`sqlery.lambda_handler` and is unchanged.

.. warning::
   **EXPERIMENTAL.** The Lambda/serverless execution mode has only been
   smoke-tested. It has never been exercised against a real Lambda-shaped
   runtime (no LocalStack/SAM fidelity testing), so it should NOT be
   considered production-ready. Use at your own risk and validate
   thoroughly in your own environment before relying on it.

Per CONTEXT decision E this is a **smoke-only** path: no LocalStack, no
SAM, no moto. The handler imports nothing from Django, configures the
standalone backend via :func:`sqlery.compat.initialize`, and delegates to
:func:`sqlery.core.lambda_core.process_event`.

Deployment shape:
    Handler: sqlery.fastapi_sqlery.lambda_handler.handler
    Environment: SQLERY_DATABASE_URL must be set
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# One-time guard so the experimental warning fires once per process (per warm
# Lambda container) rather than on every invocation.
_experimental_warning_emitted = False


def handler(event: dict, context) -> dict:
    """AWS Lambda entry point for the standalone (no-Django) backend.

    NOTE: this module deliberately has zero Django coupling. The
    automated PLAN.md check greps for Django-import patterns and must
    return empty against this file.
    """
    from sqlery.compat import initialize, get_backend, is_django_mode
    from sqlery.core.lambda_core import process_event

    global _experimental_warning_emitted
    if not _experimental_warning_emitted:
        logger.warning(
            "sqlery Lambda/serverless mode is EXPERIMENTAL: it has only been "
            "smoke-tested (no LocalStack/SAM fidelity testing) and is not "
            "production-ready."
        )
        _experimental_warning_emitted = True

    if is_django_mode():
        raise RuntimeError(
            "sqlery.fastapi_sqlery.lambda_handler is the standalone Lambda "
            "entry point; you are running in Django mode. Use "
            "sqlery.lambda_handler.handler instead."
        )

    db_url = os.environ.get("SQLERY_DATABASE_URL")
    if not db_url:
        raise RuntimeError("SQLERY_DATABASE_URL environment variable is required")

    # initialize() is idempotent enough for our smoke path: it sets compat
    # config + brings up the SQLAlchemy engine. Repeated invocations within
    # the same warm Lambda container reuse the cached singletons.
    try:
        initialize(database_url=db_url, enable_daemon=False)
    except RuntimeError:
        # Already initialized in this container — fine.
        pass

    backend = get_backend()
    logger.info(f"Standalone lambda invoked with event keys: {sorted(event.keys())}")
    return process_event(event, backend)


__all__ = ["handler"]
