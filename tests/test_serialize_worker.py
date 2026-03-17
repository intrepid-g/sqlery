"""Tests for _serialize_worker() extracted from dashboard_stats."""

import pytest
from datetime import timedelta
from django.utils import timezone
from sqlery.models import QueuedJob, Worker


# Import the function under test from the views module
from sqlery.django_sqlery.views import _serialize_worker


@pytest.mark.django_db
class TestSerializeWorkerUtilization:
    """Test uptime/busy/idle/utilization calculations."""

    def test_utilization_with_busy_seconds(self):
        """Worker with uptime and busy seconds produces correct utilization_pct."""
        now = timezone.now()
        worker = Worker.objects.create(
            node_id="test-node",
            pid=1000,
            status="idle",
            total_busy_seconds=50.0,
        )
        # Override started_at (auto_now_add) to control uptime
        Worker.objects.filter(pk=worker.pk).update(started_at=now - timedelta(seconds=100))
        worker.refresh_from_db()

        result = _serialize_worker(worker, now)

        assert result['uptime_seconds'] == pytest.approx(100.0, abs=1.0)
        assert result['busy_seconds'] == pytest.approx(50.0, abs=1.0)
        assert result['idle_seconds'] == pytest.approx(50.0, abs=1.0)
        assert result['utilization_pct'] == pytest.approx(50.0, abs=1.0)

    def test_no_started_at_yields_none_fields(self):
        """Worker with no started_at returns None for uptime/busy/idle/utilization."""
        now = timezone.now()
        worker = Worker.objects.create(
            node_id="test-node",
            pid=1001,
            status="idle",
        )
        # started_at is auto_now_add (NOT NULL in DB), so patch the instance
        worker.started_at = None

        result = _serialize_worker(worker, now)

        assert result['uptime_seconds'] is None
        assert result['busy_seconds'] is None
        assert result['idle_seconds'] is None
        assert result['utilization_pct'] is None


@pytest.mark.django_db
class TestSerializeWorkerHeartbeat:
    """Test heartbeat age and stalled detection."""

    def test_stale_heartbeat_is_stalled(self):
        """Worker with heartbeat older than 60s is marked stalled."""
        now = timezone.now()
        worker = Worker.objects.create(
            node_id="test-node",
            pid=2000,
            status="idle",
        )
        # Set heartbeat to 90 seconds ago
        Worker.objects.filter(pk=worker.pk).update(last_heartbeat=now - timedelta(seconds=90))
        worker.refresh_from_db()

        result = _serialize_worker(worker, now)

        assert result['is_stalled'] is True
        assert result['heartbeat_age_seconds'] == pytest.approx(90.0, abs=1.0)

    def test_fresh_heartbeat_not_stalled(self):
        """Worker with recent heartbeat is not stalled."""
        now = timezone.now()
        worker = Worker.objects.create(
            node_id="test-node",
            pid=2001,
            status="idle",
        )
        # last_heartbeat is auto_now, so it should be very recent
        worker.refresh_from_db()

        result = _serialize_worker(worker, now)

        assert result['is_stalled'] is False
        assert result['heartbeat_age_seconds'] < 5.0


@pytest.mark.django_db
class TestSerializeWorkerCurrentJob:
    """Test current job serialization and status overrides."""

    def test_non_running_job_overrides_status_to_idle(self):
        """When current_job status != 'running', worker status becomes 'idle' and current_job = None."""
        now = timezone.now()
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.finished_task",
            status="success",
        )
        worker = Worker.objects.create(
            node_id="test-node",
            pid=3000,
            status="busy",
            current_job=job,
        )

        result = _serialize_worker(worker, now)

        assert result['status'] == 'idle'
        assert result['current_job'] is None

    def test_running_job_past_timeout(self):
        """Running job that exceeds timeout_seconds has is_timeout = True."""
        now = timezone.now()
        job = QueuedJob.objects.create(
            task_path="myapp.tasks.slow_task",
            status="running",
            started_at=now - timedelta(seconds=120),
            timeout_seconds=60,
        )
        worker = Worker.objects.create(
            node_id="test-node",
            pid=3001,
            status="busy",
            current_job=job,
        )

        result = _serialize_worker(worker, now)

        assert result['current_job'] is not None
        assert result['current_job']['is_timeout'] is True
        assert result['current_job']['elapsed_seconds'] == pytest.approx(120.0, abs=1.0)

    def test_no_current_job_id(self):
        """Worker with no current_job_id has current_job = None."""
        now = timezone.now()
        worker = Worker.objects.create(
            node_id="test-node",
            pid=3002,
            status="idle",
        )

        result = _serialize_worker(worker, now)

        assert result['current_job'] is None


@pytest.mark.django_db
class TestSerializeWorkerPauseState:
    """Test pause state detection."""

    def test_paused_until_future(self):
        """Worker paused until a future time has is_paused = True."""
        now = timezone.now()
        worker = Worker.objects.create(
            node_id="test-node",
            pid=4000,
            status="idle",
            paused_until=now + timedelta(minutes=10),
        )

        result = _serialize_worker(worker, now)

        assert result['is_paused'] is True
        assert result['paused_until'] is not None
