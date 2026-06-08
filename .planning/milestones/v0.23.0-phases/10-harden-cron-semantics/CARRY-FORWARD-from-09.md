# Carry-forward into Phase 10 (from Phase 9 code review)

Phase 9's code review surfaced two related concerns that are correctly Phase 10's
responsibility (cron-semantics hardening / idempotency), not Phase 9 scope:

- **Two-leader cron double-fire (Phase 9 WR-02):** During brief leader overlap, two
  workers can both fire the same due cron tick. This is exactly what CRON-04 (the
  "already queued" idempotency guard under two-leader overlap) must close.

- **In-wait lease renewal has no expiry guard (Phase 9 WR-01 residual):** The worker
  now renews held leases inside the `_fork_and_execute` child-wait loop
  (`src/sqlery/core/worker.py` ~:748-754), which keeps the scheduler lease fresh during
  long jobs. But `renew_queue_leases` updates `WHERE daemon_id = self.worker_id` with no
  `expires_at` predicate, so after a long process pause a worker can re-assert a lease it
  had effectively forfeited — slightly widening the overlap window. Bounded by the same
  CRON-04 idempotency guard; verify Phase 10's exactly-once design accounts for it.

Action for Phase 10: ensure CRON-01 (atomic enqueue + next_run_at advance) and CRON-04
(idempotency under overlap) make double-fire impossible regardless of brief two-leader
overlap, which closes both items above.
