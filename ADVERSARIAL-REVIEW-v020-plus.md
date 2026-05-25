# Adversarial Review: Versions Past v0.19.0

**Scope:** v0.20.0 through v0.21.2 (and the v0.22 milestone preamble)  
**Review date:** 2026-05-25  
**Reviewer:** Adversarial auditor  
**Premise:** Treat every change as guilty until proven innocent. Assume the worst interpretation of every shortcut.

---

## Executive Summary

The codebase did not exist before 2026-03-16; v0.19.0 is a fiction. v0.20.0 was the first tagged release, making this review a post-hoc audit of the entire project history. What emerges is a pattern of **rapid feature assembly followed by reactive patch cycles**, with architectural debt accumulating faster than it is retired. The v0.21 milestone shipped 43 requirements under heavy GSD-process pressure, and the resulting system is **functionally impressive but operationally brittle**.

**Key finding:** The project has spent more energy proving it *can* do six execution modes than proving it *should*, or that any of them are safe to run unattended.

**Open strategic gap:** The compat layer remains Django-only, so standalone RQ migrants have no path.

---

## 1. v0.20.0 — The Foundation Was Rushed

**Shipped:** 2026-03-16 (same day as project scaffolding)

### 1.1 Same-day release anti-pattern
The first commit (`fe0773a`) and the first release tag (`v0.20.0`) share a calendar day. This is not agile; this is **absence of soak time**. The "core library with Django and FastAPI integrations" was committed, tagged, and presumably declared stable before a single production workload could have exercised it.
[This has been the work of many many days, but moved to its current repo]


### 1.2 The v0.20.x fire-drill
Four patch releases in three days (v0.20.1–v0.20.4, 2026-03-18 to 2026-03-19) confirm the initial release was undertested:

| Patch | What broke | What it reveals |
|-------|-----------|-----------------|
| v0.20.1/2 | "finish top-level import migration in core/" + daemon watchdog | The core module structure was not finalized before release. The daemon required an intervention API and import cleanup *after* shipping. |
| v0.20.3 | "increase job output truncation limits" | Either the original limit was chosen arbitrarily, or production data immediately hit it. |
| v0.20.4 | "bulk archive scheduled jobs from dashboard" | A dashboard feature was missing from the v0.20.0 UI/UX surface, implying the initial release was feature-checked, not integration-checked. |

**Verdict:** v0.20.0 was a developer preview dressed as a stable release.
[OK, but this is not actionable]
---

## 2. v0.21 — A Milestone Built on Process, Not Proof

**Shipped:** 2026-05-15 (after 8 weeks, 137 commits, 25 plans)

### 2.1 Core Unification (Phase 01) — Compat layer is Django-only

The compat layer (`sqlery/compat/rq.py` and `sqlery/compat/scheduler.py`, ~1,575 lines) is the primary user-facing surface for market-share capture from RQ/Celery/django-tasks-scheduler.

**Finding:** The compat layer is Django-only. `compat/rq.py` imports `sqlery.django_sqlery.models`, `sqlery.django_sqlery.queue`, and `sqlery.django_sqlery.backend`. A standalone user migrating from RQ has no path. If you want RQ's market share, you cannot ignore the standalone integration path — RQ itself is backend-agnostic.

**Risk:** The dated stubs in `django_sqlery/` rot, but the *real* risk is that the `compat/` layer is treated as second-class while being the primary user-facing surface for market-share capture.

### 2.2 Execution Modes (Phase 02) — Six modes, one reality

The milestone claims "6 execution modes × 2 integrations = 12 combinations". The adversarial view:

