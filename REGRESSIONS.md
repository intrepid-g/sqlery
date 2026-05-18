# REGRESSIONS

A log of bugs that reappeared — what broke, why, and how it was fixed.

Use this file to:
- Track recurring failure patterns
- Inform test prioritization
- Avoid reintroducing the same bug twice

## 2026-05-18 — TransactionManagementError in worker claim loop

**What broke:** Workers crash-loop every ~5s with `TransactionManagementError: select_for_update cannot be used outside of a transaction`. Job claiming was completely broken on PostgreSQL.

**Root cause:** When the claiming logic was promoted from `DjangoBackend.claim_job()` to the framework-agnostic `sqlery.core.claiming` module, the `transaction.atomic()` wrapper was lost. The old code (still visible as a comment block in backend.py) had it; the new delegation call did not. Both `get_claimable_jobs()` and `acquire_tag_locks()` use `select_for_update()`, which Django requires to be inside a transaction.

**Fix:** Wrapped the `claim_next_job_with_queue_priority()` call inside `with transaction.atomic():` in `DjangoBackend.claim_job()` — restoring the same pattern as the old commented-out code.

**Regression test:** `test_claim_job_runs_inside_transaction` in `tests/unit/test_django_backend.py`

**Inline comment:** `# REGRESSION 2026-05-18` at `src/sqlery/django_sqlery/backend.py:109`

**Validation:** All 7 TestEnqueueAndClaim tests pass including the new regression test.
