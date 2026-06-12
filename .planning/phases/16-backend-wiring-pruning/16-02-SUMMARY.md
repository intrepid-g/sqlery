# Plan 16-02 Summary: Write-path pruning checklist items 1–6

**Status:** Complete (executor stalled at the SUMMARY step after committing all code; SUMMARY authored by orchestrator).

## What was built

Added the partition key `created_at` to every id-only CAS / UPDATE filter for checklist items 1–6, so PostgreSQL prunes to a single partition (composite PK `(created_at, id)`). Optimistic-locking semantics unchanged — every CAS filter GAINS `created_at` and KEEPS `version` (D7).

**Commits:**
- `8e7c199`: feat(16-02): add created_at to CAS filters in atomic_claim_job_sqlite and _postgres
- `c2fc7c2`: feat(16-02): add created_at to mark_running/mark_success/mark_failed CAS filters

**Files:**
- `src/sqlery/django_sqlery/db_compat.py` — items 1 & 2: `atomic_claim_job_sqlite` and `atomic_claim_job_postgres` CAS filters now include `created_at=job.created_at` alongside `id` and `version`.
- `src/sqlery/django_sqlery/models.py` — items 3–5: `mark_running`, `mark_success`, `mark_failed` UPDATE filters now include `created_at`. Item 6 (`save_meta`) already filtered `id + created_at` from the Phase 15 audit (verified).

## Notes
- Items 7–11 (backend.py write paths) are owned by plan 16-03.
- `job.created_at` is already in hand on the claim path (full model rows), so no extra query is needed.
