"""DEPRECATED 2026-05-13 — moved to sqlery.core.claiming.

This module is a backward-compatibility stub for any out-of-repo importers
during the deprecation window. Remove after 2027-05-13.

Policy: comment-and-date marking per CLAUDE.md and feedback_dead_code memory.
See .planning/phases/01-core-unification/01-CONTEXT.md.
"""
# DEPRECATED 2026-05-13 — moved to sqlery.core.claiming. Remove after 2027-05-13.
from sqlery.core.claiming import *  # noqa: F401,F403
from sqlery.core.claiming import (  # explicit re-export for type checkers
    claim_next_job_with_queue_priority,
    release_job,
    get_node_id,
)


def __getattr__(name):
    import sqlery.core.claiming as _canon
    try:
        return getattr(_canon, name)
    except AttributeError as e:
        raise AttributeError(
            f"{name!r} not found in deprecated sqlery.django_sqlery.worker_claiming; "
            "this module is retired — see sqlery.core.claiming"
        ) from e
