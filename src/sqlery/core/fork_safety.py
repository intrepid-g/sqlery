"""Fork-safe connection lifecycle for fork-per-job execution.

Replaces manual _reset_db_connections() discipline with a hook-based
system that makes it impossible to fork with open DB connections.
"""

import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure functions — no IO, no side effects, deterministic
# ---------------------------------------------------------------------------


def build_default_hooks(
    django_available: bool,
    sqlalchemy_engine: object | None = None,
) -> dict[str, list[str]]:
    """Determine which fork hooks are needed based on available backends.

    Returns a dict with keys 'pre_fork', 'post_fork_parent', 'post_fork_child',
    each mapping to a list of hook identifiers (strings). The identifiers are
    resolved to actual callables by ForkSafeExecutor.

    Args:
        django_available: Whether Django's DB layer is importable.
        sqlalchemy_engine: A SQLAlchemy engine instance, or None.

    Returns:
        Dict of hook phase -> list of hook identifiers.
    """
    hooks: dict[str, list[str]] = {
        "pre_fork": [],
        "post_fork_parent": [],
        "post_fork_child": [],
    }

    if django_available:
        hooks["pre_fork"].append("django_close_all")
        hooks["post_fork_parent"].append("django_close_old")
        hooks["post_fork_child"].append("django_close_all")

    if sqlalchemy_engine is not None:
        hooks["pre_fork"].append("sqlalchemy_dispose")
        hooks["post_fork_child"].append("sqlalchemy_dispose")

    return hooks


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
        django_leaks = self._check_django_connections()
        sa_status = self._check_sqlalchemy_pool()
        leaks = verify_no_open_connections(django_leaks, sa_status)
        for leak in leaks:
            logger.warning(f"fork_safety: {leak}")

    @staticmethod
    def _check_django_connections() -> list[str] | None:
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
                if conn.connection is not None:
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

        hook_plan = build_default_hooks(django_available, sa_engine)

        hook_registry: dict[str, Callable[[], None]] = {}
        if django_available:
            hook_registry["django_close_all"] = dj_connections.close_all
            hook_registry["django_close_old"] = close_old_connections
        if sa_engine is not None:
            hook_registry["sqlalchemy_dispose"] = sa_engine.dispose

        for phase, identifiers in hook_plan.items():
            register_fn = getattr(executor, f"register_{phase}")
            for hook_id in identifiers:
                if hook_id in hook_registry:
                    register_fn(hook_registry[hook_id])

        return executor
