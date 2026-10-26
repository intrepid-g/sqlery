"""Terminal transition and history reads for the Django integration (issue #140).

A job that reaches a terminal state leaves ``sqlery_queued_job`` for ``sqlery_job_history``
in the transaction that records the outcome (see ``sqlery.core.history`` for the model and
its invariants)::

    SELECT .. FOR UPDATE                                  -- PostgreSQL only
    INSERT INTO sqlery_job_history (...) SELECT ... FROM sqlery_queued_job WHERE <job>
    DELETE FROM sqlery_queued_job WHERE <job>

``<job>`` is the job's key plus the fences the caller asks for (``version``, status). Both
statements must touch exactly one row; a caller that gets ``False`` back has a stale
completion and rolls its transaction back. Every function here that writes must run inside
``transaction.atomic()``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from sqlery.core import history as history_core
from sqlery.core.history import TERMINAL_STATUSES

logger = logging.getLogger(__name__)


def _models():
    from . import models

    return models


def live_columns() -> list[str]:
    """Columns of the live table, which history and the union view share."""
    return [f.column for f in _models().QueuedJob._meta.concrete_fields]


def _prep(column: str, value):
    """``value`` as the database expects it for ``column`` (JSON, datetimes, UUIDs...)."""
    field = _models().QueuedJob._meta.get_field(column)
    return field.get_db_prep_save(value, connection)


def _expr(column: str) -> str:
    """Placeholder for a value of ``column`` in a SELECT list.

    PostgreSQL cannot infer the type of a bare parameter there (server-side binding), so it
    is cast to the column type.
    """
    if connection.vendor != "postgresql":
        return "%s"
    field = _models().QueuedJob._meta.get_field(column)
    return f"CAST(%s AS {field.db_type(connection)})"


def move(
    job_id: int,
    created_at,
    status: str,
    values: dict | None = None,
    *,
    expected_version: int | None = None,
    require_status=None,
) -> bool:
    """Move one live job into history with the final ``status``; True iff it moved.

    ``values`` are the columns that differ from the live row at the moment of the
    transition (``output``, ``error``, ``runs``, ``termination_reason`` ...). The history row's
    version is the live version plus one, and its ``finished_at`` is the given value, else
    the row's own, else now. Nothing is written when the job is not (or no longer) in the
    state the fences demand.
    """
    values = dict(values or {})
    overrides: dict[str, str] = {}
    override_params: dict[str, list] = {}

    def put(column: str, expr: str, *params):
        overrides[column] = expr
        override_params[column] = list(params)

    put("status", _expr("status"), status)
    put("version", "version + 1")
    if "finished_at" in values:
        put("finished_at", _expr("finished_at"), _prep("finished_at", values.pop("finished_at")))
    else:
        put(
            "finished_at",
            f"COALESCE(finished_at, {_expr('finished_at')})",
            _prep("finished_at", timezone.now()),
        )
    for column, value in values.items():
        if column in ("status", "version"):
            continue
        put(column, _expr(column), _prep(column, value))

    columns = live_columns()
    # parameters in the order their placeholders appear in the SELECT list
    select_params = [p for column in columns for p in override_params.get(column, ())]

    where = ["created_at = %s", "id = %s"]
    where_params = [_prep("created_at", created_at), job_id]
    if expected_version is not None:
        where.append("version = %s")
        where_params.append(expected_version)
    if require_status is not None:
        statuses = [require_status] if isinstance(require_status, str) else list(require_status)
        where.append(f"status IN ({', '.join(['%s'] * len(statuses))})")
        where_params.extend(statuses)
    where_sql = " AND ".join(where)

    with connection.cursor() as cur:
        if connection.vendor == "postgresql":
            # Row lock first: a concurrent writer of the same job finishes or is waited for,
            # and the two statements below then see the same row.
            cur.execute(
                f"SELECT 1 FROM {history_core.LIVE_TABLE} WHERE {where_sql} FOR UPDATE",
                where_params,
            )
            if cur.fetchone() is None:
                return False
        cur.execute(
            history_core.insert_select_sql(columns, overrides, where_sql),
            select_params + where_params,
        )
        if cur.rowcount != 1:
            return False
        cur.execute(history_core.delete_live_sql(where_sql), where_params)
        if cur.rowcount != 1:
            # Cannot happen after the row lock (SQLite has a single writer); never leave a
            # half move behind.
            raise RuntimeError(f"moving job {job_id} to history touched an unexpected row count")
    return True


def move_current(rows, status: str | None = None) -> int:
    """Move live rows to history as they are now (after a bulk update wrote their final
    state). ``rows`` are ``(id, created_at, status)``; ``status`` overrides the row's own.
    Returns how many moved."""
    moved = 0
    for job_id, created_at, row_status in rows:
        if move(job_id, created_at, status or row_status, {}):
            moved += 1
    return moved


def insert_finished(job) -> None:
    """Record a job that is created already finished (a terminal ``QueuedJob.save()`` of a
    new object, imports): it goes straight to history."""
    models = _models()
    values = {}
    for field in models.JobHistory._meta.concrete_fields:
        if field.name == "pk":
            continue
        values[field.attname] = getattr(job, field.attname, None)
    if values.get("created_at") is None:
        values["created_at"] = job.created_at = timezone.now()  # auto_now_add of the live table
    if values.get("finished_at") is None:
        values["finished_at"] = job.finished_at = job.started_at or values["created_at"]
    models.JobHistory(**values).save(force_insert=True)
    job._in_history = True


def get(job_id: int):
    """The history row of ``job_id`` or None."""
    return _models().JobHistory.objects.filter(id=job_id).first()


def find(job_id: int):
    """The job whether live or finished: the live ``QueuedJob``, else its ``JobHistory`` row."""
    models = _models()
    job = models.QueuedJob.objects.filter(id=job_id).first()
    if job is not None:
        return job
    return get(job_id)


def latest_retry(job_id: int):
    """Newest retry child of ``job_id`` (live or finished), or None."""
    return (
        _models().JobAll.objects.filter(parent_job_id=job_id).order_by("-created_at", "-id").first()
    )


def settle(job, status: str | None = None) -> bool:
    """Move a finished job that still sits in the live table into history (webhook delivery
    resolved, retry created). True iff it moved."""
    with transaction.atomic():
        return move(
            job.id,
            job.created_at,
            status or job.status,
            {},
            require_status=job.status,
        )


def settle_finished(limit: int = 200) -> int:
    """Settle sweep: move terminal rows stranded in the live table into history.

    The terminal transition normally leaves nothing behind. This is the safety net for raw
    SQL / third-party code that sets a final status on ``sqlery_queued_job``, for a crash
    between the steps of a retry, and for a webhook delivery that never resolved. A failed job
    that still owes a retry (marker) and a job whose webhook is still being retried are left
    alone; so is anything that finished within the grace period. Never raises.
    """
    from sqlery.compat import get_config
    from sqlery.core.retry import is_retry_pending

    models = _models()
    moved = 0
    try:
        cutoff = timezone.now() - timedelta(seconds=history_core.settle_grace_seconds(get_config))
        strays = list(
            models.QueuedJob.objects.filter(status__in=TERMINAL_STATUSES)
            .exclude(termination_reason__startswith="retry_pending")
            .filter(models.Q(finished_at__isnull=True) | models.Q(finished_at__lte=cutoff))
            .order_by("created_at", "id")[:limit]
        )
        for job in strays:
            if (
                job.webhook_url
                and job.webhook_status == "pending"
                and (job.webhook_retries < job.webhook_max_retries)
            ):
                continue  # delivery still being retried (retry_failed_webhooks settles it)
            has_retry = models.JobAll.objects.filter(parent_job_id=job.id).exists()
            status = history_core.settle_status(job.status, job.termination_reason, has_retry)
            if status is None or is_retry_pending(job.termination_reason):
                continue
            with transaction.atomic():
                ok = move(
                    job.id,
                    job.created_at,
                    status,
                    {
                        "termination_reason": history_core.settled_termination_reason(
                            job.termination_reason
                        )
                    },
                    require_status=job.status,
                )
            if ok:
                moved += 1
                from . import ready

                ready.release_dependents_quietly(job.id, job.parent_job_id)
    except Exception:
        logger.warning("settle_finished failed", exc_info=True)
    return moved
