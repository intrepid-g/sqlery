"""Standalone AWS Lambda handler (SMOD-04).

This module is the no-Django Lambda entry point for sqlery jobs running on
the standalone (SQLAlchemy + FastAPI) integration. The Django twin lives at
:mod:`sqlery.lambda_handler` and is unchanged.

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


def handler(event: dict, context) -> dict:
    """AWS Lambda entry point for the standalone (no-Django) backend.

    NOTE: this module deliberately has zero Django coupling. The
    automated PLAN.md check greps for Django-import patterns and must
    return empty against this file.
    """
    from sqlery.compat import initialize, get_backend, is_django_mode
    from sqlery.core.lambda_core import process_event

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
