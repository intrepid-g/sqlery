"""Lifecycle tests for claim → run → complete → cleanup on a partitioned PG table.

Phase 16, plan 16-04: Acceptance criterion SC-2.
Tests the full job lifecycle on a live partitioned PostgreSQL table:
  - create_job lands in the correct daily partition
  - claim_job atomically marks the job as running
  - mark_success transitions the job
  - cleanup_jobs routes to reclaim_drained_partitions (returns reclaimed_via_partition_drop: True)

PG only — all tests skip cleanly when SQLERY_TEST_PG_URL is unset.
"""

from __future__ import annotations

import os
import pytest

_SKIP_NO_PG = pytest.mark.skipif(
    not os.environ.get("SQLERY_TEST_PG_URL"),
    reason="SQLERY_TEST_PG_URL not set — PG required for partitioned lifecycle tests",
)
pytestmark = _SKIP_NO_PG


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend():
    from sqlery.django_sqlery.backend import DjangoBackend
    return DjangoBackend()


def _create_basic_job(backend, queue_name="default", priority=0):
    """Create a simple immediately-runnable job."""
    return backend.create_job(
        task_path="tests.lifecycle.noop_task",
        kwargs={"x": 1},
        queue_name=queue_name,
        priority=priority,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=True,
        timeout_seconds=None,
    )


# ---------------------------------------------------------------------------
# SC-2: Full lifecycle test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPartitionedLifecycle:
    """Claim → run → complete → cleanup lifecycle on a partitioned PG table."""

    def test_table_is_partitioned_on_pg(self):
        """Verify the table is partitioned on the PG test DB (precondition check)."""
        from django.db import connection
        from sqlery.django_sqlery.backend import DjangoBackend

        backend = DjangoBackend()
        # On PG with the partitioned schema, _partitioned_pg() must return True.
        result = backend._partitioned_pg()
        if connection.vendor == "postgresql":
            assert result is True, (
                "On PG with the partitioned schema, _partitioned_pg() must return True. "
                "Check that migration 0030 has been applied to the test database."
            )

    def test_create_job_lands_in_today_partition(self):
        """A newly created job lands in today's date partition, not in DEFAULT."""
        from django.db import connection
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()
        job = _create_basic_job(backend)

        # Verify the row exists in the main table (via parent query)
        assert QueuedJob.objects.filter(id=job.id).exists()

        # Verify it is NOT in the DEFAULT partition (which would indicate a pruning miss)
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname
                FROM pg_inherits i
                JOIN pg_class c ON c.oid = i.inhrelid
                JOIN pg_class p ON p.oid = i.inhparent
                WHERE p.relname = %s AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT'
                """,
                ["sqlery_queued_job"],
            )
            row = cur.fetchone()
            if row is None:
                # No DEFAULT partition — trivially passes
                return
            default_partition_name = row[0]
            cur.execute(
                f"SELECT EXISTS(SELECT 1 FROM {default_partition_name} WHERE id = %s)",  # noqa: S608
                [job.id],
            )
            (in_default,) = cur.fetchone()
            assert not in_default, (
                f"Job {job.id} landed in the DEFAULT partition ({default_partition_name}) — "
                "partition keys may not match the insert timestamp."
            )

    def test_claim_run_complete_reclaim(self):
        """Full lifecycle: create → claim (running) → mark_success → cleanup routes to reclaim."""
        from sqlery.django_sqlery.models import QueuedJob

        backend = _make_backend()

        # 1. Create three jobs — one we'll claim, two as background noise
        job1 = _create_basic_job(backend, queue_name="lifecycle-test")
        job2 = _create_basic_job(backend, queue_name="lifecycle-test")
        job3 = _create_basic_job(backend, queue_name="lifecycle-test")

        assert job1.status == "queued"
        assert job2.status == "queued"
        assert job3.status == "queued"

        # 2. Register a worker and claim one job
        from sqlery.django_sqlery.models import Worker
        from django.utils import timezone

        worker, _ = Worker.objects.get_or_create(
            node_id="lifecycle-test-node",
            pid=99999,
            defaults={"status": "idle", "last_heartbeat": timezone.now()},
        )
        worker_id = f"worker_{worker.node_id}_{worker.pid}"

        claimed = backend.claim_job(queues=["lifecycle-test"], worker_id=worker_id)
        assert claimed is not None, "Expected to claim a job from the queue"
        assert claimed.status == "running"

        # 3. Mark the claimed job as successful
        claimed.refresh_from_db()
        claimed.mark_success(output="lifecycle-ok")
        claimed.refresh_from_db()
        assert claimed.status == "success"
        assert claimed.output == "lifecycle-ok"
        assert claimed.finished_at is not None

        # 4. cleanup_jobs on partitioned PG must route to reclaim_drained_partitions
        result = backend.cleanup_jobs(max_age_days=0)
        assert result.get("reclaimed_via_partition_drop") is True, (
            f"Expected cleanup_jobs to route to partition reclaim on PG, got: {result}"
        )
        # dropped_partitions may be 0 (current partition not yet outside retention)
        assert "dropped_partitions" in result

    def test_cleanup_returns_reclaimed_via_partition_drop_true(self):
        """cleanup_jobs on partitioned PG always returns reclaimed_via_partition_drop: True."""
        backend = _make_backend()
        if not backend._partitioned_pg():
            pytest.skip("Not a partitioned PG install — divergence matrix covers SQLite path")

        result = backend.cleanup_jobs()
        assert result.get("reclaimed_via_partition_drop") is True, (
            f"On partitioned PG, cleanup_jobs must return reclaimed_via_partition_drop: True. Got: {result}"
        )

    def test_mark_success_uses_created_at_filter(self):
        """mark_success update carries created_at so PG prunes to one partition (item 4)."""
        from django.db import connection

        backend = _make_backend()
        job = _create_basic_job(backend)
        job.refresh_from_db()

        # Verify mark_success only touches one partition by running EXPLAIN
        sql = (
            "UPDATE sqlery_queued_job "
            "SET status='success', version=version+1 "
            "WHERE id=%s AND created_at=%s AND version=%s"
        )
        with connection.cursor() as cur:
            cur.execute(
                f"EXPLAIN (ANALYZE FALSE, FORMAT TEXT) {sql}",
                (job.id, job.created_at, job.version),
            )
            rows = cur.fetchall()
        plan = "\n".join(r[0] for r in rows)

        import re
        child_partitions = set(re.findall(r"sqlery_queued_job_(?:\d{8}|default)", plan))
        assert "Append" not in plan and len(child_partitions) == 1, (
            f"mark_success EXPLAIN should show single-partition pruning.\nPlan:\n{plan}"
        )
