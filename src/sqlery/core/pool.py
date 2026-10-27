"""Worker pool models: Celery-style prefork or RQ-style fork-per-job (issue #161).

``WORKER_POOL = "prefork"`` (the default, as in Celery) keeps one long-lived child per
``WorkerProcess``. The parent still claims, heartbeats, renews leases, polls revokes and
enforces the timeout; it hands each claimed job to the child over a pipe. The child runs
jobs until ``WORKER_MAX_TASKS_PER_CHILD`` or ``WORKER_MAX_MEMORY_PER_CHILD`` says to recycle
it (both default ``None`` = never, Celery's defaults). A hard timeout, a revoke or a crash
kills the child; the next job gets a fresh one.

``WORKER_POOL = "fork"`` forks a new child for every job (RQ's work horse), the model
sqlery used before #161.

Concurrency (``MAX_WORKERS_PER_NODE``, the number of workers per node) defaults to Celery's
``worker_concurrency``: the number of CPUs, here capped to the CPUs this process may use
(affinity and the cgroup CPU quota of a container).

Every option can be set in the config, overridden by ``SQLERY_WORKER_*`` environment
variables (so a CLI flag reaches the worker processes a daemon spawns), and per worker by
``WorkerProcess`` arguments.

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

#: Setting -> environment variable that overrides it (both modes; spawned workers inherit it).
POOL_ENV = {
    "WORKER_POOL": "SQLERY_WORKER_POOL",
    "WORKER_MAX_TASKS_PER_CHILD": "SQLERY_WORKER_MAX_TASKS_PER_CHILD",
    "WORKER_MAX_MEMORY_PER_CHILD": "SQLERY_WORKER_MAX_MEMORY_PER_CHILD",
    "WORKER_DB_REUSE_MAX": "SQLERY_WORKER_DB_REUSE_MAX",
}
CONCURRENCY_ENV = "SQLERY_WORKER_CONCURRENCY"


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


def cpus_from(
    affinity: int | None, cpu_max: str | None, cfs_quota_us=None, cfs_period_us=None
) -> int:
    """CPUs usable by this process: the affinity count capped by a cgroup CPU quota.

    ``cpu_max`` is cgroup v2 ``cpu.max`` ("max 100000" or "200000 100000"); the v1 pair is
    ``cpu.cfs_quota_us`` / ``cpu.cfs_period_us`` (quota -1 = unlimited). A fractional quota
    rounds up (1.5 CPUs -> 2), and the result is at least 1.
    """
    cpus = affinity if affinity and affinity > 0 else 1
    quota = period = None
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) == 2 and parts[0] != "max":
            quota, period = parts
    elif cfs_quota_us is not None and cfs_period_us is not None:
        quota, period = cfs_quota_us, cfs_period_us
    try:
        quota, period = float(quota), float(period)
    except (TypeError, ValueError):
        return cpus
    if quota > 0 and period > 0:
        cpus = min(cpus, max(1, -(-quota // period)))
    return int(cpus)


def resolve_concurrency(value, cpus: int) -> int:
    """``MAX_WORKERS_PER_NODE`` as a worker count: a positive int, else ``cpus`` (Celery)."""
    return _positive_int(value) or cpus


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


def _setting(name: str, default=None):
    """An environment override (``SQLERY_...``) of a setting, else the active config."""
    env = POOL_ENV.get(name) or (CONCURRENCY_ENV if name == "MAX_WORKERS_PER_NODE" else None)
    raw = os.environ.get(env) if env else None
    if raw is not None and raw.strip() != "":
        return raw.strip()
    from ..compat import get_config

    return get_config(name, default)


def load_pool_settings(
    pool: str | None = None,
    max_tasks_per_child: int | None = None,
    max_memory_per_child: int | None = None,
    db_reuse_max: int | None = None,
) -> PoolSettings:
    """The pool settings: arguments, else ``SQLERY_WORKER_*`` env vars, else the config.

    Unset everywhere, they are Celery's defaults (prefork, never recycled).
    """
    given = {
        "WORKER_POOL": pool,
        "WORKER_MAX_TASKS_PER_CHILD": max_tasks_per_child,
        "WORKER_MAX_MEMORY_PER_CHILD": max_memory_per_child,
        "WORKER_DB_REUSE_MAX": db_reuse_max,
    }
    values = {name: v if v is not None else _setting(name) for name, v in given.items()}
    return resolve_pool_settings(
        values["WORKER_POOL"] or POOL_PREFORK,
        values["WORKER_MAX_TASKS_PER_CHILD"],
        values["WORKER_MAX_MEMORY_PER_CHILD"],
        values["WORKER_DB_REUSE_MAX"],
    )


def export_pool_options(
    pool: str | None = None,
    max_tasks_per_child: int | None = None,
    max_memory_per_child: int | None = None,
    db_reuse_max: int | None = None,
    concurrency: int | None = None,
) -> None:
    """Make CLI options visible to this process and the workers it spawns (via env vars)."""
    for name, value in (
        ("WORKER_POOL", pool),
        ("WORKER_MAX_TASKS_PER_CHILD", max_tasks_per_child),
        ("WORKER_MAX_MEMORY_PER_CHILD", max_memory_per_child),
        ("WORKER_DB_REUSE_MAX", db_reuse_max),
    ):
        if value is not None:
            os.environ[POOL_ENV[name]] = str(value)
    if concurrency is not None:
        os.environ[CONCURRENCY_ENV] = str(concurrency)


def add_pool_arguments(parser) -> None:
    """Add Celery-style worker options to an argparse parser (Django management commands)."""
    parser.add_argument(
        "--pool",
        "-P",
        choices=POOLS,
        default=None,
        help="Worker pool: prefork (default, like Celery) or fork (a child per job, like RQ)",
    )
    parser.add_argument(
        "--max-tasks-per-child",
        type=int,
        default=None,
        help="Recycle the prefork child after N jobs (default: never)",
    )
    parser.add_argument(
        "--max-memory-per-child",
        type=int,
        default=None,
        help="Recycle the prefork child once its max RSS exceeds N KiB (default: never)",
    )
    parser.add_argument(
        "--db-reuse-max",
        type=int,
        default=None,
        help="Refresh DB connections every N jobs (default: the recycle rate)",
    )


def export_pool_arguments(options: dict) -> None:
    """Export the options added by :func:`add_pool_arguments` (see export_pool_options)."""
    export_pool_options(
        pool=options.get("pool"),
        max_tasks_per_child=options.get("max_tasks_per_child"),
        max_memory_per_child=options.get("max_memory_per_child"),
        db_reuse_max=options.get("db_reuse_max"),
    )


def available_cpus() -> int:
    """CPUs this process may run on (Celery uses ``os.cpu_count()``; this also honours a
    container's CPU quota, so a 2-CPU container on a 64-core host gets 2 workers)."""
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = os.cpu_count() or 1

    def read(path):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None

    return cpus_from(
        affinity,
        read("/sys/fs/cgroup/cpu.max"),
        read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
        read("/sys/fs/cgroup/cpu/cpu.cfs_period_us"),
    )


def worker_concurrency(value=None) -> int:
    """Workers per node: ``value``, else ``SQLERY_WORKER_CONCURRENCY``, else
    ``MAX_WORKERS_PER_NODE``, else the number of CPUs (Celery's default)."""
    if _positive_int(value):
        return int(value)
    return resolve_concurrency(_setting("MAX_WORKERS_PER_NODE", None), available_cpus())


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
