# Adversarial Review: Versions Past v0.19.0

**Scope:** v0.20.0 through v0.21.2 (and the v0.22 milestone preamble)  
**Review date:** 2026-05-25  
**Reviewer:** Adversarial auditor  
**Premise:** Treat every change as guilty until proven innocent. Assume the worst interpretation of every shortcut.

---

## Executive Summary

The codebase did not exist before 2026-03-16; v0.19.0 is a fiction. v0.20.0 was the first tagged release, making this review a post-hoc audit of the entire project history. What emerges is a pattern of **rapid feature assembly followed by reactive patch cycles**, with architectural debt accumulating faster than it is retired. The v0.21 milestone shipped 43 requirements under heavy GSD-process pressure, and the resulting system is **functionally impressive but operationally brittle**.

**Key finding:** The project has spent more energy proving it *can* do six execution modes than proving it *should*, or that any of them are safe to run unattended.

---

## 1. v0.20.0 — The Foundation Was Rushed

**Shipped:** 2026-03-16 (same day as project scaffolding)

*Both findings originally filed under this section (1.1 same-day release anti-pattern, 1.2 the v0.20.x fire-drill) have been moved to NO LONGER RELEVANT — see R-H and R-I.*

---

## 2. v0.21 — A Milestone Built on Process, Not Proof

**Shipped:** 2026-05-15 (after 8 weeks, 137 commits, 25 plans)

### 2.1 Core Unification (Phase 01)

*The "compat layer is Django-only" finding originally filed under this section has been resolved in code and moved to NO LONGER RELEVANT — see R-J.*

### 2.2 Execution Modes (Phase 02) — Six modes, one reality

The milestone claims "6 execution modes × 2 integrations = 12 combinations". The adversarial view:

**Daemon mode (DMOD-01 / SMOD-01)**  
A database-backed lease system with heartbeats and SIGUSR1 signal flags. The worker's signal handler sets a flag (not a direct DB write) because psycopg is not async-signal-safe — but the daemon itself calls `refresh_worker_heartbeat()` immediately after sending SIGUSR1, so the DB timestamp is always updated server-side. The worker's deferred flag-check (every 0.5–1s) only enriches the heartbeat with status/current_job metadata. Liveness is DB-backed by design. The "zombie detection" uses five heuristics (PID gone, no worker, worker dead, worker moved on, heartbeat stale) as defense-in-depth, not because no single source of truth exists.

*HTTP trigger (SMOD-03), Lambda/serverless (DMOD-04 / SMOD-04), and async worker (ASYN-04 / ASYN-05) findings originally filed under this section have been resolved or accepted and moved to NO LONGER RELEVANT — see R-K, R-L, and R-M.*

**Synchronous thread (DMOD-05 / SMOD-05)**  
The simplest mode, yet it shares the same `JobExecutor` that was retroactively patched for the `ALLOWED_TASK_MODULES` gate in Phase 04. This means synchronous execution paths received security hardening **after** the execution-mode milestone was declared complete.

### 2.3 Testing & CI (Phase 03) — The 13% coverage confession

The milestone audit admits the coverage gate is pinned at `fail_under=13`. Thirteen percent. The justification—"196 pre-existing Django test-collection errors"—is a **collection error**, not a coverage problem. If 196 tests cannot even be collected, the test suite is structurally unsound, not merely under-covered.

