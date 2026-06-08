# Roadmap

## Shipped milestones

- **v0.21 — Feature-Complete Run Modes** (2026-03-18 → 2026-05-15) — 4 phases, 25 plans, 43 requirements. All execution modes production-ready across Django and standalone integrations on SQLite and Postgres; async worker rebuilt; security hardened (dashboard auth, webhook SSRF, CSRF, task module allowlist); test/CI infrastructure rebuilt. Archive: [`milestones/v0.21-ROADMAP.md`](milestones/v0.21-ROADMAP.md) · [`milestones/v0.21-REQUIREMENTS.md`](milestones/v0.21-REQUIREMENTS.md) · [`v0.21-MILESTONE-AUDIT.md`](v0.21-MILESTONE-AUDIT.md)
- **v0.22 — Stability, Coverage, and Operational Confidence** (2026-05-15, released through v0.22.3) — 3 phases (Phases 5–7). Restored trustworthy CI/coverage signal without the collection-error workaround or the emergency coverage floor; battle-tested crash/retry/timeout/zombie/heartbeat/lease recovery and PostgreSQL concurrent-claim behavior; delivered operator runbooks and troubleshooting docs for the production-facing execution modes.
- **v0.23.0 — Worker-Elected Cron Scheduler** (shipped 2026-06-08) — 4 phases (Phases 8–11), 11 plans, 21 requirements. A bare `sqlery-worker` cluster now fires recurring cron with no daemon present by self-electing a per-queue scheduler-leader over a real lease scheme at true parity across {Django, standalone} × {SQLite, Postgres}: built the standalone `sqlery_daemon_lease` (SQLModel + migration + atomic claim/renew/release), wired core-shared scheduler election into the worker poll loop (daemon stays authoritative, failover within one TTL), hardened cron to fire exactly-once via an atomic `advance_scheduled_task_if_due` CAS with drift correction and a jitter knob, and enforced the full matrix as a first-class CI gate. Archive: [`milestones/v0.23.0-ROADMAP.md`](milestones/v0.23.0-ROADMAP.md) · [`milestones/v0.23.0-REQUIREMENTS.md`](milestones/v0.23.0-REQUIREMENTS.md) · [`milestones/v0.23.0-MILESTONE-AUDIT.md`](milestones/v0.23.0-MILESTONE-AUDIT.md)

## Active milestone

None — v0.23.0 shipped. Start the next milestone with `/gsd-new-milestone`.

## Lower-priority / [FOLLOWUP] carry-forward

- Compat milestone (Celery/RQ/scheduler permanent drop-in surface) — deliberately deferred behind the v0.22 maturity pass and the v0.23 scheduler-parity work.
- Worker takeover of scheduling even when a daemon is up — deferred (v0.23 default keeps the daemon authoritative).
- A `WORKER_SCHEDULER_ELIGIBLE` opt-out config knob — deferred (v0.23 default is always-eligible, no knob).
- Clean-DB `alembic upgrade head` collision at `20250101_0002` (`sqlery_worker already exists`) — pre-existing, predates v0.23; the new `20260608_0015` lease migration is correct in isolation. Needs a dedicated migration-chain fix.
- Legacy `scheduler_tasks.py` `claim_due_scheduled_task` non-atomic path was not hardened in v0.23 (runtime path is `sqlery.core.scheduler.Scheduler`) — confirm dead or migrate.
- Lambda fidelity testing (LocalStack/SAM) — deferred from v0.21 Phase 2.
- Dashboard audit logging / rate limiting / payload encryption at rest — future ops/security work.
- Quarterly dead-code retention sweep — each `#CLEANUP` marker has a `Remove after YYYY-MM-DD`; arrive at the date, decide per-file.

See `.planning/BACKLOG.md` for the full backlog.
