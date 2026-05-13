# Phase 1 — Core Unification — CONTEXT

**Captured:** 2026-05-13

<domain>
Make `src/sqlery/core/` truly framework-agnostic: single source of truth for the claiming algorithm and the job executor, with zero Django imports in core modules. The package must be importable and runnable in a virtualenv without Django installed.

Requirements: UNIF-01, UNIF-02, UNIF-03, UNIF-04, UNIF-05, UNIF-06.
</domain>

<canonical_refs>
- `.planning/ROADMAP.md` — Phase 1 definition and success criteria
- `.planning/REQUIREMENTS.md` — UNIF-01..06 acceptance criteria
- `.planning/PROJECT.md` — Active items, dead-code policy, key decisions
- `.planning/codebase/ARCHITECTURE.md` — DatabaseBackend ABC contract, layer rules, anti-patterns (refreshed 2026-05-13)
- `.planning/codebase/STRUCTURE.md` — current file layout and stub policy (refreshed 2026-05-13)
- `.planning/codebase/CONCERNS.md` — current tech debt incl. duplicate code locations (refreshed 2026-05-13)
- `src/sqlery/compat/__init__.py` — `DatabaseBackend` ABC; the unification anchor
- `src/sqlery/core/claiming.py` — canonical claiming implementation
- `src/sqlery/core/worker.py` — canonical `JobExecutor`
- `src/sqlery/django_sqlery/worker_claiming.py` — duplicate to retire
- `src/sqlery/django_sqlery/executor.py` — duplicate to retire
- `CLAUDE.md` — project conventions (Python 3.10+, line 100, modern union syntax)
</canonical_refs>

<code_context>
**Reusable assets:**
- `DatabaseBackend` ABC + `DjangoBackend` + `SQLAlchemyBackend` already exist (`src/sqlery/compat/__init__.py`, `src/sqlery/django_sqlery/backend.py`, `src/sqlery/fastapi_sqlery/backend.py`). All claiming/execution data access should route through these.
- `get_backend()` / `get_config()` singletons in compat handle mode auto-detection — call sites already use them in many places.
- `retry_on_db_error` decorator + `configure_connection_resilience()` in `src/sqlery/core/db_resilience.py` — re-usable, but currently has Django imports that need guarding.

**Verified problem locations (2026-05-13):**
- Core modules with Django imports (11, not the "8 of 16" recorded in PROJECT.md):
  - `src/sqlery/core/claiming.py`
  - `src/sqlery/core/daemon.py`
  - `src/sqlery/core/db_resilience.py`
  - `src/sqlery/core/daemon_runner.py`
  - `src/sqlery/core/log_config.py`
  - `src/sqlery/core/model_utils.py`
  - `src/sqlery/core/scheduler_tasks.py`
  - `src/sqlery/core/utils.py`
  - `src/sqlery/core/worker_runner.py`
  - `src/sqlery/core/worker.py`
  - `src/sqlery/core/worker_pool.py`
- Duplicates that still need consolidation: `django_sqlery/worker_claiming.py`, `django_sqlery/executor.py`.

**Constraints:**
- Public API (`@job`, `enqueue`, `Queue`) must remain stable.
- Fork-safety contract: signal handlers must not touch DB; `_reset_db_connections()` must run pre/post fork. Do not regress.
- Dead-code policy: never delete outright — comment-and-date with a removal date.
</code_context>

<decisions>

### Consolidation pattern → "Update callers, mark stubs"
- **Decision:** `django_sqlery/worker_claiming.py` and `django_sqlery/executor.py` are retired. Update all in-repo callers to import from `sqlery.core.claiming` / `sqlery.core.worker` (routed through `DatabaseBackend` where data access is involved). The old files become comment-and-date stubs per the project dead-code policy (do NOT delete).
- **Why:** Most thorough; eliminates the "two source-of-truth" risk that CONCERNS.md flags and avoids growing the wrapper layer. Honors the user's dead-code memory ([feedback_dead_code]).
- **How to apply:**
  - Planner must enumerate every in-repo caller of `django_sqlery.worker_claiming` and `django_sqlery.executor` before changing them.
  - Stubs use the pattern documented in STRUCTURE.md: keep the file with a header comment `# DEPRECATED YYYY-MM-DD — moved to sqlery.core.X. Remove after YYYY-MM-DD (≥ 6 months out).` Optionally a single `__getattr__` lazy re-export so any out-of-repo import still works during the deprecation window.
  - Removal-date placeholders are mandatory; pick concrete dates at planning time.

### Django-import removal → through DatabaseBackend / Config (default), try/except for truly optional Django utilities
- **Decision (implicit, locked by ARCHITECTURE.md anti-pattern rule):** Data access lives behind `DatabaseBackend`. Settings access lives behind `Config`. Anything that today imports `django.db`, `django.conf.settings`, or `django.db.models` in `core/` must be re-routed through one of those two ABCs.
- **Fallback:** For genuinely Django-only utilities (e.g., management-command helpers, signal hooks), guard with `try: import django ... except ImportError: django = None` and gate the code path on `django is not None`.
- **Out of bounds for this phase:** rewriting the ABC itself. Extend it only if a specific UNIF requirement provably needs a new method.

### Standalone verification → CI job + pytest case (both)
- **Decision:** Two-layer verification.
  1. **CI job** in `.github/workflows/test.yml` (or a sibling workflow): fresh Python 3.10 venv, install `sqlery` with NO `django` dependency, then run a smoke script that imports the full core surface and exercises one enqueue + claim against SQLite via the standalone backend.
  2. **Pytest test** (e.g. `tests/test_core_standalone.py`) that uses a subprocess + `PYTHONPATH`-trimmed env (or `importlib` block on `django`) to assert `import sqlery.core` and its submodules succeed without Django on `sys.path`.
- **Why:** CI gives the real-environment guarantee that satisfies UNIF-04/05/06; the pytest case gives fast local feedback during development.
- **How to apply:**
  - Planner must produce both deliverables.
  - The CI job is independent of the existing Django CI matrix — it's an additional job, not a replacement.
  - Pytest test uses subprocess to ensure Django isn't already imported in the parent test process.

</decisions>

<deferred>
Out of scope for Phase 1 (raised in earlier sessions or as scope-creep candidates):

- Fixing the `master → main` CI branch — listed under PROJECT.md item 11; belongs in Phase 3 (Testing & CI).
- Webhook import bug (`django_sqlery/models.py:677,733` imports `from .webhooks` but file is at `src/sqlery/webhooks.py`) — Phase 3 or Phase 4.
- Annotating the 21 top-level backward-compat stubs with removal dates — Phase 4 (Security & Cleanup); only the two consolidated files in this phase get the new dated-stub treatment.
- Adding removal dates to the ~12 `# Old:` dead-code blocks — Phase 4.
- Rebuilding `AsyncWorker` — Phase 2 (ASYN-01..05).
- Extending the `DatabaseBackend` ABC with new methods — only if a UNIF requirement provably needs it; otherwise defer.
</deferred>

<open_questions>
None blocking. Researcher / planner should resolve:
- Exact list of in-repo call sites for `django_sqlery.worker_claiming` and `django_sqlery.executor` (grep before refactor).
- Whether `core/log_config.py` and `core/model_utils.py` Django imports are accidental or intentional — they may be removable outright rather than guarded.
- Removal-date convention for the new dated stubs (suggest: +12 months from commit date, but planner to confirm against any prior precedent in the repo).
</open_questions>
