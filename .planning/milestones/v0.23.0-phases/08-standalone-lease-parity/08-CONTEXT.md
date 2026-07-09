# Phase 8: Standalone Lease Parity - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Build a real standalone per-queue lease so leader election stops being a silent Django-only fake. Deliver a `sqlery_daemon_lease` SQLModel, a date-prefixed Alembic migration, and real `SQLAlchemyBackend` lease methods (`claim_queue_leases` / `renew_queue_leases` / `release_queue_leases`) with atomic claiming that matches Django semantics (Postgres `SELECT FOR UPDATE`, SQLite optimistic CAS on a `version` field). The existing standalone daemon must run against these real leases instead of the inherited fake-election default.

This phase is foundation-only: it does not add worker self-election (Phase 9), cron hardening (Phase 10), or the parity CI gate (Phase 11).

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Follow the locked project decisions: reuse the `sqlery_daemon_lease` scheme (no reserved `__scheduler__` key, no second table); mirror Django `DaemonLease` semantics exactly; lease TTL = `check_interval × 3`. Use ROADMAP success criteria, REQUIREMENTS (LEASE-01..05), and existing codebase conventions to guide decisions.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Django `DaemonLease` model (reference for fields/semantics): `src/sqlery/django_sqlery/models.py:1191` — fields `queue_name` (PK, max 255), `daemon_id`, `node_id`, `pid` (int), `acquired_at`, `expires_at` (indexed); `db_table = "sqlery_daemon_lease"`.
- Django backend lease methods to mirror: `src/sqlery/django_sqlery/backend.py:896` (`claim_queue_leases`), `:963` (`renew_queue_leases`), `:978` (`release_queue_leases`).
- ABC contract / fake-election default being replaced: `src/sqlery/compat/__init__.py:118` (`claim_queue_leases`), `:143` (`renew`), `:158` (`release`).
- Existing standalone SQLModels (pattern reference, incl. `version` CAS field on `QueuedJob`): `src/sqlery/core/models.py` — `ScheduledTask` (:19), `QueuedJob` (:58), `JobRegistry` (:226), `Worker` (:260).

### Established Patterns
- Standalone ORM = SQLModel (`SQLModel, table=True`) in `src/sqlery/core/models.py`.
- Optimistic locking on SQLite via a `version` integer field + CAS (as `QueuedJob` already does); Postgres uses `SELECT FOR UPDATE` / `SKIP LOCKED`.
- Alembic migrations live in `alembic/versions/`, date-prefixed (`YYYYMMDD_NNNN_description.py`); latest is `20260514_0014_add_shutting_down_status.py`. New migration should chain from the current head.

### Integration Points
- `SQLAlchemyBackend` (`src/sqlery/fastapi_sqlery/backend.py`) implements the `DatabaseBackend` ABC; new lease methods replace inherited defaults there.
- Existing standalone daemon already calls `claim/renew/release_queue_leases` — it will transparently start running against real leases once the methods are implemented.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Mirror Django `DaemonLease` field-for-field and match its atomic-claim semantics per backend.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase.

</deferred>
