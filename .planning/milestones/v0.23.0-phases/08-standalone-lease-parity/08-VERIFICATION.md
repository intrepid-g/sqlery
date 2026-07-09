---
phase: 08-standalone-lease-parity
verified: 2026-06-08T09:05:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: null
---

# Phase 8: Standalone Lease Parity Verification Report

**Phase Goal:** A real standalone per-queue lease exists and behaves like Django's, so leader election stops being a silent Django-only fake and the standalone daemon runs against genuine leases.
**Verified:** 2026-06-08T09:05:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth | Status | Evidence |
| --- | ----- | ------ | -------- |
| 1 | `sqlery_daemon_lease` SQLModel exists mirroring Django's fields + `version` CAS column | ✓ VERIFIED | `models.py:310-322` — `class DaemonLease(SQLModel, table=True)`, `__tablename__ = "sqlery_daemon_lease"`, exactly 7 columns (`queue_name` PK, `daemon_id`, `node_id`, `pid`, `acquired_at`, `expires_at` indexed, `version` default 0). Asserted via runtime introspection (column set, PK, expires_at index). |
| 2 | Date-prefixed Alembic migration creates the table per repo conventions | ✓ VERIFIED | `alembic/versions/20260608_0015_add_daemon_lease.py` — `revision='20260608_0015'`, `down_revision='20260514_0014'`, constant-sourced (`DAEMON_LEASE`), creates table + `ix_sqlery_daemon_lease_expires_at`. Migration `upgrade head`/`downgrade` run cleanly in isolation; head resolves to single linear `20260608_0015`. |
| 3 | `SQLAlchemyBackend` implements real `claim/renew/release_queue_leases`, replacing the fake-election default | ✓ VERIFIED | `fastapi_sqlery/backend.py:227-463` — three methods + `_claim_one_lease` helper operating on real `DaemonLease` rows. Confirmed NOT identical to `DatabaseBackend` ABC objects (ABC `claim` returns `list(queues)`, the fake election at `compat/__init__.py:141`). Signatures match ABC exactly. |
| 4 | Lease claiming is atomic, matching Django — PG `SELECT FOR UPDATE` (SKIP LOCKED), SQLite version CAS | ✓ VERIFIED | `backend.py:291-413` — `skip_locked` branch uses `with_for_update(skip_locked=True)`; `optimistic_version` branch uses `update(...).where(version==current).where(expires_at<now)` with `rowcount==1` success. `test_concurrent_claim_one_winner` proves single winner under thread race; PG path tests auto-skip without `SQLERY_TEST_PG_URL` (expected). |
| 5 | The standalone daemon runs against the real leases instead of the silent fake election | ✓ VERIFIED | `daemon.py:360-510` calls `claim/renew/release_queue_leases` (unchanged in this phase — last touch predates phase 08). With overrides now present, those calls hit real `DaemonLease` rows. `test_daemon_call_contract_matches_signatures` pins the exact call shape; chaos lease tests (`tests/chaos/test_lease_zombie.py`) now activate against standalone (10 passed vs previously auto-skipped). |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `src/sqlery/core/models.py` | DaemonLease SQLModel | ✓ VERIFIED | Lines 310-322; 7 columns, queue_name PK, expires_at indexed, version default 0. Registered on `SQLModel.metadata` (create_all produces table). |
| `src/sqlery/tables.py` | DAEMON_LEASE constant | ✓ VERIFIED | Line 10: `DAEMON_LEASE = "sqlery_daemon_lease"`. |
| `alembic/versions/20260608_0015_add_daemon_lease.py` | create-table migration | ✓ VERIFIED | Chains from `20260514_0014`; creates table + index via constant; upgrade/downgrade verified. |
| `alembic/env.py` | DaemonLease registration | ✓ VERIFIED | Line 18 import includes `DaemonLease`; feeds `target_metadata = SQLModel.metadata`. |
| `src/sqlery/fastapi_sqlery/backend.py` | Real lease methods + helper | ✓ VERIFIED | Lines 227-463; top-level `DaemonLease` + `IntegrityError` imports (no inline imports). |
| `tests/unit/test_sqlalchemy_backend_sync.py` | Lease lifecycle tests | ✓ VERIFIED | `TestSQLAlchemyLeaseLifecycle` + `_read_lease`/`_count_leases` helpers; 12 passed / 2 skipped (PG). |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `models.py` DaemonLease | `SQLModel.metadata` | `class DaemonLease(SQLModel, table=True)` | ✓ WIRED | create_all produces `sqlery_daemon_lease` with all columns + index. |
| migration `20260608_0015` | `20260514_0014` | `down_revision` chaining | ✓ WIRED | Single linear head resolves to `20260608_0015`. |
| `backend.py` | `core.models.DaemonLease` | select/update/delete | ✓ WIRED | All three methods + helper reference and mutate DaemonLease rows. |
| `backend.py` | `determine_claim_strategy` | dialect strategy split | ✓ WIRED | Called at line 254; branches skip_locked vs optimistic_version. |
| `daemon.py` | `SQLAlchemyBackend.claim_queue_leases` | call at daemon.py:363/413 | ✓ WIRED | Daemon consumes the new real methods; daemon.py unchanged this phase. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| `claim_queue_leases` | `claimed` list | `_claim_one_lease` → real DaemonLease INSERT/UPDATE on session | Yes — rows persisted, asserted by `_count_leases`/`_read_lease` | ✓ FLOWING |
| `renew_queue_leases` | `expires_at` | bulk `update(DaemonLease)` filtered on owner | Yes — `test_renew_extends_expires_at` confirms stored value increases | ✓ FLOWING |
| `release_queue_leases` | (deletion) | bulk `delete(DaemonLease)` filtered on owner | Yes — `test_release_deletes_only_owned` confirms rows removed | ✓ FLOWING |
| chaos lease path | lease activation | standalone backend now has real leases | Yes — previously-skipped chaos tests now run (10 passed) | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Override + signature + model parity | `python -c` introspection assertions | `OVERRIDE + SIG + MODEL OK` | ✓ PASS |
| Migration head chain | alembic ScriptDirectory head/down_revision | `HEAD CHAIN OK` | ✓ PASS |
| create_all produces table | SQLModel.metadata.create_all + inspect | table + index present | ✓ PASS |
| Migration upgrade/downgrade (isolated) | stamp 0014 → upgrade head → downgrade | `MIGRATION UPGRADE+DOWNGRADE OK` | ✓ PASS |
| Lease lifecycle suite | `pytest -k "Lease or lease or daemon_call_contract"` | 12 passed, 2 skipped (PG) | ✓ PASS |
| Full backend sync suite | `pytest test_sqlalchemy_backend_sync.py` | 84 passed, 6 skipped, 2 xfailed | ✓ PASS |
| Chaos lease tests activate | `pytest tests/chaos/test_lease_zombie.py` | 10 passed, 1 skipped | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| LEASE-01 | 08-01 | DaemonLease SQLModel mirroring Django + version | ✓ SATISFIED | Truth 1; models.py:310-322 |
| LEASE-02 | 08-01 | Date-prefixed Alembic migration creates table | ✓ SATISFIED | Truth 2; migration 20260608_0015 |
| LEASE-03 | 08-02 | Real SQLAlchemyBackend claim/renew/release | ✓ SATISFIED | Truth 3; backend.py:227-463 |
| LEASE-04 | 08-02 | Atomic claiming (PG FOR UPDATE / SQLite CAS) | ✓ SATISFIED | Truth 4; concurrent-winner test |
| LEASE-05 | 08-02 | Daemon runs against real leases | ✓ SATISFIED | Truth 5; daemon call-contract + chaos tests |

