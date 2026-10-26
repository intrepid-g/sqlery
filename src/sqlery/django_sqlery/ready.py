"""Ready-queue maintenance for the Django integration (issue #139).

Everything that makes a job claimable or unclaimable writes the ready entry in the same
transaction as the job row; see ``sqlery.core.ready`` for the model and its invariants. The
queue's entry points are: ``QueuedJob.save()`` / ``QueuedJob.objects.update()`` (enqueue,
requeue, edits, cancels), the claim (``DjangoBackend.get_claimable_jobs`` /
``atomic_claim_job`` / ``block_job``) and the dependency release below.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from sqlery.compat import get_config
from sqlery.core import ready as ready_core
from sqlery.core.ready import SLOT_CURRENT, SLOTS

logger = logging.getLogger(__name__)

_CHUNK = 500


def _models():
    from . import models

    return models


def _chunks(items, size=_CHUNK):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i : i + size]


def entry_for(job):
    """The ready entry (unsaved) of ``job``."""
    m = _models()
    return m.JobReady(
        slot=SLOT_CURRENT,
        job_id=job.id,
        queue_name=job.queue_name,
        priority=job.priority or 0,
        created_at=job.created_at,
        scheduled_at=job.scheduled_at,
    )


def add(jobs) -> None:
    """Give each job a ready entry; an existing entry is left alone (idempotent)."""
    m = _models()
    entries = [entry_for(j) for j in jobs]
    if entries:
        m.JobReady.objects.bulk_create(entries, ignore_conflicts=True)


def remove(job_ids, wait: bool = True) -> None:
    """Drop the ready and blocked entries of ``job_ids`` (they are not claimable now).

    With ``wait=False`` (PostgreSQL) a ready entry that another transaction holds locked is
    left alone instead of waited for: its holder is a claimer that will find the job no
    longer queued and drop the entry itself. Callers that already hold the job row locked
    (the legacy executor) use it, because waiting here, while that claimer waits for the job
    row, would deadlock.
    """
    m = _models()
    ids = [i for i in job_ids if i is not None]
    for chunk in _chunks(ids):
        entries = m.JobReady.objects.filter(slot__in=SLOTS, job_id__in=chunk)
        if wait or connection.vendor == "sqlite":
            entries.delete()
        else:
            held = list(entries.select_for_update(skip_locked=True).values_list("slot", "job_id"))
            for slot, job_id in held:
                m.JobReady.objects.filter(slot=slot, job_id=job_id).delete()
        m.JobBlocked.objects.filter(job_id__in=chunk).delete()


def before_save(job) -> bool:
    """Edit / drop the entry of an existing job BEFORE its row is written (a claim locks the
    entry first, then the row, so every writer follows that order). Returns True if an
    existing entry was updated."""
    m = _models()
    if job.status == "queued":
        return (
            m.JobReady.objects.filter(slot__in=SLOTS, job_id=job.id).update(
                queue_name=job.queue_name,
                priority=job.priority or 0,
                scheduled_at=job.scheduled_at,
            )
            > 0
        )
    remove([job.id])
    return False


def after_save(job, adding: bool, entry_updated: bool) -> None:
    """Give a job that is queued after its save the entry it still lacks."""
    m = _models()
    if job.status != "queued" or entry_updated:
        return
    if adding or not m.JobBlocked.objects.filter(job_id=job.id).exists():
        add([job])  # new job, became queued (again), or its entry was lost


def before_update(rows, edits: dict) -> None:
    """Apply a bulk edit of queue / priority / schedule to the existing entries of ``rows``
    before the job rows are updated (entries first: the lock order of a claim)."""
    if not edits:
        return
    m = _models()
    for chunk in _chunks(r[0] for r in rows):
        m.JobReady.objects.filter(slot__in=SLOTS, job_id__in=chunk).update(**edits)


def after_update(rows, requeue: bool, edits: dict) -> None:
    """Follow a bulk ``QuerySet.update`` of ``rows`` (``id, created_at, queue_name, priority,
    scheduled_at`` as they were before it)."""
    m = _models()
    if requeue:
        entries = []
        for job_id, created_at, queue_name, priority, scheduled_at in rows:
            entries.append(
                m.JobReady(
                    slot=SLOT_CURRENT,
                    job_id=job_id,
                    queue_name=edits.get("queue_name", queue_name),
                    priority=edits.get("priority", priority) or 0,
                    created_at=created_at,
                    scheduled_at=edits.get("scheduled_at", scheduled_at),
                )
            )
        for chunk in _chunks(entries):
            m.JobReady.objects.bulk_create(chunk, ignore_conflicts=True)


# --------------------------------------------------------------------------------------------
# Blocked set
# --------------------------------------------------------------------------------------------


def block(job) -> bool:
    """Move a ready job whose dependencies are unmet to the blocked set.

    Returns False if the job was not ready any more. Once the surrounding transaction has
    committed the job is re-checked: a dependency may have finished while the rows were not
    yet visible to its release (the usual lost wake-up of check-then-wait).
    """
    m = _models()
    now = timezone.now()
    rows = [
        m.JobBlocked(job_id=job.id, dependency_id=dep, created_at=job.created_at, blocked_at=now)
        for dep in ready_core.dependency_ids(job)
    ]
    if not rows:
        return False  # no usable dependency id to wait on: the job stays ready
    with transaction.atomic():
        deleted, _ = m.JobReady.objects.filter(slot__in=SLOTS, job_id=job.id).delete()
        if not deleted:
            return False
        m.JobBlocked.objects.bulk_create(rows, ignore_conflicts=True)
    job_id, created_at = job.id, job.created_at
    transaction.on_commit(lambda: recheck_quietly(job_id, created_at))
    return True


def recheck_blocked(job_id: int, created_at) -> str:
    """Re-judge one blocked job: ``ready``, ``failed``, ``blocked`` or ``gone``."""
    m = _models()
    with transaction.atomic():
        job = m.QueuedJob.objects.filter(id=job_id, created_at=created_at).first()
        if job is None or job.status != "queued":
            m.JobBlocked.objects.filter(job_id=job_id).delete()
            return "gone"
        all_met, failed = job.check_dependencies_met()
        if failed:
            try:
                job.mark_failed(
                    error=f"Dependencies failed: {failed}",
                    termination_reason="dependency_failed",
                )
            except m.ConcurrentModificationError:
                return "gone"  # someone else handled it
            return "failed"
        if not all_met:
            return "blocked"
        m.JobBlocked.objects.filter(job_id=job_id).delete()
        add([job])
        return "ready"


def recheck_quietly(job_id: int, created_at) -> None:
    try:
        recheck_blocked(job_id, created_at)
    except Exception:
        logger.warning(f"recheck of blocked job {job_id} failed", exc_info=True)


def release_dependents(job_id: int, parent_job_id: int | None = None) -> int:
    """Re-check the jobs blocked on ``job_id`` (a job that reached a terminal state).

    Dependents of the job's retry ancestors are re-checked too (a dependency is judged by
    its newest retry attempt). Returns how many dependents became ready.
    """
    m = _models()
    released = 0
    work = [(job_id, parent_job_id)]
    seen: set[int] = set()
    while work:
        current, parent = work.pop()
        if current in seen:
            continue
        seen.add(current)
        ids = [current]
        hops = 0
        while parent and parent not in ids and hops < ready_core.MAX_CHAIN_HOPS:
            ids.append(parent)
            parent = (
                m.QueuedJob.objects.filter(id=parent)
                .values_list("parent_job_id", flat=True)
                .first()
            )
            hops += 1
        dependents = list(
            m.JobBlocked.objects.filter(dependency_id__in=ids)
            .values_list("job_id", "created_at")
            .distinct()
        )
        for blocked_id, created_at in dependents:
            outcome = recheck_blocked(blocked_id, created_at)
            if outcome == "ready":
                released += 1
            elif outcome == "failed":
                work.append((blocked_id, None))
    return released


def release_dependents_quietly(job_id: int, parent_job_id: int | None = None) -> None:
    """Release the dependents of a finished job; never raises (the periodic sweep repairs
    whatever this misses)."""
    try:
        release_dependents(job_id, parent_job_id)
    except Exception:
        logger.warning(f"release_dependents({job_id}) failed", exc_info=True)


@contextmanager
def _bounded_statements(seconds: float):
    """Cap statement time on PostgreSQL for the sweep's scans, then restore the setting.

    The sweep reads every queued job once per run, so on a deep backlog it must not hold the
    shared transaction (and the xmin horizon) for long: a statement past the cap fails and
    the sweep tries again next time.
    """
    if connection.vendor != "postgresql" or seconds <= 0:
        yield
        return
    with connection.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        previous = cur.fetchone()[0]
        cur.execute(
            "SELECT set_config('statement_timeout', %s, true)", [f"{int(seconds * 1000)}ms"]
        )
    try:
        yield
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('statement_timeout', %s, true)", [previous])


def _sweep_timeout() -> float:
    try:
        return float(get_config("READY_QUEUE_SWEEP_TIMEOUT", 10))
    except (TypeError, ValueError):
        return 10.0


def maintain(queue_names=None, limit: int = 200, after_job_id: int = 0) -> dict:
    """Repair the ready queue (see ``DatabaseBackend.maintain_ready_queue``)."""
    m = _models()
    jobs = m.QueuedJob.objects.filter(status="queued")
    ghosts = m.JobReady.objects.filter(
        ~Exists(
            m.QueuedJob.objects.filter(
                id=OuterRef("job_id"), created_at=OuterRef("created_at"), status="queued"
            )
        )
    )
    if queue_names:
        jobs = jobs.filter(queue_name__in=list(queue_names))
        ghosts = ghosts.filter(queue_name__in=list(queue_names))
    jobs = jobs.filter(
        ~Exists(m.JobReady.objects.filter(slot__in=SLOTS, job_id=OuterRef("id")))
    ).filter(~Exists(m.JobBlocked.objects.filter(job_id=OuterRef("id"))))
    with _bounded_statements(_sweep_timeout()):
        with transaction.atomic():
            # 1. queued jobs with no entry get one back
            lost = list(jobs[:limit])
            add(lost)
            # 2. entries of jobs that are no longer queued are dropped (a claim would drop
            #    them too, but a queue nobody claims from would keep them)
            if connection.vendor != "sqlite":
                ghosts = ghosts.select_for_update(skip_locked=True, of=("self",))
            stale = list(ghosts.values_list("slot", "job_id")[:limit])
            for slot, job_id in stale:
                m.JobReady.objects.filter(slot=slot, job_id=job_id).delete()
    if lost:
        logger.warning(f"Restored {len(lost)} queued job(s) that had no ready entry")
    page = list(
        m.JobBlocked.objects.filter(job_id__gt=after_job_id)
        .order_by("job_id")
        .values_list("job_id", "created_at")
        .distinct()[:limit]
    )
    cursor = 0
    for blocked_id, created_at in page:
        recheck_quietly(blocked_id, created_at)
        cursor = blocked_id
    return {
        "restored": len(lost),
        "dropped": len(stale),
        "rechecked": len(page),
        "cursor": cursor if len(page) >= limit else 0,
    }
