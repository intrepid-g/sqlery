# Phase 8: Standalone Lease Parity - Pattern Map

**Mapped:** 2026-06-08
**Files analyzed:** 3 (1 model, 1 migration, 1 backend)
**Analogs found:** 3 / 3 (all exact role-matches in-repo)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/sqlery/core/models.py` (add `DaemonLease` SQLModel) | model | CRUD | Django `DaemonLease` (`django_sqlery/models.py:1191`) + sibling SQLModels (`Worker` :260, `QueuedJob` :58) | exact (cross-mode mirror) |
| `alembic/versions/YYYYMMDD_0015_add_daemon_lease.py` (new) | migration | batch/DDL | `alembic/versions/20250101_0007_tag_lock.py` (create_table) + head `20260514_0014` (chaining) | exact |
| `src/sqlery/fastapi_sqlery/backend.py` (3 lease methods) | service/backend | CRUD + atomic-claim | Django backend `claim/renew/release_queue_leases` (`django_sqlery/backend.py:896/963/978`) + `claim_job` atomic pattern (`fastapi_sqlery/backend.py:162`) | exact |

## Pattern Assignments

### `src/sqlery/core/models.py` — add `DaemonLease(SQLModel, table=True)` (model, CRUD)

**Semantic source (mirror field-for-field):** Django `DaemonLease`, `src/sqlery/django_sqlery/models.py:1200-1213`
- `queue_name` CharField(255), primary_key
- `daemon_id` CharField(255), help_text `"daemon_{node_id}_{pid}"`
- `node_id` CharField(255)
- `pid` IntegerField
- `acquired_at` DateTimeField
- `expires_at` DateTimeField, `db_index=True`
- `db_table = "sqlery_daemon_lease"`

**LEASE-01 delta:** add a `version: int` field for SQLite CAS (Django has no version because Django leases rely on `IntegrityError`/`update(...filter expires_at<now)`; standalone needs CAS like `QueuedJob.version`).

**SQLModel structural pattern** — copy field/Config style from `Worker` (`src/sqlery/core/models.py:260-299`) and the `version` field from `QueuedJob:113-114`:
```python
class DaemonLease(SQLModel, table=True):
    """DB-backed lease for queue-scoped scheduler/daemon ownership (standalone)."""

    __tablename__ = "sqlery_daemon_lease"

    queue_name: str = Field(max_length=255, primary_key=True)
    daemon_id: str = Field(max_length=255, description="daemon_{node_id}_{pid}")
    node_id: str = Field(max_length=255)
    pid: int
    acquired_at: datetime
    expires_at: datetime = Field(index=True)
    # Optimistic locking (SQLite CAS) — mirrors QueuedJob.version (models.py:113-114)
    version: int = Field(default=0, description="Version counter for optimistic locking (SQLite CAS)")