**Daemon mode (DMOD-01 / SMOD-01)**  
A database-backed lease system with heartbeats and SIGUSR1 signal flags. The worker's signal handler sets a flag (not a direct DB write) because psycopg is not async-signal-safe — but the daemon itself calls `refresh_worker_heartbeat()` immediately after sending SIGUSR1, so the DB timestamp is always updated server-side. The worker's deferred flag-check (every 0.5–1s) only enriches the heartbeat with status/current_job metadata. Liveness is DB-backed by design. The "zombie detection" uses five heuristics (PID gone, no worker, worker dead, worker moved on, heartbeat stale) as defense-in-depth, not because no single source of truth exists.

**HTTP trigger (SMOD-03)**  
"Signed internal requests" rely on a shared secret. There is no mention of key rotation, nonce replay protection, or clock-skew tolerance in the trigger handler. The security model is "trust anyone with the secret"—which is fine, until it is not.

**Lambda/serverless (DMOD-04 / SMOD-04)**  
Only smoke-tested. The milestone audit admits "LocalStack/SAM fidelity testing" is deferred. A serverless handler that has never run in a Lambda-shaped container is not a serverless handler; it is a **locally tested Python function with optimistic packaging**.

**Async worker (ASYN-04 / ASYN-05)**  
The async rebuild is the most technically sound part of Phase 02, but it introduced a **hard dependency on Django 5.2 LTS** that breaks existing users on Django 4.2. More critically, the drain-with-deadline shutdown relies on `amark_shutting_down` writing transient state to the DB before `asyncio.wait`. If the DB write hangs (network partition to Postgres), the drain deadline is defeated by the very system it depends on.

**Synchronous thread (DMOD-05 / SMOD-05)**  
The simplest mode, yet it shares the same `JobExecutor` that was retroactively patched for the `ALLOWED_TASK_MODULES` gate in Phase 04. This means synchronous execution paths received security hardening **after** the execution-mode milestone was declared complete.

### 2.3 Testing & CI (Phase 03) — The 13% coverage confession

The milestone audit admits the coverage gate is pinned at `fail_under=13`. Thirteen percent. The justification—"196 pre-existing Django test-collection errors"—is a **collection error**, not a coverage problem. If 196 tests cannot even be collected, the test suite is structurally unsound, not merely under-covered.

**The gap-closure pass fixed a real production bug:** `claim_next_job_with_queue_priority` had an arity mismatch. This was caught by tests, but the initial verifier diagnosed it as a missing `@pytest.mark.django_db` mark. The fact that the project's own verification tooling misdiagnosed a production bug should terrify anyone relying on the test suite for safety.

**Chaos testing** was "rebuilt with real subprocess workers + Hypothesis," but `test_property_based.py` is stubbed with `pytest.skip(..., allow_module_level=True)` pending a rewrite. The project claims chaos testing infrastructure exists; in reality, it has a **placeholder that skips itself**.

### 2.4 Security & Cleanup (Phase 04) — Hardening after the fact

Every security feature in Phase 04 was added **after** the execution modes were declared production-ready. This is the textbook definition of bolt-on security.

**SEC-01 — Dashboard Auth**  
Three-mode middleware: `standalone` (API key), `disabled`, or `inherit`. The default is not documented as hardened; the audit merely notes it is "installed before routers." A middleware that runs before routers is table stakes, not a security guarantee.

**SEC-02 — SSRF Defense**  
The webhook URL validator blocks private IP ranges. The audit admits four limitations are deferred to v2: DNS-rebinding window, redirect re-validation, IPv4-mapped IPv6, and Celery shim. SSRF defense with known bypasses is **SSRF theater**.

**SEC-03 — CSRF**  
Ten state-changing admin endpoints lost `@csrf_exempt`. Good. But three endpoints in `views.py` still intentionally use it. The regression suite passes, but the **intentional exceptions are not audited** for whether they are still justified.

**SEC-04 — ALLOWED_TASK_MODULES**  
An opt-in allowlist for task module imports, wired into worker dispatch. The default is unconfigured, and the code emits a warning. In practice, most deployments will never configure this, making it **security-by-opt-in**, which is indistinguishable from no security.