All 5 phase requirement IDs declared in PLAN frontmatter ([LEASE-01, LEASE-02] in 08-01; [LEASE-03, LEASE-04, LEASE-05] in 08-02) are accounted for and satisfied. REQUIREMENTS.md maps exactly LEASE-01..05 to Phase 8 — no orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `models.py` | 322 | `version` Field line >100 chars (black would wrap) | ℹ️ Info | Intentionally consistent with pre-existing `QueuedJob.version` (line 114, identically unwrapped, owned by earlier phase). Documented in 08-01-SUMMARY. Not a phase-08 regression. |
| migration `20260608_0015` | 23-37 | Single-quote string literals (black prefers double) | ℹ️ Info | Cosmetic; does not affect migration behavior. Matches some existing migration style. |

No debt markers (TBD/FIXME/XXX/HACK/PLACEHOLDER) in any modified file. No stubs, empty returns, or mock data sources.

### Human Verification Required

None. All success criteria are programmatically verifiable and verified. The Postgres atomic path (LEASE-04 PG branch) is exercised by tests that auto-skip without `SQLERY_TEST_PG_URL`; this is the documented, in-scope-deferred CI parity concern (PARITY-05, Phase 11) and the SQLite atomicity path is fully verified here. Per phase scope, Postgres matrix testing is explicitly OUT of scope for this foundation phase.

### Gaps Summary

No gaps. The phase goal is achieved: a real standalone `DaemonLease` table and SQLModel exist (LEASE-01), created by a conventions-following date-prefixed migration (LEASE-02); `SQLAlchemyBackend` implements genuine atomic `claim`/`renew`/`release_queue_leases` that override the ABC fake-election default (LEASE-03) with dialect-correct atomicity matching Django semantics — Postgres `FOR UPDATE SKIP LOCKED`, SQLite version-CAS (LEASE-04); and the unchanged standalone daemon's existing lease calls now run against real `DaemonLease` rows, evidenced by previously-skipped chaos lease tests activating and passing (LEASE-05). Behavior mirrors the Django reference (`django_sqlery/backend.py:896-989`): expired take-over, never-steal-live, owner-filtered renew/release.

**Notable parity nuance (Info, not a gap):** The SQLAlchemy backend performs an explicit idempotent self-reclaim (returns True when re-claiming a live lease it already owns), whereas Django returns False on that path (relying on IntegrityError). This was an intentional plan-specified behavior ("stable owner") and is benign for election — the owner remains the owner either way.

Two cosmetic black-formatting deviations on phase-added lines (Info) follow documented pre-existing repo precedent and do not affect goal achievement.

---

_Verified: 2026-06-08T09:05:00Z_
_Verifier: Claude (gsd-verifier)_