```
**Notes:**
- Imports already present at top of `models.py` (`Field, SQLModel`, `datetime`, `timedelta`, `UTC`) — `models.py:8,15`. No new imports needed.
- `version` field on `QueuedJob` (`models.py:113-114`) is the exact CAS-field precedent; copy its wording.
- Follow the no-inline-import rule (CLAUDE.md): top-level imports only.

---

### `alembic/versions/YYYYMMDD_0015_add_daemon_lease.py` (migration, DDL)

**Analog (create_table):** `alembic/versions/20250101_0007_tag_lock.py` (full file)
**Head to chain from:** `20260514_0014_add_shutting_down_status.py` — its `revision = '20260514_0014'` is the current head, so new migration's `down_revision = '20260514_0014'`.

**Header/chaining pattern** (from `20260514_0014` lines 7-19):
```python
revision = '20260603_0015'        # date-prefixed, next sequence number
down_revision = '20260514_0014'   # current head
branch_labels = None
depends_on = None
```

**Table-name constant pattern** — `tables.py` has no lease constant yet. Either add `DAEMON_LEASE = "sqlery_daemon_lease"` to `src/sqlery/tables.py` (preferred — matches `QUEUED_JOB`/`TAG_LOCK` convention) and import it, OR define a local module constant as `tag_lock` migration does (`TAG_LOCK = 'sqlery_tag_lock'`, line 21).

**create_table pattern** (from `20250101_0007:24-33`, extend columns to match the model):
```python
def upgrade() -> None:
    op.create_table(
        DAEMON_LEASE,
        sa.Column('queue_name', sa.String(length=255), nullable=False),
        sa.Column('daemon_id', sa.String(length=255), nullable=False),
        sa.Column('node_id', sa.String(length=255), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('acquired_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('queue_name'),
    )
    op.create_index('ix_sqlery_daemon_lease_expires_at', DAEMON_LEASE, ['expires_at'])

def downgrade() -> None:
    op.drop_index('ix_sqlery_daemon_lease_expires_at', table_name=DAEMON_LEASE)
    op.drop_table(DAEMON_LEASE)
```
**Note:** `expires_at` is indexed in the Django model (`db_index=True`, models.py:1205) — mirror with an explicit `create_index`. Use `batch_alter_table` only for ALTERs (SQLite); plain `create_table`/`create_index` need no batch mode (see `20250101_0007`).

---

### `src/sqlery/fastapi_sqlery/backend.py` — `claim_queue_leases` / `renew_queue_leases` / `release_queue_leases` (backend, atomic-claim + CRUD)

**Semantic analog (match exactly):** `src/sqlery/django_sqlery/backend.py:896-989`. Replaces ABC fake-election default (`compat/__init__.py:118-169`, where `claim_queue_leases` currently `return list(queues)`).

**Session pattern** (every method opens a session) — from `claim_job`, `backend.py:168`:
```python
with self._get_session() as session:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    strategy = determine_claim_strategy(dialect)
```
`self._get_session` set in `__init__` (`backend.py:51`). `determine_claim_strategy` (`backend.py:20-38`) returns `"skip_locked"` for Postgres, `"optimistic_version"` for SQLite.

**Imports already present** (`backend.py:9-17`): `datetime, timedelta, UTC`, `and_, or_, text, update`, `Session, select, func, delete`. Add `DaemonLease` to the `from ..core.models import ...` line (currently `QueuedJob, ScheduledTask, JobRegistry, Worker`, line 16).

**LEASE-04 atomic-claim pattern** — mirror the dual-strategy structure of `claim_job` (`backend.py:188-224`):
- **Postgres (`skip_locked`):** `select(DaemonLease).where(queue_name==q).with_for_update(skip_locked=True)`; if no row → insert; if row and `expires_at < now` → take over (mutate, commit); else skip. (Maps to Django `_claim_one_lease` take-over + insert, `django_sqlery/backend.py:918-961`.)
- **SQLite (`optimistic_version`):** read row; if none → try insert (catch `IntegrityError` → another holder won); if expired → CAS `update(DaemonLease).where(queue_name==q).where(version==current).where(expires_at < now).values(..., version=current+1)`; success iff `rowcount == 1`. This is the exact CAS shape from `claim_job:207-222`.

Per-queue loop + `claimed` accumulation mirrors Django `claim_queue_leases` (`django_sqlery/backend.py:912-916`):
```python
claimed: list[str] = []
for queue_name in queues:
    if self._claim_one_lease(session, queue_name, daemon_id, node_id, pid, lease_secs, strategy):
        claimed.append(queue_name)
return claimed
```

**`renew_queue_leases`** (Django `:963-976`) — bulk update `expires_at`:
```python
with self._get_session() as session:
    session.exec(
        update(DaemonLease)
        .where(DaemonLease.queue_name.in_(owned_queues))
        .where(DaemonLease.daemon_id == daemon_id)
        .values(expires_at=datetime.now(UTC) + timedelta(seconds=lease_secs))
    )
    session.commit()
```

**`release_queue_leases`** (Django `:978-989`) — bulk delete on clean shutdown:
```python
with self._get_session() as session:
    session.exec(
        delete(DaemonLease)
        .where(DaemonLease.queue_name.in_(owned_queues))
        .where(DaemonLease.daemon_id == daemon_id)
    )
    session.commit()
```
`delete` and `update` are already imported (`backend.py:12-13`).

**Method signatures** must match the ABC (`compat/__init__.py:118-169`) and Django backend exactly:
- `claim_queue_leases(self, queues, daemon_id, node_id, pid, lease_secs) -> list[str]`
- `renew_queue_leases(self, owned_queues, daemon_id, lease_secs) -> None`
- `release_queue_leases(self, owned_queues, daemon_id) -> None`

---

## Shared Patterns

### Atomic-claim strategy split (Postgres FOR UPDATE vs SQLite CAS)
**Source:** `determine_claim_strategy` (`fastapi_sqlery/backend.py:20-38`) + `claim_job` body (`:168-224`)
**Apply to:** the new `claim_queue_leases` implementation (LEASE-04). Reuse `determine_claim_strategy(dialect)` verbatim; branch `with_for_update(skip_locked=True)` (Postgres) vs `version`-CAS `update().where(version==current)` + `rowcount == 1` check (SQLite). Do not invent a new strategy function.

### Session lifecycle
**Source:** `self._get_session` (`backend.py:51`), used as `with self._get_session() as session: ... session.commit()` throughout the backend.
**Apply to:** all three lease methods. Always `commit()` after mutations; `refresh()` only if returning the object.

### UTC-aware timestamps
**Source:** `datetime.now(UTC)` used across `backend.py` (e.g. `:169`) and `models.py` (`default_factory=lambda: datetime.now(UTC)`, `:124`). Django side uses `timezone.now()`.
**Apply to:** `acquired_at` / `expires_at` computation in claim/renew. Use `datetime.now(UTC) + timedelta(seconds=lease_secs)`.

### Table-name constants
**Source:** `src/sqlery/tables.py` (`QUEUED_JOB`, `SCHEDULED_TASK`, `WORKER`, `REGISTRY`).
**Apply to:** add `DAEMON_LEASE = "sqlery_daemon_lease"` for the migration to import (keeps parity with existing convention; the `20260514_0014` migration imports `QUEUED_JOB` from `sqlery.tables`).

### Lease TTL
**Locked decision (CONTEXT.md):** `lease_secs = check_interval × 3`. Computed by the caller (daemon), passed in — the backend methods just consume `lease_secs`. No change needed in the backend signature.

## No Analog Found

None — every file has a strong in-repo analog. Postgres FOR-UPDATE-on-a-single-PK-row take-over is the only sub-pattern not literally present for leases (Django uses `filter(expires_at<now).update()` + insert/`IntegrityError`); the standalone equivalent is the `claim_job` `with_for_update(skip_locked=True)` shape applied to the lease row.

## Metadata

**Analog search scope:** `src/sqlery/core/models.py`, `src/sqlery/django_sqlery/{models,backend}.py`, `src/sqlery/fastapi_sqlery/backend.py`, `src/sqlery/compat/__init__.py`, `src/sqlery/tables.py`, `alembic/versions/`
**Files scanned:** 7
**Pattern extraction date:** 2026-06-08
