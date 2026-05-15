"""SEC-04: Opt-in allowlist for task module imports.

When `ALLOWED_TASK_MODULES` is unset (or empty), behavior is unchanged — all
module paths are allowed. When configured, a worker may only import jobs whose
module path matches one of the allowlist entries using prefix-with-dot-boundary
semantics (`module == entry` or `module.startswith(entry + ".")`).

This closes the "anyone who can enqueue a job can execute arbitrary Python by
passing a chosen module path" risk for operators who opt in, without breaking
backward compatibility for the existing user base.

Design (per phase 04 CONTEXT):
- Empty / None config = pass-through (BC).
- Configured = prefix match with dot boundary (defends against `myapp_evil`
  bypass when `myapp` is on the list).
- Production-env signal + unset = emit exactly one WARNING at worker startup.
"""

import logging
import os

logger = logging.getLogger(__name__)


class TaskModuleNotAllowed(Exception):
    """Raised when a job's module path is not in ALLOWED_TASK_MODULES."""


def check_task_module_allowed(module_path: str, allowed: list[str] | None) -> None:
    """Enforce the allowlist for a task module path.

    Args:
        module_path: Fully-qualified module path, e.g. ``"myapp.tasks"``.
        allowed: Configured allowlist. ``None`` or empty list = pass-through
            (allow all, preserves backward compatibility).

    Raises:
        TaskModuleNotAllowed: If ``allowed`` is set and ``module_path`` does
            not match any entry under prefix-with-dot-boundary semantics.

    Returns:
        None on success (allowed).

    Example:
        >>> check_task_module_allowed("myapp.tasks", ["myapp"])  # ok
        >>> check_task_module_allowed("myapp_evil.tasks", ["myapp"])
        Traceback (most recent call last):
            ...
        TaskModuleNotAllowed: Module 'myapp_evil.tasks' not in ALLOWED_TASK_MODULES
    """
    # Pass-through: unset / empty list = BC (allow all).
    if not allowed:
        return None

    for entry in allowed:
        if module_path == entry or module_path.startswith(entry + "."):
            return None

    raise TaskModuleNotAllowed(
        f"Module {module_path!r} not in ALLOWED_TASK_MODULES"
    )


def is_production_env(env: dict | None = None) -> bool:
    """Detect a production-shaped runtime environment.

    Checks ``ENV``, ``ENVIRONMENT``, and ``DJANGO_SETTINGS_MODULE`` env vars
    for the substring ``prod`` (case-insensitive). This catches values like
    ``"production"``, ``"prod"``, and ``"myproj.settings_production"``.

    Args:
        env: Optional dict-like environment to inspect. Defaults to
            ``os.environ``.

    Returns:
        True if any of the inspected vars look production-shaped.
    """
    if env is None:
        env = os.environ

    for key in ("ENV", "ENVIRONMENT", "DJANGO_SETTINGS_MODULE"):
        value = env.get(key)
        if value and "prod" in value.lower():
            return True
    return False


def warn_if_unconfigured(allowed: list[str] | None) -> None:
    """Emit exactly one WARNING when running production-shaped but unconfigured.

    Designed to be called once at worker startup. No-op outside production-shaped
    environments and no-op when ``allowed`` is configured.

    Args:
        allowed: The currently-configured ALLOWED_TASK_MODULES value.
    """
    if allowed:
        return
    if not is_production_env():
        return
    logger.warning(
        "ALLOWED_TASK_MODULES is unset in a production-shaped environment. "
        "Any module path that can be enqueued will be imported and executed. "
        "Configure ALLOWED_TASK_MODULES (Django settings DJANGO_SQL_JOBS) or "
        "SQLERY_ALLOWED_TASK_MODULES (standalone env var) to restrict imports. "
        "See SEC-04."
    )
