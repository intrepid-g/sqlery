"""Solar schedules (stored in cron_expression) and revoke (queued + running)."""

import datetime as dt
import threading
import time

import pytest

from sqlery import revoke
from sqlery.core.job import job
from sqlery.core.result import AsyncResult, TaskRevokedError, apply_async_job
from sqlery.core.worker import WorkerProcess
from sqlery.crontab import InvalidExpression, next_cron_occurrence, parse_cron_string
from sqlery.solar import InvalidSolarSpec, parse_solar, solar_spec

UTC = dt.timezone.utc


# ---- solar -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec,day,expected",
    [  # published almanac times (UTC); the algorithm is good to ~1-2 minutes
        ("solar:sunrise 51.4769 -0.0005", dt.date(2024, 6, 21), dt.datetime(2024, 6, 21, 3, 43, tzinfo=UTC)),
        ("solar:sunset 51.4769 -0.0005", dt.date(2024, 6, 21), dt.datetime(2024, 6, 21, 20, 21, tzinfo=UTC)),
        ("solar:sunrise 40.7128 -74.0060", dt.date(2024, 12, 21), dt.datetime(2024, 12, 21, 12, 17, tzinfo=UTC)),
        ("solar:sunset 40.7128 -74.0060", dt.date(2024, 12, 21), dt.datetime(2024, 12, 21, 21, 32, tzinfo=UTC)),
        ("solar:sunset -33.8688 151.2093", dt.date(2024, 12, 21), dt.datetime(2024, 12, 21, 9, 5, tzinfo=UTC)),
        ("solar:sunrise 35.6762 139.6503", dt.date(2024, 3, 20), dt.datetime(2024, 3, 19, 20, 46, tzinfo=UTC)),
    ],
)
def test_solar_matches_almanac(spec, day, expected):
    got = parse_solar(spec).event_on(day)
    assert abs((got - expected).total_seconds()) <= 180


def test_dawn_precedes_sunrise_and_noon_between():
    day = dt.date(2024, 9, 1)
    ev = {e: parse_solar(f"solar:{e} 48.85 2.35").event_on(day) for e in
          ("dawn", "sunrise", "solar_noon", "sunset", "dusk")}
    assert ev["dawn"] < ev["sunrise"] < ev["solar_noon"] < ev["sunset"] < ev["dusk"]


def test_next_occurrence_is_strictly_after_base_and_applies_offset():
    base = dt.datetime(2024, 12, 21, 12, 0, tzinfo=UTC)
    plain = next_cron_occurrence("solar:sunset 40.7128 -74.0060", base)
    early = next_cron_occurrence("solar:sunset 40.7128 -74.0060 -15m", base)
    assert plain - early == dt.timedelta(minutes=15)
    assert plain.second == 0 and plain > base
    # asking again from the occurrence itself yields the next day, not the same instant
    again = next_cron_occurrence("solar:sunset 40.7128 -74.0060", plain)
    assert dt.timedelta(hours=23) < again - plain < dt.timedelta(hours=25)


def test_polar_night_skips_to_next_sunrise():
    nxt = next_cron_occurrence("solar:sunrise 78.2 15.6", dt.datetime(2024, 12, 21, 12, 0, tzinfo=UTC))
    assert nxt.month == 2  # Svalbard's sun returns mid-February


@pytest.mark.parametrize("bad", [
    "solar:", "solar:sunrise", "solar:midnight 1 1", "solar:sunrise 91 0", "solar:sunrise 0 181",
    "solar:sunrise x y", "solar:sunrise 1 1 soon", "solar:sunrise 1 1 +5m extra",
])
def test_bad_specs_are_rejected_as_invalid_cron(bad):
    with pytest.raises(InvalidExpression):
        parse_cron_string(bad)
    with pytest.raises(InvalidSolarSpec):
        parse_solar(bad)


