# Phase 10: Harden Cron Semantics - Pattern Map

**Mapped:** 2026-06-08
**Files analyzed:** 5 (all modified — no new files)
**Analogs found:** 5 / 5

## File Classification

| Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------|------|-----------|----------------|---------------|
| `src/sqlery/core/scheduler.py` | service (scheduler) | event-driven (tick → enqueue) | self (Phase 8 claiming caller pattern in `core/worker.py`) | role-match |
| `src/sqlery/compat/__init__.py` (ABC) | abstract interface | contract | existing `update_scheduled_task_next_run` ABC decl (:531), `claim_queue_leases` ABC (:553+) | exact |
| `src/sqlery/fastapi_sqlery/backend.py` | backend (standalone) | CRUD + atomic CAS | `_claim_one_lease` (:276) + `determine_claim_strategy` (:21) | exact (THE template) |
| `src/sqlery/django_sqlery/backend.py` | backend (Django) | CRUD + atomic lock | `update_scheduled_task_next_run` (:654), Django lease claim in same file | exact |
| `src/sqlery/crontab.py` | utility | transform | self — `next_cron_occurrence` (:133) already accepts `base_date` | exact (no change needed) |

## Critical Schema Finding (affects mechanism choice)

**Neither `ScheduledTask` model has a `version` column.**
- Standalone SQLModel `ScheduledTask` (`src/sqlery/core/models.py:19`) — fields end with `next_run_at` (:41); no `version`. The `version` at models.py:114 belongs to `QueuedJob`; the one at :337 belongs to the `DaemonLease` SQLModel.
- Django `ScheduledTask` (`src/sqlery/django_sqlery/models.py:62`) — `next_run_at` at :148; the `version` at :388 belongs to `QueuedJob`, not `ScheduledTask`.

**Implication:** The atomic advance CAS should key on the **observed `next_run_at`** itself (`WHERE next_run_at == <observed_due_time>`), not on a version column. This is exactly the CONTEXT-suggested mechanism (`10-CONTEXT.md:30`, `:62`) and avoids adding a column + migration. The `next_run_at` value is the natural idempotency token: only the first leader's `UPDATE ... WHERE next_run_at = observed` mutates a row (`rowcount == 1`); that leader alone enqueues.

## Pattern Assignments

### `src/sqlery/fastapi_sqlery/backend.py` — new `advance_scheduled_task_if_due` (backend, atomic CAS)

**Analog:** `_claim_one_lease` (:276-445) — copy its dual-branch dialect split verbatim in shape.

**Strategy dispatch** (`backend.py:21-39`, reuse as-is — do NOT duplicate):
```python
def determine_claim_strategy(dialect_name: str | None) -> str:
    if dialect_name == "postgresql":
        return "skip_locked"
    if dialect_name == "sqlite":
        return "optimistic_version"
    return "basic_lock"
```
For the advance, Postgres should use `with_for_update()` (blocking row lock, NOT `skip_locked` — see the CR-01 comment at :304-316 explaining why single-key rows must NOT use SKIP_LOCKED). SQLite uses the predicate-CAS `update(...).where(...).execution_options(synchronize_session=False)` returning `res.rowcount == 1`.

**Postgres branch template** (`backend.py:317-365`) — adapt to ScheduledTask:
```python
stmt = (
    select(ScheduledTask)
    .where(ScheduledTask.id == task_id)
    .with_for_update()                      # blocking lock, not skip_locked
)
existing = session.exec(stmt).first()
# normalize naive→aware before compare (SQLite returns naive):
#   existing.next_run_at if tzinfo else .replace(tzinfo=UTC)
# only advance if existing.next_run_at == observed_due (still the tick we saw)
existing.next_run_at = new_next_run
session.add(existing); session.commit()
return True
```

**SQLite predicate-CAS template** (`backend.py:422-444`) — the key pattern to copy:
```python
cas_stmt = (
    update(ScheduledTask)
    .where(ScheduledTask.id == task_id)
    .where(ScheduledTask.next_run_at == observed_due)   # CAS on observed time
    .values(next_run_at=new_next_run)
    .execution_options(synchronize_session=False)        # skip ORM evaluator (naive/aware compare)
)
res = session.exec(cas_stmt)
session.commit()
return res.rowcount == 1     # True => this leader won the tick
```

**Existing methods to reuse inside the same session/txn for CRON-01 atomicity:**
- `create_job` (`backend.py:98`) — enqueue.
- `has_pending_job_for_scheduled_task` (`backend.py:968-979`) — current check-then-act guard (the CRON-04 race; the CAS replaces reliance on it).
- `update_scheduled_task_next_run` (`backend.py:981-989`) — current non-atomic advance (the CRON-01 gap: separate `session.commit`).
- `update_scheduled_task` (`backend.py:991-1004`) — used for the `once` disable path; preserve.

---

### `src/sqlery/django_sqlery/backend.py` — new atomic advance (backend, Django)

**Analog:** `update_scheduled_task_next_run` (:654-656) + the Django lease-claim pattern in the same file.

Current non-atomic advance (the gap):
```python
def update_scheduled_task_next_run(self, task_id: int, next_run_at: datetime):
    self.ScheduledTask.objects.filter(id=task_id).update(next_run_at=next_run_at)
```

