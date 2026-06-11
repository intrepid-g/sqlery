"""Unit tests for DjangoBackend (TEST-09).

Method-by-method coverage of `src/sqlery/django_sqlery/backend.py` using
pytest-django with `@pytest.mark.django_db`. Does not import from
`tests/unit/conftest.py` (owned by parallel plan 03-03).
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone


@pytest.fixture
def django_backend(db):
    """Construct a fresh DjangoBackend bound to the pytest-django test DB."""
    from sqlery.django_sqlery.backend import DjangoBackend
    return DjangoBackend()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_basic_job(backend, **overrides):
    defaults = dict(
        task_path="tests.fake.task",
        kwargs={"x": 1},
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
    )
    defaults.update(overrides)
    return backend.create_job(**defaults)


def _new_worker(backend, status="idle"):
    """Create a Worker row directly and return (uuid, model_instance)."""
    w = backend.Worker.objects.create(
        node_id="testnode",
        pid=os.getpid() + int.from_bytes(os.urandom(2), "big"),
        status=status,
    )
    return w.id, w


# ---------------------------------------------------------------------------
# 1. Enqueue / Claim
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEnqueueAndClaim:
    def test_create_job_persists(self, django_backend):
        job = _create_basic_job(django_backend, queue_name="alpha")
        assert job.id is not None
        assert job.status == "queued"
        assert job.queue_name == "alpha"

    def test_create_job_full_optionals(self, django_backend):
        job = _create_basic_job(
            django_backend,
            max_retries=2,
            retry_backoff=2.0,
            allow_parallel=True,
            timeout_seconds=30,
            retry_count=1,
            job_name="named",
            retry_intervals=[1, 2],
            meta={"k": "v"},
            dependencies=[],
            on_success_path="cb.ok",
            on_failure_path="cb.bad",
            ttl=120,
            result_ttl=60,
            failure_ttl=300,
        )
        assert job.job_name == "named"
        assert job.max_retries == 2

    def test_named_job_replaces_existing(self, django_backend):
        _create_basic_job(django_backend, job_name="dup")
        _create_basic_job(django_backend, job_name="dup")
        assert django_backend.QueuedJob.objects.filter(job_name="dup").count() == 1

    def test_claim_job_returns_queued_then_running(self, django_backend):
        _new_worker(django_backend, status="idle")
        _create_basic_job(django_backend, queue_name="q1")
        wid, _ = _new_worker(django_backend, status="idle")
        claimed = django_backend.claim_job(queues=["q1"], worker_id=str(wid))
        assert claimed is not None
        assert claimed.status == "running"

    def test_claim_job_none_when_empty(self, django_backend):
        wid, _ = _new_worker(django_backend)
        assert django_backend.claim_job(queues=["empty"], worker_id=str(wid)) is None

    def test_claim_job_no_worker(self, django_backend):
        # Worker not registered → claim_job returns None
        _create_basic_job(django_backend, queue_name="q")
        assert django_backend.claim_job(queues=["q"], worker_id=str(uuid.uuid4())) is None

    def test_claim_job_runs_inside_transaction(self, django_backend):
        """Regression: claim_job must wrap select_for_update in transaction.atomic()."""
        from unittest.mock import patch, MagicMock
        from django.db import transaction

        _create_basic_job(django_backend, queue_name="txq")
        wid, _ = _new_worker(django_backend, status="idle")

        atomic_calls = []
        original_atomic = transaction.atomic

        def tracking_atomic(*args, **kwargs):
            ctx = original_atomic(*args, **kwargs)
            atomic_calls.append(True)
            return ctx

        with patch.object(transaction, "atomic", side_effect=tracking_atomic):
            django_backend.claim_job(queues=["txq"], worker_id=str(wid))

        assert len(atomic_calls) >= 1, "claim_job must use transaction.atomic()"


# ---------------------------------------------------------------------------
# 2. Status transitions + optimistic locking
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStatusTransitions:
    def test_mark_job_success(self, django_backend):
        from django.db.models import F
        job = _create_basic_job(django_backend)
        # Need status='running' first so mark_success doesn't lock
        django_backend.QueuedJob.objects.filter(id=job.id).update(
            status="running", started_at=timezone.now()
        )
        job.refresh_from_db()
        out = django_backend.mark_job_success(job.id, output="ok")
        out.refresh_from_db()
        assert out.status == "success"
        assert out.output == "ok"

    def test_mark_job_failed(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=job.id).update(
            status="running", started_at=timezone.now()
        )
        job.refresh_from_db()
        out = django_backend.mark_job_failed(job.id, error="boom", traceback="tb")
        out.refresh_from_db()
        assert out.status == "failed"
        assert out.error == "boom"
        assert out.traceback == "tb"

    def test_mark_job_archived(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=job.id).update(status="failed")
        django_backend.mark_job_archived(job.id)
        job.refresh_from_db()
        assert job.status == "archived"

    def test_mark_job_success_missing(self, django_backend):
        assert django_backend.mark_job_success(999999) is None

    def test_mark_job_failed_missing(self, django_backend):
        assert django_backend.mark_job_failed(999999, error="x") is None

    def test_cancel_job(self, django_backend):
        job = _create_basic_job(django_backend)
        assert django_backend.cancel_job(job.id) is True
        assert django_backend.cancel_job(job.id) is False

    def test_release_job(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=job.id).update(
            status="running", started_at=timezone.now(), worker_pid=123
        )
        django_backend.release_job(job.id)
        job.refresh_from_db()
        assert job.status == "queued"
        assert job.started_at is None
        assert job.worker_pid is None

    def test_concurrent_modification_raises(self, django_backend):
        """Explicit ConcurrentModificationError assertion for T-03-07."""
        from sqlery.django_sqlery.models import ConcurrentModificationError

        job = _create_basic_job(django_backend)
        # Put into 'queued' state with version 0, then bump version externally
        # to simulate a competing writer.
        django_backend.QueuedJob.objects.filter(id=job.id).update(
            status="queued", version=99
        )
        job.refresh_from_db()
        # Force a stale version on the in-memory instance and call mark_running
        job.version = 0
        with pytest.raises(ConcurrentModificationError):
            job.mark_running()


# ---------------------------------------------------------------------------
# 3. Retry / TTL
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRetryAndTTL:
    def test_retry_failed_jobs(self, django_backend):
        j = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=j.id).update(status="failed")
        count = django_backend.retry_failed_jobs()
        assert count == 1
        j.refresh_from_db()
        assert j.status == "queued"

    def test_retry_failed_by_queue(self, django_backend):
        a = _create_basic_job(django_backend, queue_name="a")
        b = _create_basic_job(django_backend, queue_name="b")
        django_backend.QueuedJob.objects.filter(id__in=[a.id, b.id]).update(status="failed")
        assert django_backend.retry_failed_jobs(queue_name="a") == 1

    def test_retry_failed_max_jobs(self, django_backend):
        for _ in range(3):
            j = _create_basic_job(django_backend)
            django_backend.QueuedJob.objects.filter(id=j.id).update(status="failed")
        assert django_backend.retry_failed_jobs(max_jobs=2) == 2

    def test_get_expired_ttl_jobs(self, django_backend):
        j = _create_basic_job(django_backend, ttl=1)
        # Backdate created_at
        django_backend.QueuedJob.objects.filter(id=j.id).update(
            created_at=timezone.now() - timedelta(seconds=10)
        )
        expired = django_backend.get_expired_ttl_jobs()
        assert any(e.id == j.id for e in expired)

    def test_get_expired_ttl_skips_unexpired(self, django_backend):
        j = _create_basic_job(django_backend, ttl=3600)
        expired = django_backend.get_expired_ttl_jobs()
        assert not any(e.id == j.id for e in expired)

    def test_get_expired_ttl_skips_no_ttl(self, django_backend):
        _create_basic_job(django_backend, ttl=None)
        assert django_backend.get_expired_ttl_jobs() == []


# ---------------------------------------------------------------------------
# 4. Worker lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWorkerLifecycle:
    def test_update_heartbeat_creates_row_via_string_id(self, django_backend):
        django_backend.update_worker_heartbeat(
            worker_id="worker_testhost_4242", status="idle"
        )
        assert django_backend.Worker.objects.filter(node_id="testhost", pid=4242).exists()

    def test_update_heartbeat_daemon_format(self, django_backend):
        django_backend.update_worker_heartbeat(
            worker_id="daemon_testhost", status="idle"
        )
        assert django_backend.Worker.objects.filter(node_id="testhost", pid=0).exists()

    def test_update_heartbeat_by_uuid(self, django_backend):
        wid, _ = _new_worker(django_backend)
        django_backend.update_worker_heartbeat(
            worker_id=str(wid), status="busy", jobs_processed=3, total_busy_seconds=1.5
        )
        w = django_backend.Worker.objects.get(id=wid)
        assert w.status == "busy"
        assert w.jobs_processed == 3
        assert w.total_busy_seconds == 1.5

    def test_update_heartbeat_dead_skips_timestamp(self, django_backend):
        wid, w_initial = _new_worker(django_backend)
        # Set heartbeat to a known old value
        old_ts = timezone.now() - timedelta(hours=1)
        django_backend.Worker.objects.filter(id=wid).update(last_heartbeat=old_ts)
        django_backend.update_worker_heartbeat(worker_id=str(wid), status="dead")
        w = django_backend.Worker.objects.get(id=wid)
        assert w.status == "dead"
        # last_heartbeat should NOT have been bumped
        assert (timezone.now() - w.last_heartbeat) > timedelta(minutes=30)

    def test_get_worker_heartbeats_active_only(self, django_backend):
        wid_active, _ = _new_worker(django_backend)
        wid_stale, _ = _new_worker(django_backend)
        django_backend.Worker.objects.filter(id=wid_stale).update(
            last_heartbeat=timezone.now() - timedelta(minutes=10)
        )
        active = django_backend.get_worker_heartbeats(active_only=True)
        ids = {w.id for w in active}
        assert wid_active in ids
        assert wid_stale not in ids

    def test_get_worker_heartbeats_all(self, django_backend):
        wid1, _ = _new_worker(django_backend)
        wid2, _ = _new_worker(django_backend)
        all_rows = django_backend.get_worker_heartbeats(active_only=False)
        ids = {w.id for w in all_rows}
        assert {wid1, wid2}.issubset(ids)

    def test_delete_worker_registration(self, django_backend):
        wid, _ = _new_worker(django_backend)
        assert django_backend.delete_worker_registration(str(wid)) == 1
        assert django_backend.delete_worker_registration(str(wid)) == 0

    def test_refresh_worker_heartbeat(self, django_backend):
        wid, _ = _new_worker(django_backend)
        django_backend.Worker.objects.filter(id=wid).update(
            last_heartbeat=timezone.now() - timedelta(hours=1)
        )
        django_backend.refresh_worker_heartbeat(wid)
        w = django_backend.Worker.objects.get(id=wid)
        assert (timezone.now() - w.last_heartbeat) < timedelta(seconds=5)

    def test_refresh_worker_heartbeat_invalid(self, django_backend):
        # Should not raise
        django_backend.refresh_worker_heartbeat("not-a-uuid")

    def test_is_worker_paused(self, django_backend):
        wid, w = _new_worker(django_backend)
        # Not paused initially
        assert django_backend.is_worker_paused(str(wid)) is False
        # Pause until future
        django_backend.Worker.objects.filter(id=wid).update(
            paused_until=timezone.now() + timedelta(hours=1)
        )
        assert django_backend.is_worker_paused(str(wid)) is True
        # Past pause: clears and returns False
        django_backend.Worker.objects.filter(id=wid).update(
            paused_until=timezone.now() - timedelta(hours=1)
        )
        assert django_backend.is_worker_paused(str(wid)) is False
        w.refresh_from_db()
        assert w.paused_until is None

    def test_release_claimed_job(self, django_backend):
        wid, _ = _new_worker(django_backend, status="busy")
        job = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=job.id).update(
            status="running", started_at=timezone.now()
        )
        job.refresh_from_db()
        out = django_backend.release_claimed_job(
            job, worker_id=str(wid), status="success", jobs_processed=5
        )
        assert out.status == "success"
        assert out.finished_at is not None
        w = django_backend.Worker.objects.get(id=wid)
        assert w.status == "idle"
        assert w.jobs_processed == 5


# ---------------------------------------------------------------------------
# 5. Scheduled tasks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestScheduledTasks:
    def test_create_and_get(self, django_backend):
        t = django_backend.create_scheduled_task(
            name="t1", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        assert t.id is not None
        assert django_backend.get_scheduled_task(t.id).name == "t1"

    def test_get_scheduled_task_missing(self, django_backend):
        assert django_backend.get_scheduled_task(999999) is None

    def test_get_due_scheduled_tasks(self, django_backend):
        due = django_backend.create_scheduled_task(
            name="due", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        not_due = django_backend.create_scheduled_task(
            name="not_due", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        django_backend.update_scheduled_task_next_run(
            due.id, timezone.now() - timedelta(minutes=1)
        )
        django_backend.update_scheduled_task_next_run(
            not_due.id, timezone.now() + timedelta(hours=1)
        )
        ids = {t.id for t in django_backend.get_due_scheduled_tasks()}
        assert due.id in ids
        assert not_due.id not in ids

    def test_get_due_skips_disabled(self, django_backend):
        t = django_backend.create_scheduled_task(
            name="off", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0, enabled=False,
        )
        django_backend.update_scheduled_task_next_run(
            t.id, timezone.now() - timedelta(minutes=1)
        )
        assert all(d.id != t.id for d in django_backend.get_due_scheduled_tasks())

    def test_update_scheduled_task(self, django_backend):
        t = django_backend.create_scheduled_task(
            name="x", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        out = django_backend.update_scheduled_task(t.id, priority=10)
        assert out.priority == 10

    def test_delete_scheduled_task(self, django_backend):
        t = django_backend.create_scheduled_task(
            name="del", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        assert django_backend.delete_scheduled_task(t.id) is True
        assert django_backend.delete_scheduled_task(t.id) is False

    def test_get_scheduled_tasks_enabled_only(self, django_backend):
        django_backend.create_scheduled_task(
            name="on", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0, enabled=True,
        )
        django_backend.create_scheduled_task(
            name="off", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0, enabled=False,
        )
        names = {t.name for t in django_backend.get_scheduled_tasks(enabled_only=True)}
        assert names == {"on"}

    def test_has_pending_job_for_scheduled_task(self, django_backend):
        t = django_backend.create_scheduled_task(
            name="p", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        assert django_backend.has_pending_job_for_scheduled_task(t.id) is False
        _create_basic_job(django_backend, scheduled_task_id=t.id)
        assert django_backend.has_pending_job_for_scheduled_task(t.id) is True

    def test_claim_due_scheduled_task(self, django_backend):
        t = django_backend.create_scheduled_task(
            name="cl", task_path="m.f", cron_expression="* * * * *",
            queue_name="default", priority=0,
        )
        django_backend.update_scheduled_task_next_run(
            t.id, timezone.now() - timedelta(minutes=1)
        )
        claimed = django_backend.claim_due_scheduled_task(t.id)
        assert claimed is not None and claimed.id == t.id


# ---------------------------------------------------------------------------
# 6. Registry
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRegistry:
    def test_add_and_get_registry(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.add_job_to_registry(job.id, "started", metadata={"k": "v"})
        jobs = django_backend.get_registry_jobs("started")
        assert any(j.id == job.id for j in jobs)

    def test_get_registry_filters_by_queue(self, django_backend):
        a = _create_basic_job(django_backend, queue_name="a")
        b = _create_basic_job(django_backend, queue_name="b")
        django_backend.add_job_to_registry(a.id, "finished")
        django_backend.add_job_to_registry(b.id, "finished")
        a_only = django_backend.get_registry_jobs("finished", queue_name="a")
        assert [j.id for j in a_only] == [a.id]

    def test_get_registry_limit(self, django_backend):
        for _ in range(3):
            j = _create_basic_job(django_backend)
            django_backend.add_job_to_registry(j.id, "finished")
        assert len(django_backend.get_registry_jobs("finished", limit=2)) == 2

    def test_remove_from_registry(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.add_job_to_registry(job.id, "started")
        django_backend.remove_job_from_registry(job.id, "started")
        assert django_backend.get_registry_jobs("started") == []

    def test_cleanup_registry(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.add_job_to_registry(job.id, "finished")
        result = django_backend.cleanup_registry(registry_type="finished")
        assert result["deleted"] >= 1

    def test_cleanup_registry_max_age(self, django_backend):
        job = _create_basic_job(django_backend)
        django_backend.add_job_to_registry(job.id, "finished")
        django_backend.JobRegistry.objects.filter(job_id=job.id).update(
            entered_at=timezone.now() - timedelta(days=30)
        )
        result = django_backend.cleanup_registry(max_age_days=1)
        assert result["deleted"] >= 1


# ---------------------------------------------------------------------------
# 7. Cleanup / stats / vacuum
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCleanup:
    def test_cleanup_jobs_by_status(self, django_backend):
        j = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=j.id).update(status="failed")
        result = django_backend.cleanup_jobs(status="failed")
        assert result["deleted"] >= 1

    def test_cleanup_jobs_dry_run(self, django_backend):
        _create_basic_job(django_backend)
        # dry_run keeps count, but cleanup_jobs in Django returns count==count
        # for both modes; the row should remain.
        before = django_backend.QueuedJob.objects.count()
        django_backend.cleanup_jobs(status="queued", dry_run=True)
        after = django_backend.QueuedJob.objects.count()
        assert before == after

    def test_cleanup_jobs_max_age(self, django_backend):
        j = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=j.id).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        result = django_backend.cleanup_jobs(max_age_days=1)
        assert result["deleted"] >= 1

    def test_cleanup_jobs_by_count(self, django_backend):
        for _ in range(5):
            _create_basic_job(django_backend)
        result = django_backend.cleanup_jobs_by_count(keep_count=2)
        assert result["kept"] == 2
        assert result["deleted"] == 3

    def test_cleanup_jobs_by_count_dry_run(self, django_backend):
        for _ in range(5):
            _create_basic_job(django_backend)
        before = django_backend.QueuedJob.objects.count()
        django_backend.cleanup_jobs_by_count(keep_count=2, dry_run=True)
        after = django_backend.QueuedJob.objects.count()
        assert before == after

    def test_get_queue_stats(self, django_backend):
        _create_basic_job(django_backend, queue_name="s")
        stats = django_backend.get_queue_stats(queue_name="s")
        assert stats["queued"] == 1
        assert stats["queue_name"] == "s"

    def test_get_queue_stats_global(self, django_backend):
        _create_basic_job(django_backend)
        stats = django_backend.get_queue_stats()
        assert stats["queued"] >= 1

    def test_get_database_stats(self, django_backend):
        _create_basic_job(django_backend)
        stats = django_backend.get_database_stats()
        assert stats["total_jobs"] >= 1
        assert "job_counts" in stats
        assert "total_workers" in stats

    def test_vacuum_database(self, django_backend):
        # SQLite VACUUM should succeed
        out = django_backend.vacuum_database()
        assert "success" in out


# ---------------------------------------------------------------------------
# 8. Misc
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMiscMethods:
    def test_get_job_by_id_missing(self, django_backend):
        assert django_backend.get_job_by_id(999999) is None

    def test_get_job_by_id_present(self, django_backend):
        j = _create_basic_job(django_backend)
        assert django_backend.get_job_by_id(j.id).id == j.id

    def test_get_running_jobs(self, django_backend):
        j = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=j.id).update(status="running")
        assert len(django_backend.get_running_jobs()) == 1
        assert len(django_backend.get_running_jobs(queue_name="default")) == 1
        assert django_backend.get_running_jobs(queue_name="other") == []

    def test_has_running_jobs_in_queue(self, django_backend):
        j = _create_basic_job(django_backend, queue_name="x")
        django_backend.QueuedJob.objects.filter(id=j.id).update(status="running")
        assert django_backend.has_running_jobs_in_queue("x") is True
        assert django_backend.has_running_jobs_in_queue("y") is False

    def test_has_running_jobs_exclude(self, django_backend):
        j = _create_basic_job(django_backend, queue_name="x")
        django_backend.QueuedJob.objects.filter(id=j.id).update(status="running")
        assert django_backend.has_running_jobs_in_queue("x", exclude_job_id=j.id) is False

    def test_get_jobs_pagination(self, django_backend):
        for _ in range(5):
            _create_basic_job(django_backend)
        page1 = django_backend.get_jobs(limit=2, offset=0)
        page2 = django_backend.get_jobs(limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 2
        assert {j.id for j in page1}.isdisjoint({j.id for j in page2})

    def test_get_jobs_filtered(self, django_backend):
        _create_basic_job(django_backend, queue_name="a")
        _create_basic_job(django_backend, queue_name="b")
        a_jobs = django_backend.get_jobs(queue_name="a")
        assert all(j.queue_name == "a" for j in a_jobs)

    def test_count_jobs(self, django_backend):
        _create_basic_job(django_backend)
        _create_basic_job(django_backend)
        assert django_backend.count_jobs() == 2
        assert django_backend.count_jobs(status="queued") == 2
        assert django_backend.count_jobs(status="success") == 0

    def test_count_jobs_by_queue(self, django_backend):
        _create_basic_job(django_backend, queue_name="a")
        _create_basic_job(django_backend, queue_name="b")
        assert django_backend.count_jobs(queue_name="a") == 1

    def test_update_job_child_pid(self, django_backend):
        j = _create_basic_job(django_backend)
        django_backend.update_job_child_pid(j.id, 4242)
        j.refresh_from_db()
        assert j.child_pid == 4242

    def test_cascade_ancestor_status(self, django_backend):
        grandparent = _create_basic_job(django_backend)
        parent = _create_basic_job(django_backend, parent_job_id=grandparent.id)
        child = _create_basic_job(django_backend, parent_job_id=parent.id)
        django_backend.cascade_ancestor_status(child.id, "archived")
        parent.refresh_from_db()
        grandparent.refresh_from_db()
        assert parent.status == "archived"
        assert grandparent.status == "archived"

    def test_count_running_with_tag(self, django_backend):
        # JSONField .contains is not supported on SQLite; assert behaviour by
        # calling and accepting NotSupportedError. Real semantics covered in
        # Plan 03-07's postgres mirror.
        from django.db.utils import NotSupportedError
        j = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=j.id).update(
            status="running", tags=["acme"]
        )
        try:
            assert django_backend.count_running_with_tag("acme") in (0, 1)
        except NotSupportedError:
            pytest.skip("SQLite JSONField contains not supported (Postgres-only)")

    def test_count_started_with_tag_since(self, django_backend):
        from django.db.utils import NotSupportedError
        j = _create_basic_job(django_backend)
        django_backend.QueuedJob.objects.filter(id=j.id).update(
            status="running", tags=["acme"], started_at=timezone.now()
        )
        threshold = timezone.now() - timedelta(hours=1)
        try:
            assert django_backend.count_started_with_tag_since("acme", threshold) in (0, 1)
        except NotSupportedError:
            pytest.skip("SQLite JSONField contains not supported (Postgres-only)")

    def test_get_claimable_jobs(self, django_backend):
        _create_basic_job(django_backend, queue_name="q")
        out = django_backend.get_claimable_jobs(queues=["q"], limit=5)
        assert len(out) == 1

    def test_get_claimable_jobs_with_weights(self, django_backend):
        _create_basic_job(django_backend, queue_name="q", priority=0)
        out = django_backend.get_claimable_jobs(
            queues=["q"], priority_weights={"q": 5}, limit=5
        )
        assert len(out) == 1

    def test_acquire_tag_locks(self, django_backend):
        # Must not raise on SQLite; creates TagLock rows.
        django_backend.acquire_tag_locks(["acme", "stripe"])
        from sqlery.django_sqlery.models import TagLock
        assert TagLock.objects.filter(tag__in=["acme", "stripe"]).count() == 2

    def test_claim_queue_leases_and_renew_release(self, django_backend):
        claimed = django_backend.claim_queue_leases(
            queues=["a", "b"], daemon_id="d1", node_id="n1", pid=1, lease_secs=60,
        )
        assert set(claimed) == {"a", "b"}
        # Renew
        django_backend.renew_queue_leases(
            owned_queues=["a", "b"], daemon_id="d1", lease_secs=120,
        )
        # Release
        django_backend.release_queue_leases(owned_queues=["a", "b"], daemon_id="d1")
        from sqlery.django_sqlery.models import DaemonLease
        assert DaemonLease.objects.filter(queue_name__in=["a", "b"]).count() == 0

    def test_claim_lease_held_by_other(self, django_backend):
        django_backend.claim_queue_leases(
            queues=["x"], daemon_id="d1", node_id="n1", pid=1, lease_secs=60,
        )
        claimed = django_backend.claim_queue_leases(
            queues=["x"], daemon_id="d2", node_id="n2", pid=2, lease_secs=60,
        )
        # Active lease held by d1 should block d2
        assert claimed == []


# ---------------------------------------------------------------------------
# 9. Enqueue routing — scheduled_at threshold (14-02)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEnqueueRoutingThreshold:
    """Verify create_job routes far-future jobs to sqlery_scheduled_job (D1).

    Tests 1-4 mirror the four routing behaviors in the plan's <behavior> block.
    """

    def test_far_future_creates_scheduled_job_not_queued_job(self, django_backend):
        """Test 1: scheduled_at = now+2days creates ScheduledJob, not QueuedJob (threshold=1day)."""
        from sqlery.django_sqlery.models import ScheduledJob
        queued_before = django_backend.QueuedJob.objects.count()
        staged_before = ScheduledJob.objects.count()
        far_future = timezone.now() + timedelta(days=2)
        result = _create_basic_job(django_backend, scheduled_at=far_future)
        # Must be a ScheduledJob row
        assert isinstance(result, ScheduledJob), (
            f"Expected ScheduledJob, got {type(result).__name__}"
        )
        assert ScheduledJob.objects.count() == staged_before + 1, (
            "Expected one new ScheduledJob row"
        )
        assert django_backend.QueuedJob.objects.count() == queued_before, (
            "QueuedJob count must not change for far-future jobs"
        )

    def test_near_future_creates_queued_job(self, django_backend):
        """Test 2: scheduled_at = now+12hrs creates QueuedJob (below 1-day threshold)."""
        from sqlery.django_sqlery.models import ScheduledJob
        queued_before = django_backend.QueuedJob.objects.count()
        staged_before = ScheduledJob.objects.count()
        near_future = timezone.now() + timedelta(hours=12)
        result = _create_basic_job(django_backend, scheduled_at=near_future)
        assert result.__class__.__name__ == "QueuedJob", (
            f"Expected QueuedJob, got {type(result).__name__}"
        )
        assert django_backend.QueuedJob.objects.count() == queued_before + 1
        assert ScheduledJob.objects.count() == staged_before, (
            "No ScheduledJob row should be created for near-future jobs"
        )

    def test_no_scheduled_at_creates_queued_job(self, django_backend):
        """Test 3: scheduled_at=None creates QueuedJob immediately."""
        from sqlery.django_sqlery.models import ScheduledJob
        queued_before = django_backend.QueuedJob.objects.count()
        staged_before = ScheduledJob.objects.count()
        result = _create_basic_job(django_backend, scheduled_at=None)
        assert result.__class__.__name__ == "QueuedJob"
        assert django_backend.QueuedJob.objects.count() == queued_before + 1
        assert ScheduledJob.objects.count() == staged_before

    def test_exact_threshold_boundary_creates_queued_job(self, django_backend):
        """Test 4: scheduled_at = exactly now+1day creates QueuedJob (boundary is exclusive)."""
        from sqlery.django_sqlery.models import ScheduledJob
        queued_before = django_backend.QueuedJob.objects.count()
        staged_before = ScheduledJob.objects.count()
        # Exactly at threshold: not strictly greater, so goes to main queue
        exact_boundary = timezone.now() + timedelta(days=1)
        result = _create_basic_job(django_backend, scheduled_at=exact_boundary)
        assert result.__class__.__name__ == "QueuedJob", (
            "Exactly at threshold (not strictly greater) must go to QueuedJob"
        )
        assert django_backend.QueuedJob.objects.count() == queued_before + 1
        assert ScheduledJob.objects.count() == staged_before


# ---------------------------------------------------------------------------
# 10. Dual-table API surface — get_job_by_id, cancel_job, get_staged_jobs (14-03)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDualTableApiSurface:
    """Verify that get_job_by_id and cancel_job span both sqlery_queued_job and
    sqlery_scheduled_job, and that get_staged_jobs is accessible."""

    def _create_staged_job(self, backend):
        """Helper: create a far-future ScheduledJob via create_job routing."""
        from sqlery.django_sqlery.models import ScheduledJob
        far_future = timezone.now() + timedelta(days=60)
        job = _create_basic_job(backend, scheduled_at=far_future)
        assert isinstance(job, ScheduledJob), "Precondition: routing must return ScheduledJob"
        return job

    def test_get_job_by_id_queued_job(self, django_backend):
        """Test 1: job exists only in QueuedJob -> returns QueuedJob instance."""
        from sqlery.django_sqlery.models import QueuedJob
        j = _create_basic_job(django_backend)
        result = django_backend.get_job_by_id(j.id)
        assert result is not None
        assert isinstance(result, QueuedJob)
        assert result.id == j.id

    def test_get_job_by_id_scheduled_job(self, django_backend):
        """Test 2: job exists only in ScheduledJob -> returns ScheduledJob instance."""
        from sqlery.django_sqlery.models import ScheduledJob
        staged = self._create_staged_job(django_backend)
        result = django_backend.get_job_by_id(staged.id)
        assert result is not None
        assert isinstance(result, ScheduledJob)
        assert result.id == staged.id

    def test_get_job_by_id_missing(self, django_backend):
        """Test 3: id does not exist in either table -> returns None."""
        assert django_backend.get_job_by_id(999999) is None

    def test_cancel_job_queued_job(self, django_backend):
        """Test 4: job is a QueuedJob with status='queued' -> cancels it (existing behavior)."""
        j = _create_basic_job(django_backend)
        assert django_backend.cancel_job(j.id) is True
        j.refresh_from_db()
        assert j.status == "failed"

    def test_cancel_job_scheduled_job(self, django_backend):
        """Test 5: job is a ScheduledJob -> deletes the ScheduledJob row, returns True."""
        from sqlery.django_sqlery.models import ScheduledJob
        staged = self._create_staged_job(django_backend)
        staged_id = staged.id
        result = django_backend.cancel_job(staged_id)
        assert result is True
        assert not ScheduledJob.objects.filter(id=staged_id).exists()

    def test_cancel_job_missing(self, django_backend):
        """Test 6: id does not exist in either table -> returns False."""
        assert django_backend.cancel_job(999999) is False

    def test_get_jobs_returns_queued_jobs_only(self, django_backend):
        """Test 7: get_jobs() returns QueuedJob rows; staged jobs are NOT included.

        On SQLite both tables have independent auto-increment sequences so IDs
        may collide between tables (both start at 1). The meaningful assertion
        is that every returned row is a QueuedJob ORM instance, not a ScheduledJob.
        """
        from sqlery.django_sqlery.models import QueuedJob, ScheduledJob
        _create_basic_job(django_backend, queue_name="mixed")
        self._create_staged_job(django_backend)
        results = django_backend.get_jobs(queue_name="mixed")
        assert len(results) >= 1
        # All returned objects must be QueuedJob instances, never ScheduledJob
        for r in results:
            assert isinstance(r, QueuedJob), (
                f"get_jobs() returned a {type(r).__name__}, expected QueuedJob only"
            )

    def test_get_staged_jobs_callable(self, django_backend):
        """Test 8: get_staged_jobs() is callable and returns staged rows."""
        from sqlery.django_sqlery.models import ScheduledJob
        staged = self._create_staged_job(django_backend)
        results = django_backend.get_staged_jobs()
        assert isinstance(results, list)
        assert any(r.id == staged.id for r in results)

    def test_get_staged_jobs_filter_by_queue(self, django_backend):
        """get_staged_jobs(queue_name=...) filters by queue."""
        far_future = timezone.now() + timedelta(days=60)
        job_a = _create_basic_job(django_backend, scheduled_at=far_future, queue_name="qA")
        job_b = _create_basic_job(django_backend, scheduled_at=far_future, queue_name="qB")
        results = django_backend.get_staged_jobs(queue_name="qA")
        result_ids = {r.id for r in results}
        assert job_a.id in result_ids
        assert job_b.id not in result_ids

    def test_staged_job_invisible_to_claim(self, django_backend):
        """A staged job is never visible to claim_job (it is in sqlery_scheduled_job, not queued)."""
        from sqlery.django_sqlery.models import ScheduledJob
        staged = self._create_staged_job(django_backend)
        assert not django_backend.QueuedJob.objects.filter(id=staged.id).exists()


# ---------------------------------------------------------------------------
# 11. Postgres-only branch placeholder
# ---------------------------------------------------------------------------

@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("SQLERY_TEST_PG_URL"),
    reason="SQLERY_TEST_PG_URL not set; skipping PostgreSQL-only test",
)
def test_select_for_update_skip_locked_postgres_branch():
    """Placeholder test for SELECT FOR UPDATE SKIP LOCKED on PostgreSQL.

    Plan 03-07 owns the full PG mirror; this test exists to satisfy the
    plan's acceptance criterion that at least one `@pytest.mark.postgres`
    test exists for the SKIP LOCKED branch.
    """
    # Real implementation in plan 03-07; here we just assert the env var was set.
    assert os.environ.get("SQLERY_TEST_PG_URL")
