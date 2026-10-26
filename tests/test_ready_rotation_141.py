"""Ready-queue rotation (#141): the decisions of the protocol, and what real interleavings do.

Pure decision tests state a scenario and the action the rotator (or a writer) must take; the
PostgreSQL scenarios assert outcomes of real interleavings ("in this scenario no job is lost
or claimed twice"). Design: docs/design/queue-robustness.md, section "#141 Rotation".
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace

import pytest

from sqlery.core import rotation as rot
from sqlery.core.rotation import (
    DRAIN,
    IDLE,
    RECLAIM,
    RETRY,
    SWITCH,
    RotationConfig,
    RotationState,
    decide_rotation_step,
)
from tests.pg_url import sqlalchemy_pg_url

PG_URL = os.environ.get("SQLERY_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="SQLERY_TEST_PG_URL not set")

CLEAN = (False, False, False)


def state(gen=3, nonempty=CLEAN, dirty=None, elapsed=True, **kw):
    """Generation 3 -> current slot 0, previous slot 2, next slot 1."""
    return RotationState(
        generation=gen,
        nonempty=nonempty,
        dirty=nonempty if dirty is None else dirty,
        now=1000.0,
        last_switch=0.0 if elapsed else 990.0,
        interval=60.0,
        **kw,
    )


# ---- pure decisions: the rotator ------------------------------------------------------------
def test_next_slot_not_empty_means_no_switch_but_drain():
    s = state(nonempty=(False, True, False))  # slot 1 is the next slot
    assert decide_rotation_step(s) == rot.Action(DRAIN, 1)


def test_previous_slot_not_empty_is_drained_first():
    s = state(nonempty=(False, True, True))
    assert decide_rotation_step(s).slot == 2


def test_switch_lock_not_acquired_means_no_switch_and_retry():
    action = decide_rotation_step(state(switch_lock=False))
    assert action.kind == RETRY and action.kind != SWITCH


def test_switch_when_interval_elapsed_and_ring_is_clean():
    assert decide_rotation_step(state()).kind == SWITCH


def test_interval_not_elapsed_means_no_switch():
    assert decide_rotation_step(state(elapsed=False)).kind == IDLE


def test_first_ever_tick_may_switch():
    assert decide_rotation_step(replace(state(), last_switch=None)).kind == SWITCH


def test_draining_slot_not_empty_means_drain():
    assert decide_rotation_step(state(nonempty=(False, False, True))) == rot.Action(DRAIN, 2)


def test_draining_slot_empty_but_not_reclaimed_means_reclaim():
    s = state(nonempty=CLEAN, dirty=(False, False, True))
    assert decide_rotation_step(s) == rot.Action(RECLAIM, 2)


def test_reclaim_lock_failure_means_retry_and_no_reclaim():
    s = state(dirty=(False, False, True), reclaim_lock=False)
    assert decide_rotation_step(s).kind == RETRY


def test_unreclaimed_slot_stops_the_ring_from_switching():
    # Falling behind: the switch waits for the reclaim even though the interval has elapsed.
    s = state(dirty=(False, True, False), reclaim_lock=False)
    assert decide_rotation_step(s).kind != SWITCH


def test_current_slot_content_never_triggers_drain_or_reclaim():
    s = state(nonempty=(True, False, False), elapsed=False)
    assert decide_rotation_step(s).kind == IDLE


def test_decision_depends_only_on_the_derived_state():
    # A crash between steps loses nothing: the restarted rotator re-derives the same state
    # and decides the same next action.
    s = state(nonempty=(False, False, True))
    assert decide_rotation_step(s) == decide_rotation_step(replace(s))


class FakeIO:
    """In-memory mirror of the protocol steps (same semantics as ``SqlRotationIO``), with a
    crash that fires after the n-th mutating step and a writer / reader that can block."""

    def __init__(self, rows: int, crash_after: int | None = None):
        self.gen = 3
        self.rows = [rows, 0, 0]  # entries per slot
        self.dirty = [rows > 0, False, False]
        self.now = 1000.0
        self.last_switch: float | None = None
        self.steps = 0
        self.crash_after = crash_after
        self.total = rows

    def _step(self):
        self.steps += 1
        if self.crash_after is not None and self.steps == self.crash_after:
            self.crash_after = None
            raise RuntimeError("crash")

    def read_state(self, interval):
        nonempty = tuple(r > 0 for r in self.rows)
        dirty = tuple(d or n for d, n in zip(self.dirty, nonempty))
        return RotationState(
            self.gen, nonempty, dirty, self.now, self.last_switch, interval  # type: ignore
        )

    def switch(self, interval):
        if decide_rotation_step(self.read_state(interval)).kind != SWITCH:
            return "skipped"
        self.gen += 1
        self.last_switch = self.now
        self._step()
        return "switched"

    def drain(self, slot, batch):
        cur = self.gen % 3
        if slot == cur:
            return 0
        n = min(batch, self.rows[slot])
        self.rows[slot] -= n
        self.rows[cur] += n
        self.dirty[cur] = True
        self._step()
        return n

    def reclaim(self, slot, batch):
        cur = self.gen % 3
        if slot == cur:
            return False
        self.rows[cur] += self.rows[slot]
        self.rows[slot] = 0
        self.dirty[slot] = False
        self._step()
        return True


@pytest.mark.parametrize("crash_after", range(1, 6))
def test_crash_at_any_step_loses_no_entry_and_the_next_tick_continues(crash_after):
    io = FakeIO(rows=25, crash_after=crash_after)
    config = RotationConfig(interval=60, drain_batch=10)
    with pytest.raises(RuntimeError):
        for _ in range(10):
            rot.run_rotation_tick(io, config)
    assert sum(io.rows) == io.total  # whatever the crash interrupted, nothing was lost
    io.now += 120
    for _ in range(5):
        rot.run_rotation_tick(io, config)
        io.now += 120
    cur = io.gen % 3
    assert io.rows[cur] == io.total
    assert [r for i, r in enumerate(io.rows) if i != cur] == [0, 0]
    assert not any(d for i, d in enumerate(io.dirty) if i != cur)


def test_a_full_cycle_switches_drains_and_reclaims_in_order():
    io = FakeIO(rows=5)
    report = rot.run_rotation_tick(io, RotationConfig(interval=60, drain_batch=2))
    assert report["switched"] == 1 and report["drained"] == 5 and report["reclaimed"] == 1
    assert io.rows[io.gen % 3] == 5 and io.rows.count(0) == 2


# ---- pure decisions: the writers ------------------------------------------------------------
def test_rotation_disabled_writers_use_slot_0_and_take_no_lock():
    calls = []
    slot = rot.writer_slot(lambda sql: calls.append(sql) or 7, False, "postgresql")
    assert slot == 0 and calls == []
    assert rot.plan_writer(False, "postgresql") == rot.WriterPlan(fenced=False, slot=0)


def test_rotation_never_applies_off_postgresql():
    assert not rot.plan_writer(True, "sqlite").fenced


def test_enabled_writer_locks_before_it_reads_the_generation():
    rot.reset_probe()
    calls = []

    def read_one(sql):
        calls.append(sql)
        return 8 if sql == rot.GEN_SQL else True

    assert rot.writer_slot(read_one, True, "postgresql") == 8 % 3
    assert calls.index(rot.LOCK_SHARED_SQL) < calls.index(rot.GEN_SQL)
    assert "advisory_xact_lock_shared" in rot.LOCK_SHARED_SQL


def test_missing_sequence_never_fails_a_writer():
    rot.reset_probe()
    assert rot.writer_slot(lambda sql: False, True, "postgresql") == 0
    rot.reset_probe()


def test_lock_key_follows_the_partitioning_convention():
    assert rot.ROTATION_LOCK_KEY == int.from_bytes(b"SQLEROTA", "big")


# ---- pure decisions: health -----------------------------------------------------------------
def test_rotation_lag_past_threshold_degrades_health():
    assert rot.assess_rotation(True, 100, 300) == rot.OK
    assert rot.assess_rotation(True, 301, 300) == rot.DEGRADED
    assert rot.assess_rotation(True, None, 300) == rot.UNKNOWN
    assert rot.assess_rotation(False, 10**6, 300) == rot.OK


def test_lag_counts_from_the_last_reclaim_else_from_the_last_switch():
    assert rot.rotation_lag(100, 40, 90) == 60
    assert rot.rotation_lag(100, None, 90) == 10
    assert rot.rotation_lag(100, None, None) is None


def test_settings_are_read_with_safe_defaults():
    cfg = rot.config_from_getter(lambda n, d=None: {"READY_ROTATION_ENABLED": "true"}.get(n, d))
    assert cfg.enabled and cfg.interval == 60 and cfg.lag_threshold == 240
    assert not rot.config_from_getter(lambda n, d=None: d).enabled


# ---- PostgreSQL scenarios ---------------------------------------------------------------------
TASK = dict(task_path="operator.add", kwargs={"_args": [1, 2]})


@pytest.fixture
def pg(monkeypatch):
    from sqlalchemy import text

    from sqlery.core import ready_sql
    from sqlery.fastapi_sqlery import database as db_mod
    from sqlery.fastapi_sqlery.backend import SQLAlchemyBackend

    db_mod._engine = None
    db_mod.init_database(sqlalchemy_pg_url(PG_URL))
    engine = db_mod.get_engine()
    monkeypatch.setattr(ready_sql, "_rotation_enabled", lambda: True)
    rot.reset_probe()
    try:
        yield SQLAlchemyBackend(), engine, rot.SqlRotationIO(rot.sqlalchemy_tx)
    finally:
        rot.reset_probe()
        engine.dispose()
        with db_mod.get_engine().connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
            conn.commit()
        db_mod._engine = None


def _new_job(backend, **kw):
    return backend.create_job(
        queue_name="default",
        priority=0,
        scheduled_at=None,
        max_retries=0,
        retry_backoff=1.0,
        allow_parallel=False,
        timeout_seconds=None,
        **TASK,
        **kw,
    )


def _claim_and_finish(backend):
    from sqlery.core.attempts import attempt_of

    job = backend.claim_job(["default"], "trigger-rot")
    if job is not None:
        backend.mark_job_success(job.id, output="3", attempt=attempt_of(job))
    return job


def _slots(engine):
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT slot, job_id FROM sqlery_job_ready ORDER BY job_id")
        ).fetchall()
    return {(s, j) for s, j in rows}


def _size(engine, slot):
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(text(f"SELECT pg_relation_size('{rot.partition_name(slot)}')")).scalar()


def _generation(engine):
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(text(rot.GEN_SQL)).scalar()


@needs_pg
def test_enabled_writer_inserts_into_the_current_slot(pg):
    backend, engine, io = pg
    assert io.switch(0) == "switched"
    gen = _generation(engine)
    job = _new_job(backend)
    assert _slots(engine) == {(gen % 3, job.id)}


@needs_pg
def test_open_writer_of_the_old_generation_blocks_the_switch_and_is_drained_after_commit(pg):
    from sqlalchemy import text

    backend, engine, io = pg
    gen = _generation(engine)
    old = gen % 3
    writer = engine.connect()
    tx = writer.begin()
    slot = rot.writer_slot(lambda sql: writer.execute(text(sql)).scalar(), True, "postgresql")
    assert slot == old
    writer.execute(
        text(
            "INSERT INTO sqlery_job_ready (slot, job_id, queue_name, priority, created_at, "
            "scheduled_at) VALUES (:s, 42, 'q', 7, '2026-01-02T03:04:05+00', "
            "'2026-02-03T00:00:00+00')"
        ),
        {"s": slot},
    )
    try:
        assert io.switch(0) == "busy"  # try-lock fails: the generation did not move
        assert _generation(engine) == gen
    finally:
        tx.commit()
        writer.close()
    assert io.switch(0) == "switched"
    new = _generation(engine) % 3
    assert new != old
    assert _slots(engine) == {(old, 42)}  # still where the writer put it, still claimable
    assert io.drain(old, 100) == 1
    assert io.reclaim(old, 100) is True
    assert _slots(engine) == {(new, 42)}
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT queue_name, priority, created_at, scheduled_at FROM sqlery_job_ready")
        ).one()
    assert row[0] == "q" and row[1] == 7
    assert row[2].isoformat().startswith("2026-01-02T03:04:05")
    assert row[3].isoformat().startswith("2026-02-03T00:00:00")


@needs_pg
def test_repeatable_read_reader_of_the_partition_makes_reclaim_fail_and_nothing_is_truncated(pg):
    from sqlalchemy import text

    backend, engine, io = pg
    old = _generation(engine) % 3
    job = _new_job(backend)
    assert io.switch(0) == "switched"
    reader = engine.connect()
    tx = reader.begin()
    reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    reader.execute(text(f"SELECT count(*) FROM {rot.partition_name(old)}")).scalar()
    try:
        assert io.reclaim(old, 100) is False  # NOWAIT: never waits for the reader
        assert _slots(engine) == {(old, job.id)}  # the entry is still there
    finally:
        tx.rollback()
        reader.close()
    assert io.drain(old, 100) == 1
    assert io.reclaim(old, 100) is True
    assert _slots(engine) == {(_generation(engine) % 3, job.id)}
    assert _claim_and_finish(backend).id == job.id  # and it is still claimable


@needs_pg
def test_full_cycle_with_concurrent_enqueue_and_claim_loses_and_duplicates_no_job(pg):
    backend, engine, io = pg
    config = RotationConfig(interval=0, drain_batch=7)
    total = 80
    created: list[int] = []
    claimed: list[int] = []
    stop = threading.Event()
    errors: list[BaseException] = []

    def producer():
        try:
            for _ in range(total):
                created.append(_new_job(backend).id)
                time.sleep(0.005)
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    def consumer():
        try:
            deadline = time.time() + 90
            while time.time() < deadline and len(claimed) < total:
                job = _claim_and_finish(backend)
                if job is None:
                    time.sleep(0.01)
                else:
                    claimed.append(job.id)
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    def rotator():
        try:
            while not stop.is_set():
                rot.run_rotation_tick(io, config)
                time.sleep(0.01)
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    start_gen = _generation(engine)
    threads = [threading.Thread(target=f) for f in (producer, consumer, consumer, rotator)]
    for t in threads:
        t.start()
    for t in threads[:3]:
        t.join(100)
    stop.set()
    threads[3].join(30)
    assert not errors, errors
    assert sorted(claimed) == sorted(created) and len(set(claimed)) == total
    assert _generation(engine) - start_gen >= 3  # it really rotated meanwhile
    rot.run_rotation_tick(io, config)
    assert _slots(engine) == set()


@needs_pg
def test_pinned_snapshot_on_an_unrelated_table_does_not_stop_the_storage_being_reclaimed(pg):
    from sqlalchemy import text

    backend, engine, io = pg
    old = _generation(engine) % 3
    pin = engine.connect()
    tx = pin.begin()
    pin.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    pin.execute(text("SELECT count(*) FROM sqlery_worker")).scalar()  # snapshot taken, held
    try:
        ids = [_new_job(backend).id for _ in range(30)]
        for _ in range(27):
            _claim_and_finish(backend)  # 27 dead entries VACUUM could not remove now
        assert _size(engine, old) > 0
        rot.run_rotation_tick(io, RotationConfig(interval=0, drain_batch=100))
        new = _generation(engine) % 3
        assert new != old
        assert _size(engine, old) == 0  # TRUNCATE: reclaimed although the horizon is pinned
        assert len(_slots(engine)) == 3 and {s for s, _ in _slots(engine)} == {new}
        assert {j for _, j in _slots(engine)} <= set(ids)
    finally:
        tx.rollback()
        pin.close()


@needs_pg
@pytest.mark.django_db(transaction=True)
def test_django_writers_follow_the_generation_and_the_rotator_cycles(settings):
    from django.db import connection

    from sqlery.django_sqlery.backend import DjangoBackend
    from sqlery.django_sqlery.models import JobReady

    if connection.vendor != "postgresql":
        pytest.skip("Django is not on PostgreSQL")
    settings.DJANGO_SQL_JOBS = {**settings.DJANGO_SQL_JOBS, "READY_ROTATION_ENABLED": True}
    rot.reset_probe()
    io = rot.SqlRotationIO(rot.django_tx)
    config = RotationConfig(enabled=True, interval=0, drain_batch=10)
    backend = DjangoBackend()
    first = _new_job(backend)
    assert list(JobReady.objects.values_list("slot", flat=True)) == [0]
    report = rot.run_rotation_tick(io, config)
    assert report["switched"] == 1 and report["drained"] == 1 and report["reclaimed"] == 1
    cur = io.read_state(0).current
    second = _new_job(backend)
    assert set(JobReady.objects.values_list("slot", "job_id")) == {
        (cur, first.id),
        (cur, second.id),
    }
    with connection.cursor() as cur_:
        cur_.execute("SELECT pg_relation_size('sqlery_job_ready_0')")
        assert cur_.fetchone()[0] == 0
    rot.reset_probe()
