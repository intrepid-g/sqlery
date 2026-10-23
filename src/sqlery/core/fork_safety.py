"""Fork-safe connection lifecycle for fork-per-job execution.

Replaces manual _reset_db_connections() discipline with a hook-based
system that makes it impossible to fork with open DB connections.
"""

import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

# Opt-in setting (default False): keep the parent's PostgreSQL connection open across
# os.fork() instead of closing it before and reopening it after every job (#127).
KEEP_PG_CONNECTION_SETTING = "KEEP_PG_CONNECTION_ACROSS_FORK"

# libpq transaction status meaning "idle, nothing pending" (PQTRANS_IDLE).
_PQTRANS_IDLE = 0

# Inherited PostgreSQL connections disowned by a forked child. Held here so they are never
# garbage-collected: finalising one runs PQfinish, which sends Terminate on the socket the
# parent still uses. The child leaves through os._exit, so these are never finalised.
_DISOWNED_PG_CONNECTIONS: list = []


# ---------------------------------------------------------------------------
# Pure functions — no IO, no side effects, deterministic
# ---------------------------------------------------------------------------


def build_default_hooks(
    django_available: bool,
    sqlalchemy_engine: object | None = None,
    keep_pg_connection: bool = False,
) -> dict[str, list[str]]:
    """Determine which fork hooks are needed based on available backends.

    Returns a dict with keys 'pre_fork', 'post_fork_parent', 'post_fork_child',
    each mapping to a list of hook identifiers (strings). The identifiers are
    resolved to actual callables by ForkSafeExecutor.

    Args:
        django_available: Whether Django's DB layer is importable.
        sqlalchemy_engine: A SQLAlchemy engine instance, or None.
        keep_pg_connection: Opt in to keeping idle PostgreSQL Django connections open
            across the fork (the child disowns them without closing). Everything else
            (SQLite, libSQL, a PostgreSQL connection that is mid-transaction) is still
            closed before the fork.

    Returns:
        Dict of hook phase -> list of hook identifiers.
    """
    hooks: dict[str, list[str]] = {
        "pre_fork": [],
        "post_fork_parent": [],
        "post_fork_child": [],
    }

    if django_available and keep_pg_connection:
        hooks["pre_fork"].append("django_close_unkept")
        hooks["post_fork_child"].append("django_disown_pg")
    elif django_available:
        hooks["pre_fork"].append("django_close_all")
        hooks["post_fork_parent"].append("django_close_old")
        hooks["post_fork_child"].append("django_close_all")

    if sqlalchemy_engine is not None:
        hooks["pre_fork"].append("sqlalchemy_dispose")
        hooks["post_fork_child"].append("sqlalchemy_dispose")

    return hooks


def can_keep_across_fork(
    vendor: str, in_atomic_block: bool, needs_rollback: bool, uses_pool: bool, tx_status: object
) -> bool:
    """Whether an open Django connection may stay open through os.fork().

    Only PostgreSQL, only a plain (unpooled) connection that is idle: not inside an
    atomic block, not marked for rollback, and libpq reports no transaction or pending
    result. Anything else is closed before the fork, exactly as without the opt-in.
    """
    return (
        vendor == "postgresql"
        and not in_atomic_block
        and not needs_rollback
        and not uses_pool
        and tx_status == _PQTRANS_IDLE
    )


def verify_no_open_connections(
    django_connection_names: list[str] | None = None,
    sqlalchemy_pool_status: dict | None = None,
) -> list[str]:
    """Check for connections that survived a close_all and should not be open.

    Returns a list of warning strings for each leaked connection found.
    Empty list means all clear.

    Args:
        django_connection_names: Names of Django DB connections that report
            as usable (i.e. conn.is_usable() returns True after close_all).
        sqlalchemy_pool_status: Dict with 'checkedout' key from pool.status().

    Returns:
        List of leak warning strings. Empty = clean.
    """
    leaks: list[str] = []

    if django_connection_names:
        for name in django_connection_names:
            leaks.append(f"django connection '{name}' still open after close_all")

    if sqlalchemy_pool_status and sqlalchemy_pool_status.get("checkedout", 0) > 0:
        count = sqlalchemy_pool_status["checkedout"]
        leaks.append(f"sqlalchemy pool has {count} checked-out connection(s) after dispose")

    return leaks


# ---------------------------------------------------------------------------
# Adapter layer — the actual IO around os.fork()
# ---------------------------------------------------------------------------


