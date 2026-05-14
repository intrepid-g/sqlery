"""Standalone subprocess executor (SMOD-02).

Spawns ``python -c <driver>`` as a subprocess for the standalone
(non-Django) integration mode. The child is scrubbed of
``DJANGO_SETTINGS_MODULE`` so compat-mode detection routes to standalone,
then ``initialize(database_url=...)`` brings up the SQLAlchemy backend and
``JobExecutor`` claims+executes a single job (``one_shot=True``).

Fork safety: the parent SQLAlchemy engine is disposed before ``Popen``
(RESEARCH section 8). SQLite WAL mode tolerates multiple readers but the
parent's open engine can race the child on writes.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


_STANDALONE_WORKER_SCRIPT = """
import os, sys
os.environ.pop('DJANGO_SETTINGS_MODULE', None)
from sqlery.compat import initialize, get_backend
from sqlery.core.worker import JobExecutor, WorkerProcess

database_url = os.environ['SQLERY_STANDALONE_DB_URL']
queues = os.environ.get('SQLERY_STANDALONE_QUEUES', 'default').split(',')
one_shot = os.environ.get('SQLERY_STANDALONE_ONE_SHOT', '1') == '1'

initialize(
    database_url=database_url,
    worker_queues=queues,
    enable_daemon=False,
    max_workers=1,
)
backend = get_backend()

if one_shot:
    import uuid
    worker_id = str(uuid.uuid4())
    job = backend.claim_job(queues, worker_id)
    if job is not None:
        JobExecutor(backend=backend).execute_job(job)
else:
    WorkerProcess(queues=queues).run()
"""


def _dispose_engine_safely() -> None:
    """Drop any engine handles the parent might hold (best-effort)."""
    try:
        from sqlery.fastapi_sqlery import database as _db
        eng = getattr(_db, "_engine", None)
        if eng is not None:
            eng.dispose()
            _db._engine = None  # type: ignore[attr-defined]
    except Exception:
        logger.debug("standalone subprocess: engine dispose skipped", exc_info=True)


def spawn_subprocess_worker(
    database_url: str,
    queues: list[str] | None = None,
    one_shot: bool = True,
    timeout: int = 60,
) -> int:
    """Spawn the standalone worker subprocess and (when one_shot) wait for it.

    Args:
        database_url: SQLAlchemy URL passed via env to the child.
        queues: Queue names to claim from. Defaults to ``['default']``.
        one_shot: When True the child claims-and-exits after one job.
            When False the child runs ``WorkerProcess`` until SIGTERM and
            this call returns immediately with rc=0.
        timeout: Seconds to wait for the subprocess (one_shot=True only).

    Returns:
        The child's exit code (or 0 for detached persistent workers).
    """
    queues = list(queues or ["default"])
    _dispose_engine_safely()

    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["SQLERY_FORCE_STANDALONE"] = "1"
    env["SQLERY_STANDALONE_DB_URL"] = database_url
    env["SQLERY_STANDALONE_QUEUES"] = ",".join(queues)
    env["SQLERY_STANDALONE_ONE_SHOT"] = "1" if one_shot else "0"

    cmd = [sys.executable, "-c", _STANDALONE_WORKER_SCRIPT]

    if one_shot:
        try:
            completed = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            logger.error(
                "standalone subprocess worker timed out after %ss; stdout=%r stderr=%r",
                timeout, e.stdout, e.stderr,
            )
            return -1
        if completed.returncode != 0:
            logger.error(
                "standalone subprocess worker failed (exit=%s)\n--- stdout ---\n%s\n--- stderr ---\n%s",
                completed.returncode, completed.stdout, completed.stderr,
            )
        else:
            logger.info("standalone subprocess worker completed cleanly")
        return completed.returncode

    proc = subprocess.Popen(
        cmd, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    logger.info("spawned persistent standalone worker (pid=%s)", proc.pid)
    return 0


__all__ = ["spawn_subprocess_worker"]