> **Partially addressed 2026-05-25.** The gate was raised from `fail_under=13` to `fail_under=20` in `pyproject.toml`. This is a ratchet, not a fix — the underlying 196 collection errors remain the real defect (see Recommendation #5, which argues for fixing/deleting the broken tests rather than tuning the floor). Note: baseline coverage was measured at ~15%, so the suite must actually clear 20% for CI to stay green.

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
*Finding moved to NO LONGER RELEVANT as a deliberate WON'T-FIX — see R-N.*

**CLEAN-01 — Backward-compatibility stubs**  
The project date-stamped 22 stubs instead of deleting them. The estimate was 24; the reconciliation is documented, but the underlying decision—"keep dead code because we are afraid to delete it"—remains unchallenged.

---

## 3. Post-v0.21 Patches (v0.21.1–v0.21.2) — The Stabilization Tax

Shipped within a week of the milestone (2026-05-18 to 2026-05-25), these patches prove the milestone was not stable at close:

- **v0.21.1:** `wrap claim_job in transaction.atomic to fix worker crash-loop on PostgreSQL`  
  A worker crash-loop on Postgres is not a minor bug; it is a **production outage**. That this was found after the Phase 03 verifier PASS means the verifier was insufficient.
[CAN ANYTHING BE DONE that was not already? no, then not relevant anymore]

- **v0.21.2:** `stop dashboard from spamming console errors when session expires`  
  The dashboard's `updateStats()` threw an error every 3 seconds when a 403 was returned on session expiry, logging a hard failure to the console indefinitely. The same bug existed in `updateTasks()` and `pollFeed()`. Fixed by handling 401/403 gracefully (stop polling, show toast) and treating other non-OK responses as transient warnings instead of thrown errors.
[RESOLVED 2026-05-25 — see R-O]

- **v0.21.2 (cont'd):** `prevent dashboard from polling /admin/sqlery/undefined when config is missing`  
  When the inline `DASHBOARD_CONFIG` script failed to load (CSP block, syntax error, etc.), `dashboard.js` created a fallback `{}`, causing `fetch(undefined)` on every 3-second refresh cycle and producing an endless stream of requests to `/admin/sqlery/undefined`. Fixed by adding a `_urlOk()` guard that validates URLs are non-empty strings before each fetch.
[RESOLVED 2026-05-25 — see R-P]

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

3. **Make the compat layer standalone-capable.** ~~The RQ compat currently hard-codes Django imports.~~
   > **Resolved 2026-05-25.** `compat/rq.py` was rewritten to be backend-agnostic: the four top-level Django imports were removed and made lazy/mode-detecting, utility functions and the `Job`/`Worker` stubs now route through `get_backend()` / the `DatabaseBackend` ABC (Django fast-path preserved), and `Retry`/`JobStatus` were inlined to drop the Django transitive import. A standalone suite (`tests/test_compat_rq_standalone.py`, 9 tests) proves `import sqlery.compat.rq` works with Django absent. Commits `a1ea763`, `f91e37e`. See R-J.

4. **Add compat contract tests.** There are no tests proving that `from sqlery.compat.rq import Queue` behaves like `from rq import Queue` for common call patterns. Without regression tests, the compat layer will drift from the APIs it claims to mirror.

5. **Replace the 13% coverage floor with a 0% floor.** A dishonest number is worse than no number. Force the team to fix the 196 collection errors or delete the tests causing them.
   > **Partially addressed 2026-05-25.** Floor raised 13 → 20 (not 0). The core objection — fix or delete the 196 broken collections — is still open.

6. **Demote Lambda and HTTP trigger to "experimental."** They have not run in realistic environments. Labeling them production-ready is reckless.
   > **Partially addressed 2026-05-25.** The Lambda half is **resolved**: Lambda/serverless is now explicitly marked EXPERIMENTAL (docstring warning + runtime log + doc callouts) — see R-L. HTTP trigger was *hardened* rather than demoted: it gained an IP/origin allowlist (loopback-only by default) on top of the existing HMAC + 5s-window signature, and the secret-only trust model is accepted by design (R-K). Whether HTTP trigger should additionally carry an "experimental" label remains an open judgment call.

8. **Stop adding security features until the test suite can catch arity bugs.** SEC-01 through SEC-04 are meaningless if the verifier cannot distinguish a missing decorator from a production crash.

**Score:** 4 of 8 original recommendations remain open (#4, #5, #6, #8). Recommendation #3 (standalone-capable compat layer) is now **fully resolved** as of 2026-05-25. Of the still-open four, #5 is partially addressed (coverage floor raised, collection errors unfixed) and #6 is partially addressed (Lambda half resolved via EXPERIMENTAL labeling; HTTP-trigger label still a judgment call). The SMOD-03 security gap is no longer counted as open — it is accepted by design (R-K).

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

## R-H. Same-Day Release Anti-Pattern (was Section 1.1)

**Original complaint:** The first commit (`fe0773a`) and the first release tag (`v0.20.0`) share a calendar day — "absence of soak time." The "core library with Django and FastAPI integrations" was committed, tagged, and presumably declared stable before a single production workload could have exercised it.

**Resolution / Verdict (2026-05-25):** Premise rejected. The same-day premise is false — the repository's first commit is not the project's actual start. This has been the work of many days; the codebase was migrated into its current repo from prior work that predates this repository's history.

---

## R-I. The v0.20.x Fire-Drill (was Section 1.2)

**Original complaint:** Four patch releases in three days (v0.20.1–v0.20.4) — an import-migration/daemon-watchdog fix, an output-truncation-limit bump, and a dashboard archive feature — confirm the initial release was undertested. **Original verdict:** v0.20.0 was a developer preview dressed as a stable release.

**Resolution / Verdict (2026-05-25):** Non-actionable observation. The maintainer accepts the characterization but notes there is nothing to act on — the patches already shipped and the underlying releases are historical. Retained for the record only.

---

## R-J. Compat Layer Was Django-Only (was Section 2.1, Recommendation #3)

**Original finding:** The compat layer (`compat/rq.py`, `compat/scheduler.py`) was Django-only. `compat/rq.py` imported `sqlery.django_sqlery.models`, `sqlery.django_sqlery.queue`, and `sqlery.django_sqlery.backend`, so a standalone user migrating from RQ had no path — despite the compat layer being the primary user-facing surface for market-share capture from RQ/Celery/django-tasks-scheduler.

**Resolution / Verdict (2026-05-25):** **RESOLVED IN CODE.** `compat/rq.py` was rewritten to be backend-agnostic: all four top-level Django imports were removed and made lazy/mode-detecting; utility functions and the `Job`/`Worker` stubs now route through the framework-agnostic `get_backend()` / `DatabaseBackend` ABC, with the Django fast-path preserved; `Retry`/`JobStatus` were inlined to drop the Django transitive import. A standalone test suite (`tests/test_compat_rq_standalone.py`, 9 tests) proves `import sqlery.compat.rq` works with Django absent. Commits `a1ea763`, `f91e37e`.

---

## R-K. HTTP Trigger Secret-Only Trust (was Section 2.2, SMOD-03)

**Original complaint:** "Signed internal requests" rely on a shared secret. No key rotation, nonce replay protection, or clock-skew tolerance was mentioned in the trigger handler. The security model is "trust anyone with the secret."

> **Partially addressed 2026-05-25.** Added an IP/origin allowlist (`INTERNAL_ALLOWED_IPS`, default loopback-only `["127.0.0.1", "::1"]`, opt-out via `["*"]`/`None`) as defense-in-depth on top of the HMAC check. Enforced in the framework-agnostic `core/triggers.py:handle()` plus the Django and FastAPI entry points, matched against the real socket peer (`REMOTE_ADDR` / `request.client.host`), never the attacker-controllable `X-Forwarded-For`. The handler already verified HMAC-SHA256 with a 5s timestamp window (clock-skew/replay bound) via constant-time compare; key rotation and a true nonce remain open.

**Resolution / Verdict (2026-05-25):** **ACCEPTED — by design.** The "trust anyone with the secret" critique is acceptable: a secret is, by definition, secret. Combined with HMAC-SHA256, the 5s timestamp window, and the loopback-default IP allowlist already in place, the threat model is considered adequate. Not counted as an open security gap.

---

## R-L. Lambda/Serverless Maturity Label (was Section 2.2, DMOD-04 / SMOD-04)

**Original complaint:** Only smoke-tested. "LocalStack/SAM fidelity testing" is deferred. A serverless handler that has never run in a Lambda-shaped container is not a serverless handler; it is a locally tested Python function with optimistic packaging.

**Resolution / Verdict (2026-05-25):** **RESOLVED by labeling.** The maintainer's directive was "specify and log: Experimental," and the mode is now explicitly marked EXPERIMENTAL: a `.. warning::` block in both `lambda_handler.py` module docstrings (Django + standalone), a one-time `logger.warning` emitted on first handler invocation per process, and ⚠️ callouts in `examples/lambda/README.md` and `docs/ARCHITECTURE.md`. Handler logic unchanged. This satisfies the Lambda half of Recommendation #6; the maturity gap (no LocalStack/SAM fidelity testing) is unchanged but is now honestly labeled.

---

## R-M. Async Worker Risks (was Section 2.2, ASYN-04 / ASYN-05)

**Original complaint:** The async rebuild introduced a hard dependency on Django 5.2 LTS that breaks existing users on Django 4.2. More critically, the drain-with-deadline shutdown relies on `amark_shutting_down` writing transient state to the DB before `asyncio.wait`; if the DB write hangs (network partition to Postgres), the drain deadline is defeated by the very system it depends on.

**Resolution / Verdict (2026-05-25):** **ACCEPTED — won't-fix.** The maintainer accepts the risk as not a relevant issue for the supported deployment profile. Retained for the record only.

---

## R-N. SEC-04 — ALLOWED_TASK_MODULES Security-by-Opt-In (was Section 2.4)

**Original complaint:** An opt-in allowlist for task module imports, wired into worker dispatch. The default is unconfigured and the code emits a warning. In practice most deployments will never configure this, making it security-by-opt-in, which is indistinguishable from no security.

**Resolution / Verdict (2026-05-25):** **WON'T-FIX — accepted as designed.** Security-by-opt-in is the intended posture: the allowlist is deliberately opt-in with a startup warning, and the maintainer has marked this as not going to be addressed.

---

## R-G. Rectification Log (was Section 9)

| # | Finding | Status | Date |
|---|---------|--------|------|
| R-1 | CHANGELOG references ghost versions, omits real releases | **RESOLVED** | 2026-05-25 |
| R-2 | Compat layer emits DeprecationWarning contradicting "permanent" decision | **RESOLVED** | 2026-05-25 |
| R-3 | Fork safety via manual `_reset_db_connections()` | **RESOLVED** | 2026-05-25 |
| R-4 | SMOD-03 HTTP trigger has no network enforcement (secret-only trust) | **ACCEPTED** — by design; HMAC + 5s window + loopback-default IP allowlist deemed adequate (a secret is secret) | 2026-05-25 |
| R-5 | Lambda/serverless (DMOD-04/SMOD-04) mislabeled despite smoke-only testing | **RESOLVED** — marked EXPERIMENTAL in code + docs (specify-and-log directive met); fidelity testing remains a known, labeled gap | 2026-05-25 |
| R-6 | Coverage gate dishonestly pinned at `fail_under=13` | **PARTIAL** — raised to 20; 196 collection errors still unfixed | 2026-05-25 |
| R-7 | Compat layer Django-only; no standalone RQ migration path | **RESOLVED** — `compat/rq.py` made backend-agnostic via `get_backend()`/`DatabaseBackend`; standalone test suite added (commits `a1ea763`, `f91e37e`) | 2026-05-25 |
| R-8 | Async worker (ASYN-04/05): Django 5.2 dependency + DB-dependent drain deadline | **ACCEPTED** — won't-fix; risk accepted for supported deployment profile | 2026-05-25 |
| R-9 | SEC-04 ALLOWED_TASK_MODULES is security-by-opt-in | **WON'T-FIX** — opt-in posture accepted as designed | 2026-05-25 |
| R-10 | v0.20.0 same-day release anti-pattern (Section 1.1) | **N/A** — premise rejected; prior work predates this repo | 2026-05-25 |
| R-11 | v0.20.x fire-drill (Section 1.2) | **N/A** — non-actionable historical observation | 2026-05-25 |
| R-12 | Dashboard session expiry produces infinite console.error spam (dashboard.js) | **RESOLVED** — 401/403 now stops polling + shows toast; other non-OK logs console.warn (commit `2dedcec`) | 2026-05-25 |
| R-13 | Missing DASHBOARD_CONFIG causes fetch(undefined) → /admin/sqlery/undefined 404s | **RESOLVED** — `_urlOk()` guard added before all auto-refresh fetch() calls (commit `9d86ff2`) | 2026-05-25 |

---

*End of review.*
