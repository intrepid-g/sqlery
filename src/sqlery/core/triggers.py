"""Pure-function HTTP trigger receiver (SMOD-03 / CONTEXT decision D).

This module is the **framework-agnostic** HTTP trigger surface for sqlery.
Both the Django view (``sqlery.django_sqlery.views.internal_worker`` /
``trigger_view``) and the FastAPI route
(``sqlery.fastapi_sqlery.triggers.router``) parse their respective requests
into a :class:`TriggerEnvelope`, call :func:`handle`, and translate the
:class:`TriggerResult` back to a framework-native response.

Distinct from the top-level :mod:`sqlery.triggers` strategy module (147
lines, subprocess/django-tasks/thread dispatch) — per CONTEXT open question
3, the top-level file stays live and unchanged. This file is the *receiver*
side, not the dispatcher; once :func:`handle` decides what to do, it
delegates to the same JobExecutor / strategy helpers the rest of the
codebase uses.

Envelope shape (per PLAN <interfaces>):

    @dataclass class TriggerEnvelope:
        body: bytes
        headers: dict[str, str]   # X-Signature, X-Timestamp
        payload: dict             # {'action', 'queue_name'?, 'job_id'?, 'task_id'?}

Result shape:

    @dataclass class TriggerResult:
        status_code: int
        body: dict
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from sqlery.core.signature import verify_signature

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Envelope / Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TriggerEnvelope:
    """Framework-agnostic carrier for an incoming HTTP trigger request."""

    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    payload: dict = field(default_factory=dict)


@dataclass
class TriggerResult:
    """Framework-agnostic response from :func:`handle`."""

    status_code: int = 200
    body: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Idempotency cache (LRU, in-memory)
# ---------------------------------------------------------------------------

# An in-memory LRU keyed by ``(timestamp, signature)``. Flagged as a future
# replacement: a distributed deployment will need a shared cache backend
# (Redis, DB). For SMOD-03's smoke-level guarantees the in-memory map is
# sufficient — duplicate hits within the 5-second signature window resolve
# to a 409 instead of a double-claim.
_IDEMPOTENCY_TTL_SECONDS = 30
_IDEMPOTENCY_MAX_ENTRIES = 1024
_idempotency_cache: "OrderedDict[tuple[str, str], float]" = OrderedDict()


def _idempotency_seen(key: tuple[str, str]) -> bool:
    """Check + record a request fingerprint; True if already seen."""
    now = time.time()
    # Evict expired entries.
    expired = [k for k, ts in _idempotency_cache.items() if now - ts > _IDEMPOTENCY_TTL_SECONDS]
    for k in expired:
        _idempotency_cache.pop(k, None)
    if key in _idempotency_cache:
        return True
    _idempotency_cache[key] = now
    while len(_idempotency_cache) > _IDEMPOTENCY_MAX_ENTRIES:
        _idempotency_cache.popitem(last=False)
    return False


def _reset_idempotency_cache() -> None:
    """Test-only helper; clears the in-memory cache between cases."""
    _idempotency_cache.clear()


# ---------------------------------------------------------------------------
# Secret resolution (delegated to backend config / env)
# ---------------------------------------------------------------------------


def _resolve_secret() -> str | None:
    """Read the shared HMAC secret from compat config (Django settings or
    standalone env)."""
    try:
        from sqlery.compat import get_config
        secret = get_config("INTERNAL_SECRET", None)
    except Exception:
        secret = None
    if secret is None:
        import os
        secret = os.environ.get("SQLERY_INTERNAL_SECRET")
    return secret


def _resolve_max_age() -> int:
    try:
        from sqlery.compat import get_config
        return int(get_config("SIGNATURE_MAX_AGE", 5))
    except Exception:
        return 5


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def handle(envelope: TriggerEnvelope) -> TriggerResult:
    """Process one HTTP trigger envelope.

    Steps:
    1. Verify the HMAC signature (X-Signature, X-Timestamp) against the
       configured ``INTERNAL_SECRET``. Bad/missing signature → 401.
    2. Check the idempotency cache; duplicate hit within the signature
       window → 409.
    3. Dispatch on ``payload['action']``:
       - ``process_queue`` → claim and execute one job from the named queue
         (or the requested job_id), synchronously in-process.
       - ``process_scheduled`` → run the scheduler to enqueue due tasks.
       Unknown action → 400.

    Returns:
        :class:`TriggerResult` with the appropriate status_code and body.
    """
    signature = envelope.headers.get("X-Signature") or envelope.headers.get("x-signature")
    timestamp = envelope.headers.get("X-Timestamp") or envelope.headers.get("x-timestamp")
    if not signature or not timestamp:
        return TriggerResult(401, {"error": "missing signature headers"})

    secret = _resolve_secret()
    if not secret:
        logger.error("INTERNAL_SECRET not configured for HTTP trigger")
        return TriggerResult(500, {"error": "server misconfigured"})

    if not verify_signature(signature, timestamp, secret, max_age=_resolve_max_age()):
        return TriggerResult(401, {"error": "invalid signature"})

    if _idempotency_seen((timestamp, signature)):
        return TriggerResult(409, {"error": "duplicate request"})

    action = envelope.payload.get("action", "process_queue")

    if action == "process_queue":
        return _dispatch_process_queue(envelope.payload)
    if action == "process_scheduled":
        return _dispatch_process_scheduled(envelope.payload)
    return TriggerResult(400, {"error": f"unknown action: {action!r}"})


# ---------------------------------------------------------------------------
# Action dispatchers
# ---------------------------------------------------------------------------


def _dispatch_process_queue(payload: dict) -> TriggerResult:
    """Claim and execute one job from the requested queue (or by job_id)."""
    from sqlery.compat import get_backend
    from sqlery.core.worker import JobExecutor

    backend = get_backend()

    job_id = payload.get("job_id")
    queue_name = payload.get("queue_name") or "default"

    job: Any = None
    if job_id is not None:
        try:
            job = backend.get_job_by_id(int(job_id))
        except Exception as e:
            return TriggerResult(404, {"error": f"job {job_id} not found: {e}"})
        if job is None:
            return TriggerResult(404, {"error": f"job {job_id} not found"})
    else:
        # Fall back to claim-from-queue.
        import uuid
        worker_id = f"trigger-{uuid.uuid4()}"
        try:
            job = backend.claim_job([queue_name], worker_id)
        except Exception as e:  # pragma: no cover
            logger.exception("claim_job failed inside HTTP trigger")
            return TriggerResult(500, {"error": str(e)})
        if job is None:
            return TriggerResult(200, {"ok": True, "claimed": False, "job_ids": []})

    try:
        JobExecutor(backend=backend).execute_job(job)
    except Exception as e:  # pragma: no cover
        logger.exception("execute_job failed inside HTTP trigger")
        return TriggerResult(500, {"error": str(e)})

    return TriggerResult(200, {"ok": True, "claimed": True, "job_ids": [job.id]})


def _dispatch_process_scheduled(payload: dict) -> TriggerResult:
    """Run the scheduler to enqueue any due scheduled tasks."""
    try:
        from sqlery.core.scheduler import Scheduler
        scheduler = Scheduler()
        enqueued = scheduler.run_once() if hasattr(scheduler, "run_once") else []
    except Exception as e:  # pragma: no cover
        logger.exception("scheduler dispatch failed inside HTTP trigger")
        return TriggerResult(500, {"error": str(e)})

    job_ids: list[int] = []
    if isinstance(enqueued, list):
        job_ids = [getattr(j, "id", j) for j in enqueued]
    return TriggerResult(200, {"ok": True, "job_ids": job_ids})


__all__ = [
    "TriggerEnvelope",
    "TriggerResult",
    "handle",
    "_reset_idempotency_cache",
]
