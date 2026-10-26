"""Opt-in rotation of the PostgreSQL ready queue (issue #141).

``sqlery_job_ready`` is partitioned by ``slot`` (0, 1, 2). Without rotation every writer uses
slot 0 and VACUUM has to reclaim the dead entries of claimed jobs, which a pinned xmin
horizon prevents. With ``READY_ROTATION_ENABLED`` the rotator moves the writers to a fresh
slot every ``READY_ROTATION_INTERVAL`` seconds, drains the old slot into the new one and
``TRUNCATE``s it, so the storage is reclaimed without VACUUM. The full protocol, and why each
step is safe, is in ``docs/design/queue-robustness.md`` (section "#141 Rotation").

State is derived, never stored:

* ``generation`` = ``last_value`` of ``sqlery_ready_gen_seq`` (reading a sequence is not MVCC,
  so a REPEATABLE READ writer still sees the current generation); ``current = generation % 3``
  is the slot writers insert into;
* a slot other than ``current`` that holds rows is *draining*, one that is empty but whose
  storage was not yet reclaimed is *dirty*; both are cleaned before the next switch.

Three decisions, all pure and tested without a database:

* ``plan_writer`` / ``writer_slot``: what a writer does (lock + read the generation, or slot 0);
* ``decide_rotation_step``: the next action of the rotator from the derived state;
* ``assess_rotation``: the health verdict from the rotation lag.

The SQL executors below are thin: each protocol step is one short transaction.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator

from ..tables import JOB_READY

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------------
# Names and SQL
# --------------------------------------------------------------------------------------------

# Advisory-lock key (same convention as core/partitioning.py: 8 ASCII bytes as an int8).
ROTATION_LOCK_KEY: int = int.from_bytes(b"SQLEROTA", "big")

SLOT_COUNT = 3
# Slot every writer uses while rotation is off (and what the migrations backfill into).
SLOT_DEFAULT = 0

GEN_SEQUENCE = "sqlery_ready_gen_seq"
# Timestamps (epoch seconds) kept in sequences: non-transactional, so updating them leaves no
# dead tuples, and readable from any process (health) without a table.
SWITCH_TS_SEQUENCE = "sqlery_ready_switch_ts_seq"
RECLAIM_TS_SEQUENCE = "sqlery_ready_reclaim_ts_seq"

LOCK_SHARED_SQL = f"SELECT pg_advisory_xact_lock_shared({ROTATION_LOCK_KEY})"
TRY_LOCK_SQL = f"SELECT pg_try_advisory_xact_lock({ROTATION_LOCK_KEY})"
GEN_SQL = f"SELECT last_value FROM {GEN_SEQUENCE}"
SEQUENCE_PROBE_SQL = f"SELECT to_regclass('{GEN_SEQUENCE}') IS NOT NULL"

SQLSTATE_LOCK_NOT_AVAILABLE = "55P03"

# Seconds before a writer re-checks a sequence that was missing.
_PROBE_RETRY_SECONDS = 30.0


def partition_name(slot: int) -> str:
    return f"{JOB_READY}_{int(slot) % SLOT_COUNT}"


def slot_for_generation(generation: int) -> int:
    return int(generation) % SLOT_COUNT


def postgres_rotation_ddl() -> list[str]:
    """PostgreSQL DDL of the rotation objects (idempotent, safe to re-run)."""
    return [
        f"CREATE SEQUENCE IF NOT EXISTS {GEN_SEQUENCE} START 3",
        # A fresh sequence returns its start value from the first nextval() without
        # advancing past the value a reader sees: mark it called, once.
        f"SELECT setval('{GEN_SEQUENCE}', last_value, true) FROM {GEN_SEQUENCE} "
        "WHERE NOT is_called",
        f"CREATE SEQUENCE IF NOT EXISTS {SWITCH_TS_SEQUENCE} START 1",
        f"CREATE SEQUENCE IF NOT EXISTS {RECLAIM_TS_SEQUENCE} START 1",
    ]


def drop_rotation_ddl() -> list[str]:
    return [
        f"DROP SEQUENCE IF EXISTS {RECLAIM_TS_SEQUENCE}",
        f"DROP SEQUENCE IF EXISTS {SWITCH_TS_SEQUENCE}",
        f"DROP SEQUENCE IF EXISTS {GEN_SEQUENCE}",
    ]


# --------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def rotation_enabled(get: Callable[[str, Any], Any]) -> bool:
    """Whether ``READY_ROTATION_ENABLED`` is on (default off)."""
    try:
        return _truthy(get("READY_ROTATION_ENABLED", False))
    except Exception:
        return False


@dataclass(frozen=True)
class RotationConfig:
    enabled: bool = False
    interval: float = 60.0
    drain_batch: int = 1000
    # Health degrades when no reclaim succeeded for this long; 0 = 3 x interval + 60 s.
    lag_degraded_seconds: float = 0.0

    @property
    def lag_threshold(self) -> float:
        if self.lag_degraded_seconds > 0:
            return self.lag_degraded_seconds
        return 3 * self.interval + 60


def config_from_getter(get: Callable[[str, Any], Any]) -> RotationConfig:
    """Read the rotation settings; invalid values fall back to the defaults."""
    d = RotationConfig()

    def _num(name, default, cast, minimum):
        try:
            value = cast(get(name, default))
            return value if value >= minimum else default
        except (TypeError, ValueError):
            return default

    return RotationConfig(
        enabled=rotation_enabled(get),
        interval=_num("READY_ROTATION_INTERVAL", d.interval, float, 1.0),
        drain_batch=_num("READY_ROTATION_DRAIN_BATCH", d.drain_batch, int, 1),
        lag_degraded_seconds=_num(
            "READY_ROTATION_LAG_DEGRADED_SECONDS", d.lag_degraded_seconds, float, 0.0
        ),
    )


# --------------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WriterPlan:
    """What a writer of ``sqlery_job_ready`` does before inserting."""

    fenced: bool  # take the shared lock and read the generation
    slot: int | None  # fixed slot when not fenced


def plan_writer(enabled: bool, dialect: str, sequence_present: bool = True) -> WriterPlan:
    """Rotation off (the default), not PostgreSQL, or no sequence yet: slot 0, no lock,
    exactly the behaviour of the non-rotating queue. Otherwise the writer fences itself."""
    if enabled and dialect == "postgresql" and sequence_present:
        return WriterPlan(fenced=True, slot=None)
    return WriterPlan(fenced=False, slot=SLOT_DEFAULT)


class _SequenceProbe:
    """Remembers that the generation sequence exists (a missing one is re-checked rarely, so
    enabling rotation before migrating degrades to slot 0 instead of failing every enqueue)."""

    def __init__(self):
        self.present = False
        self.retry_at = 0.0

    def check(self, read_one: Callable[[str], Any]) -> bool:
        if self.present:
            return True
        now = time.monotonic()
        if now < self.retry_at:
            return False
        self.present = bool(read_one(SEQUENCE_PROBE_SQL))
        if not self.present:
            self.retry_at = now + _PROBE_RETRY_SECONDS
            logger.warning(
                f"READY_ROTATION_ENABLED is on but {GEN_SEQUENCE} does not exist: run the "
                "migrations; writers use slot 0 until then"
            )
        return self.present


_probe = _SequenceProbe()


def reset_probe() -> None:
    """Forget what is known about the sequence (tests)."""
    _probe.present = False
    _probe.retry_at = 0.0


def writer_slot(read_one: Callable[[str], Any], enabled: bool, dialect: str) -> int:
    """Slot a writer inserts into, fenced against the rotator when rotation is on.

    ``read_one(sql)`` runs a statement in the writer's own transaction and returns the first
    column of the first row. The shared advisory lock is held until that transaction ends, so
    the rotator (which needs the key exclusively to switch) cannot advance the generation
    while this writer may still insert into the slot it read. Call it before the insert and
    inside the transaction that does the insert.
    """
    plan = plan_writer(enabled, dialect)
    if not plan.fenced:
        return plan.slot
    if not _probe.check(read_one):
        return SLOT_DEFAULT
    read_one(LOCK_SHARED_SQL)
    return slot_for_generation(read_one(GEN_SQL))


async def awriter_slot(read_one, enabled: bool, dialect: str) -> int:
    """``writer_slot`` for an async transaction (``read_one`` is a coroutine function)."""
    plan = plan_writer(enabled, dialect)
    if not plan.fenced:
        return plan.slot
    if not _probe.present:
        if time.monotonic() < _probe.retry_at:
            return SLOT_DEFAULT
        _probe.present = bool(await read_one(SEQUENCE_PROBE_SQL))
        if not _probe.present:
            _probe.retry_at = time.monotonic() + _PROBE_RETRY_SECONDS
            return SLOT_DEFAULT
    await read_one(LOCK_SHARED_SQL)
    return slot_for_generation(await read_one(GEN_SQL))


# --------------------------------------------------------------------------------------------
# Rotator decisions
# --------------------------------------------------------------------------------------------

IDLE = "idle"
DRAIN = "drain"
RECLAIM = "reclaim"
SWITCH = "switch"
RETRY = "retry"


@dataclass(frozen=True)
class RotationState:
    """Everything the next step depends on, derived from the database on every tick."""

    generation: int
    nonempty: tuple[bool, bool, bool]  # a slot holds visible rows
    dirty: tuple[bool, bool, bool]  # a slot's storage is not reclaimed (rows or dead space)
    now: float  # seconds (database clock)
    last_switch: float | None  # when the generation last advanced
    interval: float
    # Outcome of the lock the previous attempt of this action tried; None = not tried yet.
    switch_lock: bool | None = None
    reclaim_lock: bool | None = None

    @property
    def current(self) -> int:
        return slot_for_generation(self.generation)

    @property
    def others(self) -> tuple[int, int]:
        """The two slots writers do not use: the previous one first (oldest data)."""
        cur = self.current
        return ((cur - 1) % SLOT_COUNT, (cur + 1) % SLOT_COUNT)

    @property
    def interval_elapsed(self) -> bool:
        return self.last_switch is None or self.now - self.last_switch >= self.interval


@dataclass(frozen=True)
class Action:
    kind: str
    slot: int | None = None
    reason: str = ""


def decide_rotation_step(state: RotationState) -> Action:
    """The next step of the rotator. Pure: the same state always gives the same action, so a
    crash between steps just re-derives the state and continues.

    Order matters: a slot that is not clean blocks the switch (the ring never moves on while
    the previous generation is unreclaimed, which is what bounds the storage), draining comes
    before reclaiming, and the switch is last.
    """
    others = state.others
    for slot in others:
        if state.nonempty[slot]:
            return Action(DRAIN, slot)
    for slot in others:
        if state.dirty[slot]:
            if state.reclaim_lock is False:
                return Action(RETRY, slot, "partition is locked by another transaction")
            return Action(RECLAIM, slot)
    if not state.interval_elapsed:
        return Action(IDLE, reason="interval not elapsed")
    if state.switch_lock is False:
        return Action(RETRY, reason="a writer of the current generation is still open")
    return Action(SWITCH)


# --------------------------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------------------------

OK = "ok"
DEGRADED = "degraded"
UNKNOWN = "unknown"


def rotation_lag(now: float, last_reclaim: float | None, last_switch: float | None) -> float | None:
    """Seconds since the last successful reclaim; before the first one, since the last switch
    (the rotation has started but never completed a cycle). None = rotation never ran."""
    if last_reclaim is not None:
        return max(0.0, now - last_reclaim)
    if last_switch is not None:
        return max(0.0, now - last_switch)
    return None


def assess_rotation(enabled: bool, lag: float | None, threshold: float) -> str:
    """``ok``/``degraded``/``unknown`` for the rotation lag (disabled rotation is ``ok``).

    Degraded means the ring is not keeping up (a transaction keeps a partition or the
    generation lock): the ready table then behaves like the non-rotating one, never worse.
    """
    if not enabled:
        return OK
    if lag is None:
        return UNKNOWN
    return DEGRADED if threshold > 0 and lag > threshold else OK


STATUS_SQL = (
    f"SELECT (SELECT last_value FROM {SWITCH_TS_SEQUENCE}), "
    f"(SELECT last_value FROM {RECLAIM_TS_SEQUENCE}), "
    "floor(extract(epoch from clock_timestamp()))"
)


def _ts(value) -> float | None:
    """A timestamp stored in a sequence; the start value 1 means "never"."""
    value = float(value or 0)
    return value if value > 1 else None


def status_from_row(row, config: RotationConfig) -> dict:
    last_switch, last_reclaim, now = _ts(row[0]), _ts(row[1]), float(row[2])
    lag = rotation_lag(now, last_reclaim, last_switch)
    return {
        "enabled": config.enabled,
        "status": assess_rotation(config.enabled, lag, config.lag_threshold),
        "lag_seconds": None if lag is None else int(lag),
        "threshold_seconds": int(config.lag_threshold),
    }


# --------------------------------------------------------------------------------------------
# SQL executors (each protocol step is one short transaction)
# ---------------------------------------------------------------------------------------------

# ``tx()`` is a context manager that yields ``execute(sql, params=None) -> list[tuple]``, runs
# everything inside it as one transaction (commit on exit, rollback on an exception) and uses
# ``:name`` placeholders.

TxFactory = Callable[[], Any]

_ROW_COLUMNS = "job_id, queue_name, priority, created_at, scheduled_at"


def _state_sql() -> str:
    parts = ", ".join(f"EXISTS (SELECT 1 FROM {partition_name(s)})" for s in range(SLOT_COUNT))
    sizes = ", ".join(f"pg_relation_size('{partition_name(s)}') > 0" for s in range(SLOT_COUNT))
    return (
        f"SELECT (SELECT last_value FROM {GEN_SEQUENCE}), {parts}, {sizes}, "
        f"(SELECT last_value FROM {SWITCH_TS_SEQUENCE}), "
        "floor(extract(epoch from clock_timestamp()))"
    )


def _move_sql(slot: int, limited: bool) -> str:
    part = partition_name(slot)
    where = (
        f" WHERE ctid IN (SELECT ctid FROM {part} LIMIT :n FOR UPDATE SKIP LOCKED)"
        if limited
        else ""
    )
    return (
        f"WITH moved AS (DELETE FROM {part}{where} RETURNING {_ROW_COLUMNS}), "
        f"ins AS (INSERT INTO {JOB_READY} (slot, {_ROW_COLUMNS}) "
        f"SELECT CAST(:cur AS smallint), {_ROW_COLUMNS} FROM moved "
        "ON CONFLICT DO NOTHING RETURNING 1) "
        "SELECT count(*) FROM moved"
    )


def is_lock_not_available(exc: BaseException) -> bool:
    """True for PostgreSQL's ``lock_not_available`` (55P03), however the driver wraps it."""
    seen: set[int] = set()
    todo: list[Any] = [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if getattr(e, "sqlstate", None) == SQLSTATE_LOCK_NOT_AVAILABLE:
            return True
        if getattr(e, "pgcode", None) == SQLSTATE_LOCK_NOT_AVAILABLE:
            return True
        todo.extend([getattr(e, "orig", None), e.__cause__, e.__context__])
    return False


class SqlRotationIO:
    """The rotator's database operations; ``tx`` supplies the transactions."""

    def __init__(self, tx: TxFactory):
        self._tx = tx

    @staticmethod
    def _read_state(execute, interval: float) -> RotationState:
        row = execute(_state_sql())[0]
        return RotationState(
            generation=int(row[0]),
            nonempty=(bool(row[1]), bool(row[2]), bool(row[3])),
            dirty=(
                bool(row[1]) or bool(row[4]),
                bool(row[2]) or bool(row[5]),
                bool(row[3]) or bool(row[6]),
            ),
            now=float(row[8]),
            last_switch=_ts(row[7]),
            interval=interval,
        )

    def read_state(self, interval: float) -> RotationState:
        with self._tx() as execute:
            return self._read_state(execute, interval)

    def switch(self, interval: float) -> str:
        """``switched``, ``busy`` (lock not acquired) or ``skipped`` (nothing to do any more).

        Holding the key exclusively proves that no writer which read the old generation is
        still open; writers that start after this transaction read the new one.
        """
        with self._tx() as execute:
            if not execute(TRY_LOCK_SQL)[0][0]:
                return "busy"
            state = replace(self._read_state(execute, interval), switch_lock=True)
            if decide_rotation_step(state).kind != SWITCH:
                return "skipped"  # another rotator got there first, or a slot is unclean
            execute(f"SELECT nextval('{GEN_SEQUENCE}')")
            execute(f"SELECT setval('{SWITCH_TS_SEQUENCE}', :now)", {"now": int(state.now)})
        return "switched"

    def drain(self, slot: int, batch: int) -> int:
        """Move up to ``batch`` entries of a non-current slot into the current one.

        Rows a claimer holds (``FOR UPDATE SKIP LOCKED``) stay for the next call. The shared
        key keeps the generation fixed for the transaction.
        """
        with self._tx() as execute:
            execute(LOCK_SHARED_SQL)
            cur = slot_for_generation(execute(GEN_SQL)[0][0])
            if slot == cur:
                return 0
            return int(execute(_move_sql(slot, True), {"n": int(batch), "cur": cur})[0][0])

    def reclaim(self, slot: int, batch: int) -> bool:
        """Empty a drained slot's storage with ``TRUNCATE``; False if it is locked (retry)."""
        try:
            with self._tx() as execute:
                execute(LOCK_SHARED_SQL)
                cur = slot_for_generation(execute(GEN_SQL)[0][0])
                if slot == cur:
                    return False
                # Never waits: a transaction that touched the partition keeps its lock.
                execute(f"LOCK TABLE {partition_name(slot)} IN ACCESS EXCLUSIVE MODE NOWAIT")
                # Whatever is left (a claimer may have rolled back) goes to the current slot.
                moved = int(execute(_move_sql(slot, False), {"cur": cur})[0][0])
                if moved > max(batch, 1) * 10:
                    return False  # far more than a straggler: let drain handle it in batches
                execute(f"TRUNCATE {partition_name(slot)}")
                execute(
                    f"SELECT setval('{RECLAIM_TS_SEQUENCE}', "
                    "CAST(floor(extract(epoch from clock_timestamp())) AS bigint))"
                )
        except Exception as exc:
            if is_lock_not_available(exc):
                return False
            raise
        return True

    def status(self) -> tuple:
        with self._tx() as execute:
            return tuple(execute(STATUS_SQL)[0])


MAX_STEPS_PER_TICK = 50


def run_rotation_tick(io, config: RotationConfig, max_steps: int = MAX_STEPS_PER_TICK) -> dict:
    """Advance the rotation as far as possible this tick; stops at the first step that cannot
    make progress (it is retried next tick). Every step re-derives the state, so a crash
    anywhere leaves a state the next tick continues from."""
    report = {"switched": 0, "drained": 0, "reclaimed": 0, "retry": ""}
    for _ in range(max_steps):
        state = io.read_state(config.interval)
        action = decide_rotation_step(state)
        if action.kind == IDLE:
            break
        if action.kind == DRAIN:
            moved = io.drain(action.slot, config.drain_batch)
            report["drained"] += moved
            if moved == 0:
                report["retry"] = "drain: remaining entries are held by claimers"
                break
        elif action.kind == RECLAIM:
            if io.reclaim(action.slot, config.drain_batch):
                report["reclaimed"] += 1
            else:
                retry = decide_rotation_step(replace(state, reclaim_lock=False))
                report["retry"] = retry.reason or "reclaim"
                break
        elif action.kind == SWITCH:
            if report["switched"]:
                break  # one generation per tick
            outcome = io.switch(config.interval)
            if outcome == "switched":
                report["switched"] += 1
            else:
                busy = decide_rotation_step(replace(state, switch_lock=False))
                report["retry"] = busy.reason if outcome == "busy" else "state changed"
                break
        else:  # RETRY
            report["retry"] = action.reason
            break
    return report


# --------------------------------------------------------------------------------------------
# Database adapters and the rotator that the scheduler leader drives
# --------------------------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


@contextmanager
def django_tx() -> Iterator[Callable]:
    from django.db import connection, transaction

    if connection.in_atomic_block:
        # A step must be its own transaction (its locks end with it).
        raise RuntimeError("ready-queue rotation needs its own transaction")
    with transaction.atomic():
        with connection.cursor() as cur:

            def execute(sql: str, params: dict | None = None) -> list[tuple]:
                cur.execute(_PLACEHOLDER.sub(r"%(\1)s", sql), params or None)
                return list(cur.fetchall()) if cur.description else []

            yield execute


@contextmanager
def sqlalchemy_tx() -> Iterator[Callable]:
    from sqlalchemy import text

    from sqlery.fastapi_sqlery.database import get_engine

    with get_engine().begin() as conn:

        def execute(sql: str, params: dict | None = None) -> list[tuple]:
            result = conn.execute(text(sql), params or {})
            return [tuple(r) for r in result.fetchall()] if result.returns_rows else []

        yield execute


def _database_is_postgres() -> bool:
    from sqlery.compat import is_django_mode

    if is_django_mode():
        from django.db import connection

        return connection.vendor == "postgresql"
    from sqlery.fastapi_sqlery.database import get_engine

    return get_engine().dialect.name == "postgresql"


def default_io() -> SqlRotationIO | None:
    """Rotation executor for the active integration mode; None unless PostgreSQL."""
    from sqlery.compat import is_django_mode

    if not _database_is_postgres():
        return None
    return SqlRotationIO(django_tx if is_django_mode() else sqlalchemy_tx)


class Rotator:
    """Runs the rotation from the scheduler leader's loop (at most one tick per check
    interval); never raises. Safe with two leaders overlapping: every step re-checks."""

    def __init__(self):
        self._last_tick: float | None = None

    @staticmethod
    def check_interval(config: RotationConfig) -> float:
        return max(1.0, min(10.0, config.interval / 6))

    def tick(self, get_config: Callable[[str, Any], Any] | None = None) -> dict:
        try:
            if get_config is None:
                from sqlery.compat import get_config
            config = config_from_getter(get_config)
            if not config.enabled:
                return {}
            now = time.monotonic()
            if self._last_tick is not None and now - self._last_tick < self.check_interval(config):
                return {}
            self._last_tick = now
            io = default_io()
            if io is None:
                return {}
            report = run_rotation_tick(io, config)
            if report["retry"]:
                logger.debug(f"ready-queue rotation waiting: {report['retry']}")
            return report
        except Exception as e:
            logger.error(f"Ready-queue rotation failed: {e}", exc_info=True)
            return {}


def check_rotation(get_config: Callable[[str, Any], Any]) -> dict | None:
    """Rotation lag for the health endpoints; None when rotation is off. Never raises."""
    config = config_from_getter(get_config)
    if not config.enabled:
        return None
    try:
        io = default_io()
        if io is None:
            return {"enabled": True, "status": UNKNOWN, "reason": "not_postgresql"}
        return status_from_row(io.status(), config)
    except Exception as exc:
        logger.warning(f"ready-queue rotation probe failed: {exc}")
        return {"enabled": True, "status": UNKNOWN, "reason": f"probe_failed: {type(exc).__name__}"}