**CLEAN-01 — Backward-compatibility stubs**  
The project date-stamped 22 stubs instead of deleting them. The estimate was 24; the reconciliation is documented, but the underlying decision—"keep dead code because we are afraid to delete it"—remains unchallenged.

---

## 3. Post-v0.21 Patches (v0.21.1–v0.21.2) — The Stabilization Tax

Shipped within a week of the milestone (2026-05-18 to 2026-05-25), these patches prove the milestone was not stable at close:

- **v0.21.1:** `wrap claim_job in transaction.atomic to fix worker crash-loop on PostgreSQL`  
  A worker crash-loop on Postgres is not a minor bug; it is a **production outage**. That this was found after the Phase 03 verifier PASS means the verifier was insufficient.

- **v0.21.2:** `auto-register workers on-demand to fix jobs waiting with idle workers`  
  Jobs were waiting while workers sat idle. This is a **scheduling deadlock** in the daemon's worker pool logic. Again, found after the milestone was audited and closed.

- **Dialect-aware atomic claiming:**  
  A v0.21.2 feature that swaps `SELECT FOR UPDATE SKIP LOCKED` for optimistic version-CAS depending on the DB dialect. Adding a fundamental concurrency primitive **after** the security and testing milestone is complete suggests the Postgres path was never seriously load-tested.

---

## 4. Architecture — Abstractions That Leak

### 4.1 The `DatabaseBackend` ABC

Thirty-plus methods in an abstract base class sounds elegant. In practice, it means **every new feature must be implemented twice** (Django + SQLAlchemy) and tested in at least four configurations (sync/async × SQLite/Postgres). The project does this, but at what cost? The v0.21 milestone required 25 plans and 137 commits to achieve parity across both backends. A single-backend queue would have shipped in a quarter of the effort.

### 4.2 Global singletons

`_backend`, `_config`, and `_engine` are module-level singletons initialized once per process. This makes testing harder (evidenced by the 196 collection errors) and prevents running multiple sqlery configurations in the same process. For a library, this is **application-level state** pretending to be a reusable component.

---

## 5. Process & Documentation — Audit Theater

### 5.1 The v0.21 milestone audit

The audit is 254 lines of self-congratulation with accepted-deferred items. It finds "anomalies" but rates them "informational." The discovery that the verifier misdiagnosed a production arity bug is framed as "net positive outcome" rather than **verification system failure**.

### 5.2 STATE.md staleness

The audit notes `.planning/STATE.md` still shows "Phase: 04 — EXECUTING" when reality is complete. The recommendation is to run `/gsd` state-update. If the project's own state-tracking mechanism cannot keep up with its velocity, what chance does an operator have?

---

## 6. v0.22 Preamble — Admitting the Debt

The current milestone (v0.22) is explicitly a **stabilization pass**. Its very existence is an admission that v0.21 was not ready. The requirements read like a bug report dressed as a roadmap:

- "Eliminate the temporary coverage/collection workaround"
- "CI signal is trustworthy"
- "Failure handling is battle-tested"
- "Operator docs cover deploy, run, observe, recover"

These are not features; they are **apologies**.

---

## 7. Open Recommendations

3. **Make the compat layer standalone-capable.** The RQ compat currently hard-codes Django imports. If you want RQ's market share, you must support standalone mode — many RQ users are not Django shops. The `Queue` wrapper in `compat/rq.py` should delegate to `sqlery.core.job_queue` and use `get_backend()`, not `_DjangoQueue`.

4. **Add compat contract tests.** There are no tests proving that `from sqlery.compat.rq import Queue` behaves like `from rq import Queue` for common call patterns. Without regression tests, the compat layer will drift from the APIs it claims to mirror.

5. **Replace the 13% coverage floor with a 0% floor.** A dishonest number is worse than no number. Force the team to fix the 196 collection errors or delete the tests causing them.

6. **Demote Lambda and HTTP trigger to "experimental."** They have not run in realistic environments. Labeling them production-ready is reckless.