def test_helper_builds_valid_spec_and_normal_cron_unaffected():
    spec = solar_spec("sunrise", 52.52, 13.405, offset_minutes=-30)
    assert spec == "solar:sunrise 52.52 13.405 -30m"
    assert parse_solar(spec).offset_seconds == -1800
    assert parse_cron_string("*/10 * * * *") is not None


@pytest.mark.django_db
def test_django_scheduled_task_accepts_solar_cron_expression():
    from sqlery.models import ScheduledTask

    t = ScheduledTask(name="dusk-report", task_path="tests.tasks.dummy_task",
                      schedule_type="cron", cron_expression="solar:dusk 48.85 2.35 +10m")
    t.full_clean()  # runs validate_cron_expression
    t.save()
    assert t.next_run_at > dt.datetime.now(UTC)
    t.cron_expression = "solar:dusk 99 2.35"
    with pytest.raises(Exception):
        t.full_clean()


def test_core_scheduler_computes_future_next_run_for_solar():
    from sqlery.core.scheduler import Scheduler

    nxt = Scheduler.calculate_next_run(
        object.__new__(Scheduler), "solar:sunrise 52.52 13.405",
        base_time=dt.datetime.now(UTC) - dt.timedelta(days=30))  # stale base is clamped forward
    assert nxt > dt.datetime.now(UTC)


# ---- revoke ----------------------------------------------------------------------------
@job
def sleeper(seconds):
    time.sleep(seconds)
    return "finished"


@pytest.fixture
def backend(tmp_path):
    from sqlery.fastapi_sqlery import database
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    database.init_database(f"sqlite:///{tmp_path / 'rv.db'}")
    b = SQLAlchemyBackend()
    import sqlery.compat as compat

    compat._backend = b
    yield b
    compat._backend = None


def test_revoke_queued_job(backend):
    res = apply_async_job(sleeper.task_path, {"seconds": 1}, backend=backend)
    assert revoke(res.id, backend=backend) is True
    assert res.state == "REVOKED" and res.ready()
    with pytest.raises(TaskRevokedError):
        res.get(timeout=1)
    assert backend.claim_job(["default"], "w") is None
    assert revoke(res.id, backend=backend) is False  # already finished


def test_running_job_needs_terminate_flag(backend):
    res = apply_async_job(sleeper.task_path, {"seconds": 1}, backend=backend)
    backend.claim_job_by_id(res.id, "w")
    assert res.revoke() is False                       # running: plain revoke is a no-op
    assert backend.is_job_revoke_requested(res.id) is False
    assert res.revoke(terminate=True) is True
    assert backend.is_job_revoke_requested(res.id) is True


def test_worker_kills_running_job_when_revoked(backend):
    res = apply_async_job(sleeper.task_path, {"seconds": 60}, backend=backend)
    claimed = backend.claim_job_by_id(res.id, "w")
    worker = WorkerProcess(queues=["default"], backend=backend)

    threading.Timer(1.5, lambda: res.revoke(terminate=True)).start()
    start = time.monotonic()
    outcome = worker._fork_and_execute(claimed)
    elapsed = time.monotonic() - start

    assert elapsed < 15, "child was not killed by the revoke request"
    assert outcome["success"] is False and "Revoked" in outcome["error"]
    assert res.state == "REVOKED"
    assert backend.get_latest_retry(res.id) is None    # revoked jobs are not retried


@pytest.mark.django_db
def test_django_backend_revoke_flag_and_states():
    from sqlery.compat import get_backend
    from sqlery.models import QueuedJob

    backend = get_backend()
    queued = apply_async_job("tests.tasks.dummy_task", {})
    assert revoke(queued.id) is True and queued.state == "REVOKED"

    from tests.running_jobs import django_running_job

    running = django_running_job(task_path="tests.tasks.dummy_task", queue_name="default")
    res = AsyncResult(running.id)
    assert res.revoke() is False and backend.is_job_revoke_requested(running.id) is False
    assert res.revoke(terminate=True) is True
    assert backend.is_job_revoke_requested(running.id) is True
    assert backend.request_job_revoke(queued.id) is False  # only running jobs can be flagged
