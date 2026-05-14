# Phase 04 — Security & Cleanup: Plan Index

**Created:** 2026-05-14
**Plans:** 6
**Waves:** 3

## Wave Map

| Wave | Plans | Parallel? | Autonomous |
|------|-------|-----------|------------|
| 1 | 04-01, 04-02 | yes (no file overlap) | 04-01 has checkpoint (24-file change); 04-02 autonomous |
| 2 | 04-03, 04-04 | yes (no file overlap) | both autonomous |
| 3 | 04-05, 04-06 | yes (no file overlap) | both have checkpoints (CSRF behavior change + docs review) |

## Dependency DAG

```
04-01 (CLEAN omnibus: webhooks move + BC stubs + worker_process fix) ─┬─> 04-04 (SEC-02 SSRF) ─┐
                                                                      │                        │
                                                                      ├─> 04-03 (SEC-01 auth)  ├─> 04-05 (SEC-03 CSRF)
                                                                      │                        │
                                                                      │                        ├─> 04-06 (docs/SECURITY.md)
04-02 (SEC-04 ALLOWED_TASK_MODULES) ──────────────────────────────────┴───────────────────────┘
```

Explicit `depends_on` (from frontmatter):

| Plan | depends_on | Rationale |
|------|-----------|-----------|
| 04-01 | [] | First wave; no upstream deps |
| 04-02 | [] | First wave; touches `core/worker.py` + config files only — no overlap with 04-01 |
| 04-03 | [01] | Soft dep — relies on tree being clean post-CLEAN; no direct file overlap |
| 04-04 | [01] | Hard dep — patches `src/sqlery/django_sqlery/webhooks.py` which only exists at that path after 04-01's move |
| 04-05 | [03, 04] | Soft sequencing — CSRF tests share fixtures with SEC-01 auth tests; runs after Wave 2 to keep test infra coherent |
| 04-06 | [02, 03, 04, 05] | Docs describe the four implemented controls; must run after they all land |

## Requirement → Plan Matrix

| Req | Plan | Notes |
|-----|------|-------|
| SEC-01 | 04-03 | `fastapi_sqlery/auth.py` + middleware wired before all routes incl. `/trigger`. Docs in 04-06. |
| SEC-02 | 04-04 | `security/ssrf.py` + wired at `django_sqlery/webhooks.py` callsite. Docs in 04-06. |
| SEC-03 | 04-05 | Drop `@csrf_exempt` from 11 endpoints + regression test. Docs in 04-06. |
| SEC-04 | 04-02 | `core/security.py` + `core/worker.py` integration + both backends' config. Docs in 04-06. |
| CLEAN-01 | 04-01 (Task 2) | 20 BC stubs date-stamped with `Remove after 2027-05-14`. |
| CLEAN-02 | 04-01 (Task 3) | `async_worker.py` marker verified (no-op). |
| CLEAN-03 | 04-01 (Task 3) | Top 4 commented-block hotspots date-stamped. |
| CLEAN-04 | 04-01 (Task 1) | `webhooks.py` moved to `django_sqlery/`; dated BC stub left at old path; regression test for both import paths. |
| (no req — `worker_process.py:71` arity fix from Phase 03 deferral) | 04-01 (Task 4) | Folded in per CONTEXT specifics #1; AST-based regression test. |

**Coverage check:** All 8 requirement IDs appear in at least one plan. ✓ One extra non-REQ fix folded into 04-01.

## File Ownership (parallelism check)

**Wave 1** (04-01, 04-02):
- 04-01: 24 files in `src/sqlery/*` and `src/sqlery/django_sqlery/*` (BC stubs, webhooks move, worker_process fix) + 2 new test files
- 04-02: `src/sqlery/core/security.py` (new), `src/sqlery/core/worker.py`, `src/sqlery/django_sqlery/settings.py`, `src/sqlery/fastapi_sqlery/config.py`, `tests/unit/test_security.py`
- Overlap: NONE → parallel-safe. ✓
  - Caveat: 04-02 modifies `src/sqlery/core/worker.py` and 04-01 (Task 3, CLEAN-03) ALSO adds date-stamp markers to that file. Both edits are additive (different line ranges) but if run in parallel worktrees, a merge will be required. **Mitigation:** executor of 04-02 reads `src/sqlery/core/worker.py` AFTER 04-01 Task 3 lands, OR the two run sequentially on the same worktree. Documented here so the orchestrator can choose.

**Wave 2** (04-03, 04-04):
- 04-03: `src/sqlery/fastapi_sqlery/auth.py` (new), `src/sqlery/fastapi_sqlery/app.py`, `tests/unit/test_dashboard_auth.py`
- 04-04: `src/sqlery/security/__init__.py` (new), `src/sqlery/security/ssrf.py` (new), `src/sqlery/django_sqlery/webhooks.py`, `tests/unit/test_ssrf.py`
- Overlap: NONE → parallel-safe. ✓

**Wave 3** (04-05, 04-06):
- 04-05: `src/sqlery/django_sqlery/api_views.py`, `tests/test_csrf_regression.py`
- 04-06: `docs/SECURITY.md` (new), `README.md`
- Overlap: NONE → parallel-safe. ✓

## Cross-Plan Notes

1. **04-01's webhooks file move is a precondition for 04-04**: 04-04 patches `src/sqlery/django_sqlery/webhooks.py`, which only exists at that path after 04-01 Task 1. Explicit `depends_on: [01]`.
2. **04-02 / 04-01 soft overlap on `core/worker.py`**: 04-02 wires `check_task_module_allowed` into JobExecutor; 04-01 Task 3 may add CLEAN-03 markers to the same file. Both additive — but Wave 1 parallelism may require a manual merge. Acceptable; document in executor flow.
3. **04-05 CSRF removal may break dashboard JS** (RESEARCH Pitfall): Task 3 of 04-05 includes a grep-check; if any `fetch(`/`XMLHttpRequest` calls in `src/sqlery/django_sqlery/static/` don't send `X-CSRFToken`, the SUMMARY files a follow-up. Out of scope for Phase 4 unless trivial.
4. **04-06 depends on 04-02..04-05 SUMMARYs being accurate** — but the plan reads the PLAN.md files directly, so it doesn't actually need SUMMARYs to exist. Soft dep.
5. **[ASSUMED] flags for plan-checker:**
   - 04-03 Task 1: `/healthz` route doesn't currently exist on the standalone dashboard — plan creates it. If it DOES exist already, executor confirms and skips the route registration.
   - 04-03 Task 1: Standalone-mount detection via `request.scope["app"] is our_app` is verified in RESEARCH against Starlette docs but not against this codebase's exact ASGI stack. Test asserts behavior with a real `Starlette.mount(...)` parent.
   - 04-05 Task 1: RESEARCH counts 11 `@csrf_exempt` in `api_views.py`; Task 1 re-audits and surfaces any delta before proceeding.
   - 04-06 README placement: `[ASSUMED]` no existing "Security" section in README; plan adds one near the bottom.

## Open Items at Plan-Close

- None blocking. All 8 SEC/CLEAN requirement IDs are mapped. CLEAN-04 bug status confirmed live at plan time (`from sqlery.django_sqlery.webhooks import …` raised `ModuleNotFoundError`).

---
*Index created: 2026-05-14*
