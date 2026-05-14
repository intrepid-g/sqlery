"""Mode-agnostic Lambda dispatch helper (DMOD-04 / SMOD-04).

Lifted out of :mod:`sqlery.lambda_handler` (Django-only) so the standalone
Lambda handler (:mod:`sqlery.fastapi_sqlery.lambda_handler`) can call the
same claim+execute logic without importing Django.

Per CONTEXT decision E this is **smoke-only** — no LocalStack, no SAM, no
moto. The function takes a parsed EventBridge-style event dict and a
configured ``DatabaseBackend`` and runs one synchronous claim+execute pass,
returning a structured result dict.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def process_event(event: dict, backend) -> dict:
    """Dispatch a single Lambda invocation.

    Supported actions (``event['action']``):
      - ``process_queue``: claim one job from the given queue (or by job_id)
        and execute it synchronously. Returns the DB-row status after
        execution (lifecycle assertion target — RESEARCH §E / CONTEXT E).
      - ``process_scheduled`` / ``poll_and_process``: run the scheduler
        once to enqueue any due tasks, then claim+execute up to one
        queued job.

    Args:
        event: EventBridge-style payload. Required keys: ``action``.
            Optional: ``queue_name`` (default ``"default"``), ``job_id``.
        backend: A :class:`sqlery.compat.DatabaseBackend`-conforming
            object. The caller is responsible for bringing it up.

    Returns:
        ``{'processed': [...], 'failed': [...], 'job_ids': [...]}`` —
        always lists, never tuples. Tests assert DB-row lifecycle, not
        this return value (per PLAN-CHECKER-FIXES B1).
    """
    from sqlery.core.worker import JobExecutor

    action = event.get("action", "process_queue")
    queue_name = event.get("queue_name") or "default"
    job_id = event.get("job_id")
    processed: list[int] = []
    failed: list[int] = []
    job_ids: list[int] = []

    if action in ("process_queue", "poll_and_process"):
        job: Any = None
        if job_id is not None:
            try:
                job = backend.get_job_by_id(int(job_id))
            except Exception:
                logger.exception("lambda_core: get_job_by_id failed")
        else:
            worker_id = f"lambda-{uuid.uuid4()}"
            try:
                job = backend.claim_job([queue_name], worker_id)
            except Exception:
                logger.exception("lambda_core: claim_job failed")

        if job is not None:
            job_ids.append(job.id)
            try:
                JobExecutor(backend=backend).execute_job(job)
                processed.append(job.id)
            except Exception:
                logger.exception("lambda_core: execute_job failed")
                failed.append(job.id)

    elif action == "process_scheduled":
        # Best-effort scheduler tick; absence of a Scheduler in standalone
        # mode is non-fatal.
        try:
            from sqlery.core.scheduler import Scheduler
            sched = Scheduler()
            if hasattr(sched, "run_once"):
                enqueued = sched.run_once() or []
                for j in enqueued:
                    job_ids.append(getattr(j, "id", j))
        except Exception:
            logger.exception("lambda_core: scheduler run_once failed")

    else:
        logger.warning(f"lambda_core: unknown action {action!r}")

    return {"processed": processed, "failed": failed, "job_ids": job_ids}


__all__ = ["process_event"]
