# Roadmap

## Shipped milestones

- **v1.0 — Feature-Complete Run Modes** (2026-03-18 → 2026-05-15) — 4 phases, 25 plans, 43 requirements. All execution modes production-ready across Django and standalone integrations on SQLite and Postgres; async worker rebuilt; security hardened (dashboard auth, webhook SSRF, CSRF, task module allowlist); test/CI infrastructure rebuilt. Archive: [`milestones/v1.0-ROADMAP.md`](milestones/v1.0-ROADMAP.md) · [`milestones/v1.0-REQUIREMENTS.md`](milestones/v1.0-REQUIREMENTS.md) · [`v1.0-MILESTONE-AUDIT.md`](v1.0-MILESTONE-AUDIT.md)

## Active milestone

_None — milestone v1.0 closed. Start the next milestone with `/gsd-new-milestone`._

Top-priority backlog item routed by `.planning/BACKLOG.md`: lock drop-in compatibility with Celery, RQ, and django-tasks-scheduler as a permanent first-class feature (new `sqlery.compat.celery` shim + de-deprecate `sqlery.compat.rq` + audit `sqlery.compat.scheduler`).

## Lower-priority / [FOLLOWUP] carry-forward

- Coverage gate path from 13% → 70% (Phase 3 03-08 `[FOLLOWUP]`).
- Phase 1 `standalone-no-django` CI human-verify (push branch + watch).
- Lambda fidelity testing (LocalStack/SAM) — deferred from v1.0 Phase 2.
- SSRF v2 hardening (~50ms DNS-rebinding window, HTTP redirect re-validation, IPv4-mapped IPv6 normalization) — documented in `docs/SECURITY.md`.
- Quarterly dead-code retention sweep — each `#CLEANUP` marker has a `Remove after YYYY-MM-DD`; arrive at the date, decide per-file.

See `.planning/BACKLOG.md` for the full backlog.
