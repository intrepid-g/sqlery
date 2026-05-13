"""DEPRECATED 2026-05-13 — moved to sqlery.core.worker.

This top-level shim re-exports `TaskExecutor` for any out-of-repo importers
during the deprecation window. Remove after 2027-05-13.

Policy: comment-and-date marking per CLAUDE.md and feedback_dead_code memory.
See .planning/phases/01-core-unification/01-CONTEXT.md.
"""
# DEPRECATED 2026-05-13 — moved to sqlery.core.worker. Remove after 2027-05-13.
from sqlery.core.worker import *  # noqa: F401,F403
from sqlery.core.worker import (  # explicit re-export for type checkers
    JobExecutor,
    _current_job_var,
)


def __getattr__(name):
    import sqlery.core.worker as _canon
    try:
        return getattr(_canon, name)
    except AttributeError as e:
        raise AttributeError(
            f"{name!r} not found in deprecated sqlery.executor; "
            "this module is retired — see sqlery.core.worker"
        ) from e
