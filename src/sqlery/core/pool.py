"""Worker pool models: Celery-style prefork or RQ-style fork-per-job (issue #161).

``WORKER_POOL = "prefork"`` (the default, as in Celery) keeps one long-lived child per
``WorkerProcess``. The parent still claims, heartbeats, renews leases, polls revokes and
enforces the timeout; it hands each claimed job to the child over a pipe. The child runs
jobs until ``WORKER_MAX_TASKS_PER_CHILD`` or ``WORKER_MAX_MEMORY_PER_CHILD`` says to recycle
it (both default ``None`` = never, Celery's defaults). A hard timeout, a revoke or a crash
kills the child; the next job gets a fresh one.

``WORKER_POOL = "fork"`` forks a new child for every job (RQ's work horse), the model
sqlery used before #161.

DB connections are refreshed at the recycle rate: ``WORKER_DB_REUSE_MAX`` (default ``None``
= ``WORKER_MAX_TASKS_PER_CHILD``) force-closes them every N jobs, in the child and in the
parent. When neither is set, connections are closed around each job only when unusable or
past ``CONN_MAX_AGE`` (what Celery's Django fixup does by default).
"""

from __future__ import annotations

import logging
import os
import pickle
import struct
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

POOL_PREFORK = "prefork"
POOL_FORK = "fork"
POOLS = (POOL_PREFORK, POOL_FORK)

_FRAME_HEADER = struct.Struct("!I")


@dataclass(frozen=True)
class PoolSettings:
    """Resolved pool configuration of one worker."""

    pool: str = POOL_PREFORK
    max_tasks_per_child: int | None = None
    max_memory_per_child: int | None = None  # KiB
    db_reuse_max: int | None = None


# ---------------------------------------------------------------------------
# Pure functions — no IO, no side effects, deterministic
# ---------------------------------------------------------------------------


def _positive_int(value) -> int | None:
    """``value`` as a positive int, or None for unset, zero, negative or invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve_pool_settings(
    pool=POOL_PREFORK, max_tasks_per_child=None, max_memory_per_child=None, db_reuse_max=None
) -> PoolSettings:
    """Normalise the raw settings; an unknown pool falls back to prefork with a warning.

    ``db_reuse_max`` unset follows ``max_tasks_per_child``: connections are refreshed at
    the same rate the child is recycled.
    """
    name = str(pool or POOL_PREFORK).strip().lower()
    if name not in POOLS:
        logger.warning(f"Unknown WORKER_POOL {pool!r}; using {POOL_PREFORK!r}")
        name = POOL_PREFORK
    max_tasks = _positive_int(max_tasks_per_child)
    reuse = _positive_int(db_reuse_max)
    return PoolSettings(
        pool=name,
        max_tasks_per_child=max_tasks,
        max_memory_per_child=_positive_int(max_memory_per_child),
        db_reuse_max=reuse if reuse is not None else max_tasks,
    )


def db_refresh_due(jobs_done: int, db_reuse_max: int | None) -> bool:
    """Whether DB connections are force-refreshed after the ``jobs_done``-th job."""
    return bool(db_reuse_max) and jobs_done > 0 and jobs_done % db_reuse_max == 0


def should_recycle(
    jobs_done: int, max_tasks: int | None, max_rss_kib: int | None, max_memory_kib: int | None
) -> bool:
    """Whether the prefork child exits after its ``jobs_done``-th job."""
    if max_tasks and jobs_done >= max_tasks:
        return True
    return bool(max_memory_kib) and max_rss_kib is not None and max_rss_kib > max_memory_kib


# ---------------------------------------------------------------------------
# Adapter layer — settings, process memory, DB connections, pipe frames
# ---------------------------------------------------------------------------


def load_pool_settings() -> PoolSettings:
    """Read the pool settings from the active config."""
    from ..compat import get_config

    return resolve_pool_settings(
        get_config("WORKER_POOL", POOL_PREFORK),
        get_config("WORKER_MAX_TASKS_PER_CHILD", None),
        get_config("WORKER_MAX_MEMORY_PER_CHILD", None),
        get_config("WORKER_DB_REUSE_MAX", None),
    )


def max_rss_kib() -> int | None:
    """Peak resident set size of this process in KiB (what Celery compares), or None."""
    try:
        import resource
    except ImportError:
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss // 1024 if sys.platform == "darwin" else rss  # macOS reports bytes


def refresh_db_connections(force: bool) -> None:
    """Close this process's DB connections; they reopen on the next query.

    ``force`` closes every connection (Django) and empties the SQLAlchemy pool. Otherwise
    only Django connections that are unusable or past ``CONN_MAX_AGE`` are closed, and the
    SQLAlchemy pool is left to ``pool_pre_ping`` / ``pool_recycle``.
    """
    if force:
        from .db_resilience import discard_connections

        discard_connections()
        return
    try:
        from django.conf import settings
        from django.db import close_old_connections

        if settings.configured:
            close_old_connections()
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Refreshing Django DB connections failed: {e}")


def encode_frame(obj) -> bytes:
    """``obj`` pickled and length-prefixed, for :func:`read_frame`. Raises if unpicklable."""
    payload = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    return _FRAME_HEADER.pack(len(payload)) + payload


def write_all(fd: int, data: bytes) -> None:
    """Write all of ``data`` to a pipe (a large frame takes several writes)."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _read_exact(fd: int, size: int) -> bytes | None:
    chunks = []
    while size:
        chunk = os.read(fd, size)
        if not chunk:
            return None
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def read_frame(fd: int):
    """Receive one object framed by :func:`encode_frame`; None once the writer has closed."""
    header = _read_exact(fd, _FRAME_HEADER.size)
    if header is None:
        return None
    payload = _read_exact(fd, _FRAME_HEADER.unpack(header)[0])
    return None if payload is None else pickle.loads(payload)
