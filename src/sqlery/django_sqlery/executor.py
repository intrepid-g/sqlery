"""DEPRECATED 2026-05-13 — moved to sqlery.core.worker.

This module is a backward-compatibility stub for any out-of-repo importers
during the deprecation window. Remove after 2027-05-13.

The historic Django-coupled `TaskExecutor` class still lives in this repo
(at `sqlery.django_sqlery._executor_impl.TaskExecutor`) because it carries
scheduled-task helpers not yet ported to the framework-agnostic
`sqlery.core.worker.JobExecutor`. Porting those is tracked as a follow-up
in .planning/phases/01-core-unification/01-CONTEXT.md.

In-repo callers should import from `sqlery.core.worker` (which lazily
resolves `TaskExecutor` via module __getattr__).

Policy: comment-and-date marking per CLAUDE.md and feedback_dead_code memory.
See .planning/phases/01-core-unification/01-CONTEXT.md.
"""
# DEPRECATED 2026-05-13 — moved to sqlery.core.worker. Remove after 2027-05-13.
from sqlery.core.worker import *  # noqa: F401,F403
from sqlery.core.worker import (  # explicit re-export for type checkers
    JobExecutor,
    _current_job_var,
)
from sqlery.django_sqlery._executor_impl import TaskExecutor, _RQCompatJob  # noqa: F401


def __getattr__(name):
    # Try canonical core.worker first, then fall back to the historic
    # Django-coupled implementation module for any symbol it might expose.
    import sqlery.core.worker as _canon_worker
    import sqlery.django_sqlery._executor_impl as _impl
    try:
        return getattr(_canon_worker, name)
    except AttributeError:
        pass
    try:
        return getattr(_impl, name)
    except AttributeError as e:
        raise AttributeError(
            f"{name!r} not found in deprecated sqlery.django_sqlery.executor; "
            "this module is retired — see sqlery.core.worker"
        ) from e