class ForkSafeExecutor:
    """Wraps os.fork() with guaranteed DB connection lifecycle hooks.

    Usage:
        executor = ForkSafeExecutor.auto_configure()
        child_pid = executor.fork()
        if child_pid == 0:
            # child — connections are already clean
            ...
        else:
            # parent — connections are already reconnected
            ...
    """

    def __init__(self):
        self._pre_fork: list[Callable[[], None]] = []
        self._post_fork_parent: list[Callable[[], None]] = []
        self._post_fork_child: list[Callable[[], None]] = []
        self.keep_pg_connection = False

    def register_pre_fork(self, hook: Callable[[], None]) -> None:
        self._pre_fork.append(hook)

    def register_post_fork_parent(self, hook: Callable[[], None]) -> None:
        self._post_fork_parent.append(hook)

    def register_post_fork_child(self, hook: Callable[[], None]) -> None:
        self._post_fork_child.append(hook)

    def fork(self) -> int:
        """Fork with guaranteed connection lifecycle.

        Runs pre-fork hooks, calls os.fork(), then runs the appropriate
        post-fork hooks in parent or child. Returns the PID (0 in child,
        >0 in parent), same as os.fork().
        """
        for hook in self._pre_fork:
            try:
                hook()
            except Exception as e:
                logger.warning(f"pre_fork hook {hook.__name__} failed: {e}")

        self._verify_pre_fork()

        pid = os.fork()

        if pid == 0:
            for hook in self._post_fork_child:
                try:
                    hook()
                except Exception as e:
                    logger.warning(f"post_fork_child hook {hook.__name__} failed: {e}")
        else:
            for hook in self._post_fork_parent:
                try:
                    hook()
                except Exception as e:
                    logger.warning(f"post_fork_parent hook {hook.__name__} failed: {e}")

        return pid

    def _verify_pre_fork(self) -> None:
        """Log warnings for any connections that survived pre-fork cleanup."""
        django_leaks = self._check_django_connections(skip_keepable=self.keep_pg_connection)
        sa_status = self._check_sqlalchemy_pool()
        leaks = verify_no_open_connections(django_leaks, sa_status)
        for leak in leaks:
            logger.warning(f"fork_safety: {leak}")

    @staticmethod
    def _django_conn_keepable(conn) -> bool:
        """Adapter for can_keep_across_fork: read the live state of one Django connection."""
        try:
            raw = conn.connection
            if raw is None:
                return False
            tx_status = raw.info.transaction_status
            tx_status = getattr(tx_status, "value", tx_status)
            return can_keep_across_fork(
                vendor=conn.vendor,
                in_atomic_block=conn.in_atomic_block,
                needs_rollback=conn.needs_rollback,
                uses_pool=bool(conn.settings_dict.get("OPTIONS", {}).get("pool")),
                tx_status=tx_status,
            )
        except Exception:
            return False

    @classmethod
    def django_close_unkept(cls) -> None:
        """pre_fork (opt-in mode): close every Django connection except keepable PostgreSQL ones."""
        from django.db import connections

        for conn in connections.all(initialized_only=True):
            if conn.connection is not None and not cls._django_conn_keepable(conn):
                conn.close()

    @staticmethod
    def django_disown_pg() -> None:
        """post_fork_child (opt-in mode): drop inherited PostgreSQL connections WITHOUT closing.

        The child shares the parent's socket. close() (or letting the driver object be
        garbage-collected) would send Terminate on it and end the parent's session. The
        child only forgets the connection (Django opens its own on the next query) and
        parks the driver object so it is never finalised. Any other open connection is
        closed as in the default path.
        """
        from django.db import connections

        for conn in connections.all(initialized_only=True):
            raw = conn.connection
            if raw is None:
                continue
            if conn.vendor != "postgresql":
                conn.close()
                continue
            _DISOWNED_PG_CONNECTIONS.append(raw)
            conn.connection = None
            conn.close_at = None
            conn.run_on_commit = []

    @classmethod
    def _check_django_connections(cls, skip_keepable: bool = False) -> list[str] | None:
        try:
            from django.conf import settings
            from django.db import connections as dj_connections
        except ImportError:
            return None
        if not settings.configured:
            return None
        open_names = []
        try:
            for name in dj_connections:
                conn = dj_connections[name]
                if conn.connection is not None and not (
                    skip_keepable and cls._django_conn_keepable(conn)
                ):
                    open_names.append(name)
        except Exception:
            return None
        return open_names or None

    @staticmethod
    def _check_sqlalchemy_pool() -> dict | None:
        try:
            from sqlery.fastapi_sqlery.database import _engine
            if _engine is not None and hasattr(_engine, "pool"):
                return {"checkedout": _engine.pool.checkedout()}
        except Exception:
            pass
        return None

    @classmethod
    def auto_configure(cls) -> "ForkSafeExecutor":
        """Build a ForkSafeExecutor with hooks for all detected backends."""
        executor = cls()

        try:
            from django.conf import settings
            from django.db import connections as dj_connections, close_old_connections
            django_available = settings.configured
        except ImportError:
            django_available = False
            dj_connections = None
            close_old_connections = None

        sa_engine = None
        try:
            from sqlery.fastapi_sqlery.database import _engine
            sa_engine = _engine
        except Exception:
            pass

        keep_pg = False
        if django_available:
            try:
                from sqlery.compat import get_config

                keep_pg = bool(get_config(KEEP_PG_CONNECTION_SETTING, False))
            except Exception:
                keep_pg = False

        executor.keep_pg_connection = keep_pg
        hook_plan = build_default_hooks(django_available, sa_engine, keep_pg_connection=keep_pg)

        hook_registry: dict[str, Callable[[], None]] = {}
        if django_available:
            hook_registry["django_close_all"] = dj_connections.close_all
            hook_registry["django_close_old"] = close_old_connections
            hook_registry["django_close_unkept"] = cls.django_close_unkept
            hook_registry["django_disown_pg"] = cls.django_disown_pg
        if sa_engine is not None:
            hook_registry["sqlalchemy_dispose"] = sa_engine.dispose

        for phase, identifiers in hook_plan.items():
            register_fn = getattr(executor, f"register_{phase}")
            for hook_id in identifiers:
                if hook_id in hook_registry:
                    register_fn(hook_registry[hook_id])

        return executor