**Atomic advance pattern (Django):** wrap enqueue + conditional advance in `transaction.atomic()`. Use the queryset-`.update()` CAS idiom (Django's parallel to the SQLite version-CAS — returns affected row count):
```python
# observed_due = the next_run_at value read in get_due_scheduled_tasks
with transaction.atomic():
    advanced = self.ScheduledTask.objects.filter(
        id=task_id, next_run_at=observed_due      # CAS predicate
    ).update(next_run_at=new_next_run)            # returns rowcount
    if advanced != 1:
        return None                                # another leader won; do not enqueue
    return self.create_job(...)                    # same txn => CRON-01 atomic
```
For Postgres, `select_for_update()` on the ScheduledTask row inside the txn is also acceptable; the `.update()` rowcount-CAS already gives exactly-once on both engines. `has_pending_job_for_scheduled_task` (:647-652) and `update_scheduled_task` (:658-661) stay for the once/interval paths.

---

### `src/sqlery/compat/__init__.py` — DatabaseBackend ABC (abstract interface)

**Analog:** existing scheduled-task ABC decls (`:519-551`) and the lease ABC.

Add an `@abstractmethod` for the new atomic advance (signature mirroring the existing decls), e.g.:
```python
@abstractmethod
def advance_scheduled_task_if_due(
    self, task_id: int, observed_next_run_at: datetime, new_next_run_at: datetime
) -> bool:
    """Atomically advance next_run_at only if it still equals observed value (CAS).
    Returns True iff this caller won the tick (and should enqueue)."""
    pass
```
Keep modern type syntax (`X | None`), Google-style docstring (`Args/Returns`), matching the file's existing style (:531-551).

---

### `src/sqlery/core/scheduler.py` — `_enqueue_for_scheduled_task` / `calculate_next_run` / `run_due_tasks`

**Analog:** the Phase 8 claiming caller loop (`run_due_tasks` :29-64 already mirrors the per-item atomic-claim try/except pattern from `core/worker.py`).

- **CRON-02 (drift, `calculate_next_run` :130-149):** already accepts `base_time` and is tz-safe (`:142-147`). Pass `task.next_run_at` as `base_time` from the cron branch (`:108`), not `now`. `next_cron_occurrence` (`crontab.py:133`) needs no change — it already takes `base_date` and adds 1 minute (`crontab.py:157`). For long downtime, loop/clamp to the next **future** occurrence (CONTEXT :24).
- **CRON-01 + CRON-04 (`_enqueue_for_scheduled_task` :66-128):** replace the check-then-act sequence (`has_pending` :78 → `create_job` :89 → separate `update_scheduled_task_next_run` :109) with a single call to the new backend `advance_scheduled_task_if_due(...)` that does conditional-advance + enqueue in one txn. Capture `observed_due = task.next_run_at` from the row read in `run_due_tasks`. Preserve the `interval` (:110-115) and `once` (:116-117) branches.
- **CRON-03 (jitter):** read `get_config('scheduler_jitter_seconds', 0)` (`compat/__init__.py:904`); when > 0 apply a bounded `random.uniform(0, jitter)` `time.sleep` (or enqueue offset) BEFORE enqueue. Must not feed into `calculate_next_run` base time (CONTEXT :25, :31). Top-level imports `random`, `time` per CLAUDE.md.

## Shared Patterns

### Atomic dialect split (Postgres lock vs SQLite predicate-CAS)
**Source:** `src/sqlery/fastapi_sqlery/backend.py:21-39` (`determine_claim_strategy`) and `:276-445` (`_claim_one_lease`)
**Apply to:** the new advance in both backends.
- Postgres: `with_for_update()` blocking lock (NOT `skip_locked` for single-key rows — see :304-316).
- SQLite/fallback: `update(...).where(<cas predicate>).execution_options(synchronize_session=False)`, success = `res.rowcount == 1` (:418-420, :442-444).

### Naive→aware datetime normalization before compare
**Source:** `backend.py:344-348`, `:391-396`
**Apply to:** any `next_run_at` comparison in the standalone advance (SQLite returns naive datetimes):
```python
existing_dt = existing.next_run_at if existing.next_run_at.tzinfo else existing.next_run_at.replace(tzinfo=UTC)
```

### Config access
**Source:** `src/sqlery/compat/__init__.py:904` (`get_config(key, default)`)
**Apply to:** reading `scheduler_jitter_seconds` in scheduler. Note: standalone config keys are read via `StandaloneConfig.get` (`fastapi_sqlery/config.py:114`); Django via `DJANGO_SQL_JOBS` defaults. New key likely needs adding to both config default sets (planner: confirm `DEFAULTS` in `django_sqlery/settings.py`).

### Per-task resilient loop
**Source:** `src/sqlery/core/scheduler.py:54-62` (try/except per task, log + continue)
**Apply to:** keep unchanged when wiring the atomic advance — a failed CAS returns None (skip), an exception logs and continues.

## No Analog Found

None. Every change has a strong existing analog; the Phase 8 lease split is a near-exact template.

## Metadata

**Analog search scope:** `src/sqlery/core/`, `src/sqlery/compat/`, `src/sqlery/fastapi_sqlery/`, `src/sqlery/django_sqlery/`
**Files scanned:** scheduler.py, crontab.py, compat/__init__.py, both backends, both ScheduledTask models
**Pattern extraction date:** 2026-06-08
