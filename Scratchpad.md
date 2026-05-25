# Scratchpad

Ongoing notes and TODOs from the adversarial review rectification effort.

**Source:** `ADVERSARIAL-REVIEW-v020-plus.md` (reviewed 2026-05-25)

---

## Completed

- [x] **A: CHANGELOG.md rewrite** — Replaced ghost versions (0.11.0, 3.0.0) with real release history (v0.20.0–v0.21.2)
- [x] **B: Compat deprecation removal** — Removed contradictory DeprecationWarnings from `compat/rq.py` + `compat/scheduler.py`, updated docstrings to "Permanent first-class feature"

---

## Standing Priorities

These are architectural mandates — every future change must respect them.

1. **RQ + Celery full compat is a must.** Drop-in compatibility is a permanent first-class feature, not transitional. All compat work should treat these shims as production API surface.

2. **Daemon signaling must be DB-only.** No SIGUSR1 or other OS signals for heartbeats/flags — all signaling state must live exclusively in the database. The current SIGUSR1 signal handler sets a flag but doesn't write to DB, meaning a worker can miss a heartbeat if the signal arrives during a blocking DB call. The "zombie detection" uses 5 heuristics because no single source of truth exists. (Section 2.2, DMOD-01/SMOD-01)

3. **Fork safety must be ensured — no manual discipline.** ~~The current `_reset_db_connections()` is a bare `connections.close_all()` wrapped in `try/except pass`.~~ **[PARTIALLY ADDRESSED 2026-05-25]** `ForkSafeExecutor` now wraps `os.fork()` with pre/post-fork hooks and leak verification. The main fork path in `_fork_and_execute` uses it. Remaining work:
   - 4 error-recovery `_reset_db_connections()` calls still use the manual pattern (lines 196, 559, 674, 748)
   - No user-registered custom hooks yet
   - Leak verification logs warnings but doesn't hard-fail (intentional for now)

4. **Multiple backends, but 99% code reuse.** Keep Django + SQLAlchemy backends, but refactor toward a thin adapter pattern over shared core. The v0.21 parity effort (25 plans, 137 commits) was too expensive — new features should not require implementing twice. (Section 4.1)

---

## Backlog — High Priority

Items that directly affect production safety or user trust.

- [ ] **Compat layer standalone support** — `compat/rq.py` hard-codes Django imports (`sqlery.django_sqlery.models`, `.queue`, `.backend`). Standalone/RQ users migrating without Django have no path. Must delegate to `get_backend()` instead of `_DjangoQueue`. (Section 2.1, Rec #3)
- [ ] **Compat contract tests** — Zero tests proving `from sqlery.compat.rq import Queue` behaves like `from rq import Queue` for common call patterns. Without regression tests the compat layer will drift. (Rec #4)
- [ ] **Celery compat layer** — Does not exist yet. Standing priority says full Celery compat is a must.
- [ ] **Coverage floor at 13%** — 196 test collection errors are structural, not coverage. Either fix collection or delete broken tests. A dishonest number is worse than no number. (Section 2.3, Rec #5)
- [ ] **`test_property_based.py` is a stub** — `pytest.skip(..., allow_module_level=True)` pending rewrite. Chaos testing infrastructure is a placeholder. (Section 2.3)

## Backlog — Medium Priority

Items that improve operational quality but aren't blocking.

- [ ] **Lambda/HTTP trigger → "experimental"** — Never run in realistic environments (LocalStack/SAM testing deferred). Label honestly. (Section 2.2, Rec #6)
- [ ] **HTTP trigger security** — Shared-secret signing with no key rotation, no nonce replay protection, no clock-skew tolerance. (Section 2.2, SMOD-03)
- [ ] **SEC-02 SSRF defense gaps** — DNS-rebinding window, redirect re-validation, IPv4-mapped IPv6, Celery shim all deferred to v2. Known bypasses. (Section 2.4)
- [ ] **SEC-04 `ALLOWED_TASK_MODULES`** — Opt-in with warning = no security by default. Consider making it opt-out or enforce-by-default with a safe allowlist. (Section 2.4)
- [ ] **STATE.md staleness** — Still shows "Phase: 04 — EXECUTING". (Section 5.2)
- [ ] **Global singletons** — `_backend`, `_config`, `_engine` are module-level, one-per-process. Prevents multi-config testing and running multiple sqlery instances in one process. (Section 4.3)

## Backlog — Low Priority / Housekeeping

- [ ] **3 intentional `@csrf_exempt` endpoints** — Not audited for whether they're still justified. (Section 2.4, SEC-03)
- [ ] **22 dated BC stubs** — `Remove after 2027-05-14`. Calendar reminder to actually remove them.
- [ ] **Async worker Django 5.2 hard dep** — Breaks Django 4.2 users. CHANGELOG listed this as v0.11.0 (ghost version). (Section 2.2)

---

## Notes

_(Feed me info/TODOs here)_