8. **Stop adding security features until the test suite can catch arity bugs.** SEC-01 through SEC-04 are meaningless if the verifier cannot distinguish a missing decorator from a production crash.

**Score:** 5 of 8 original recommendations remain open.

---

-----------------------
NO LONGER RELEVANT:
-----------------------

## R-A. Fork Safety (was Section 4.2, Recommendation #7)

**Resolved by:** commit `364f6db` — replaced manual `_reset_db_connections` with hook-based `ForkSafeExecutor` for fork-safe connection lifecycle.

**Original complaint:** The architecture solved fork safety with manual `_reset_db_connections()` calls. There was no fork-safe connection pool, no `pre-fork` hook registry, and no verification that child processes actually reopen connections. The two-layer timeout (child SIGALRM + parent SIGKILL safety net) was a mitigation for a bug that should not be possible in a well-designed system.

**Original recommendation (#7):** "Implement a real fork-safe connection lifecycle. Manual `_reset_db_connections()` is not architecture; it is a prayer."

Now uses a hook-based executor that manages the connection lifecycle around `os.fork()` automatically.

---

## R-B. Subprocess Mode Fork Concerns (was in Section 2.2)

**Resolved by:** commit `364f6db` — ForkSafeExecutor.

**Original complaint:** Fork-per-job is memory-safe but connection-unsafe by default. The parent must close DB connections before fork and the child must reopen them. The project had `_reset_db_connections()`, but this was manual discipline enforced by convention. One missed callsite in a future refactor introduces a fork-after-connect bug.

No longer applies — the hook-based executor handles this structurally.

---

## R-C. CHANGELOG Bankruptcy (was Section 5.3)

**Resolved 2026-05-25.** `CHANGELOG.md` was rewritten from scratch using git tag history as the source of truth. It now contains accurate, categorized entries for v0.20.0 through v0.21.2 with real dates. The ghost versions `0.11.0` and `3.0.0` are gone.

---

## R-D. Compat Layer Deprecation Contradiction (was in Section 2.1, Recommendation #2)

**Resolved 2026-05-25.** `warnings.warn()` calls and `import warnings` removed from `compat/rq.py` and `compat/scheduler.py`. Module docstrings updated to "Permanent first-class feature." All ~12 function/class deprecation notices stripped. The code now matches the planning decision.

---

## R-E. Recommendation #1 — v0.20.x Install History (Partially Addressed)

**Partially addressed 2026-05-25.** The CHANGELOG has been rewritten with accurate version history (v0.20.0–v0.21.2), so the record is honest. Whether docs should recommend `v0.20.x` for installation remains a docs/marketing decision — not a code problem.

---

## R-F. Timeline Reference (Section 8)

| Date | Event |
|------|-------|
| 2026-03-16 | Project scaffolding; v0.20.0 released |
| 2026-03-18 | v0.20.1 / v0.20.2 (import fix + daemon watchdog) |
| 2026-03-19 | v0.20.3 (truncation limits) / v0.20.4 (dashboard archive) |
| 2026-03-16–2026-05-15 | v0.21 development (4 phases, 137 commits) |
| 2026-05-15 | v0.21 milestone closed |
| 2026-05-18 | v0.21.1 (Postgres crash-loop fix) |
| 2026-05-25 | v0.21.2 (idle-worker deadlock fix + dialect-aware claiming) |

---

## R-G. Rectification Log (was Section 9)

| # | Finding | Status | Date |
|---|---------|--------|------|
| R-1 | CHANGELOG references ghost versions, omits real releases | **RESOLVED** | 2026-05-25 |
| R-2 | Compat layer emits DeprecationWarning contradicting "permanent" decision | **RESOLVED** | 2026-05-25 |
| R-3 | Fork safety via manual `_reset_db_connections()` | **RESOLVED** | 2026-05-25 |

---

*End of review.*
