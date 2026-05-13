"""Regression tests for Phase 1 standalone-import contract (UNIF-04/05/06).

These tests assert that `sqlery.core` and all its submodules import cleanly in a
Python interpreter where `django` has been forcibly blocked from `sys.modules`.

The implementation spawns a fresh subprocess and installs a `MetaPathFinder` that
raises `ImportError` on any attempt to import `django` or `django.*`. This works
even when `django` is installed in the dev environment (which it is, via the
`dev` extra). The CI job `standalone-no-django` provides the complementary
layer: a fresh venv where `django` is genuinely not installed.

If either layer ever fails, an unguarded `import django` has crept back into the
core layer — see CONTEXT.md "code_context" for the original 11 offending modules.
"""

import subprocess
import sys


# All 11 core submodules that must import cleanly without Django installed.
# Enumerated from CONTEXT.md / src/sqlery/core/ — keep in sync if new modules added.
_CORE_SUBMODULES = [
    "sqlery.core",
    "sqlery.core.claiming",
    "sqlery.core.worker",
    "sqlery.core.daemon",
    "sqlery.core.db_resilience",
    "sqlery.core.log_config",
    "sqlery.core.model_utils",
    "sqlery.core.daemon_runner",
    "sqlery.core.worker_runner",
    "sqlery.core.scheduler_tasks",
    "sqlery.core.utils",
    "sqlery.core.worker_pool",
]


_BLOCK_DJANGO_PREAMBLE = """
import sys


class _BlockDjango:
    def find_spec(self, name, path=None, target=None):
        if name == "django" or name.startswith("django."):
            raise ImportError(f"django blocked for test: {name}")
        return None


sys.meta_path.insert(0, _BlockDjango())
"""


def _run_in_subprocess(body: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run `body` in a fresh Python interpreter with django blocked at import time."""
    code = _BLOCK_DJANGO_PREAMBLE + body
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_core_imports_without_django():
    """All 11 sqlery.core submodules must import in a django-less interpreter."""
    imports = "\n".join(f"import {mod}" for mod in _CORE_SUBMODULES)
    body = imports + '\nprint("OK")\n'

    result = _run_in_subprocess(body)

    assert result.returncode == 0, (
        f"Subprocess failed (rc={result.returncode}).\n"
        f"stdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "OK" in result.stdout, f"Missing OK sentinel. stdout={result.stdout!r}"


def test_db_resilience_retry_works_without_django():
    """`retry_on_db_error` decorator must wrap functions without Django installed.

    This proves Plan 01 Task 1's `_RETRYABLE_EXC` fallback path actually works
    at runtime when django.db.utils is unavailable.
    """
    body = """
import sqlery.core.db_resilience as dbr

assert hasattr(dbr, "retry_on_db_error"), "retry_on_db_error missing"

@dbr.retry_on_db_error(max_retries=1)
def _noop():
    return 42

assert _noop() == 42, "decorated function did not return underlying value"
print("RETRY_OK")
"""
    result = _run_in_subprocess(body)

    assert result.returncode == 0, (
        f"Subprocess failed (rc={result.returncode}).\n"
        f"stdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "RETRY_OK" in result.stdout, f"Missing RETRY_OK sentinel. stdout={result.stdout!r}"
