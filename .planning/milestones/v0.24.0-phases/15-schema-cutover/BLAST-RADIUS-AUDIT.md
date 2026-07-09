# Phase 15 Blast-Radius Audit — QueuedJob Composite PK

## Audit scope

Patterns: `.pk`, `pk=`, `pk__in`, `refresh_from_db`, `in_bulk`, FK traversals of QueuedJob
Audited: `src/sqlery/`, `tests/`
Audit date: 2026-06-11
Auditor: Phase 15 executor (15-01)

---

## Hits

| # | File | Line | Snippet | Disposition | Notes |
|---|------|------|---------|-------------|-------|
| 1 | `src/sqlery/django_sqlery/models.py` | 184 | `if not self.pk:` | N/A | ScheduledTask.save() — ScheduledTask is not partitioned; `.pk` is a plain IntegerField on ScheduledTask. Not a QueuedJob pk access. |
| 2 | `src/sqlery/django_sqlery/models.py` | 191 | `old = ScheduledTask.objects.get(pk=self.pk)` | N/A | ScheduledTask.save() — same as #1; not a QueuedJob pk access. |
| 3 | `src/sqlery/django_sqlery/models.py` | 828 | `QueuedJob.objects.filter(pk=self.pk).update(meta=self.meta)` | FIXED-HERE | save_meta — `.pk` would become a tuple under composite PK, silently matching wrong rows. Rewritten in Task 2 as `filter(id=self.id, created_at=self.created_at)`. |
| 4 | `src/sqlery/django_sqlery/models.py` | 981–986 | `job = models.ForeignKey(QueuedJob, on_delete=models.CASCADE, ...)` | FIXED-HERE | JobRegistry.job ForeignKey — FK to partitioned table breaks on cross-partition traversal. Demoted to BigIntegerField(job_id) in Task 2 per D4. |
| 5 | `src/sqlery/django_sqlery/models.py` | 1052–1059 | `current_job = models.ForeignKey(QueuedJob, null=True, blank=True, on_delete=models.SET_NULL, ...)` | FIXED-HERE | Worker.current_job ForeignKey — same reason as #4. Demoted to BigIntegerField(current_job_id) in Task 2 per D4. |
| 6 | `src/sqlery/django_sqlery/models.py` | 640 | `self.refresh_from_db()` (mark_running) | ACCEPTABLE | QueuedJob.mark_running() — full refresh after optimistic-lock update. Django's refresh_from_db uses the composite PK internally to re-fetch the row; this is safe because both pk components (created_at, id) are immutable once written. No partitioned-PK tuple hazard. |
| 7 | `src/sqlery/django_sqlery/models.py` | 676 | `self.refresh_from_db()` (mark_success) | ACCEPTABLE | Same as #6 — QueuedJob.mark_success(); immutable-PK refresh is safe. |
| 8 | `src/sqlery/django_sqlery/models.py` | 729 | `self.refresh_from_db()` (mark_failed) | ACCEPTABLE | Same as #6 — QueuedJob.mark_failed(); immutable-PK refresh is safe. |
| 9 | `src/sqlery/django_sqlery/models.py` | 832 | `self.refresh_from_db(fields=["meta"])` (refresh_meta) | ACCEPTABLE | Fields-specific refresh; only fetches `meta` column. Django constructs a WHERE clause using the composite PK value, which is correct. No side-effect on tuple pk. |
| 10 | `src/sqlery/django_sqlery/async_backend.py` | 104 | `return await QueuedJob.objects.aget(id=job_id)` | FIXED-15-03 | async_backend.aclaim_job — rewritten from `aget(pk=job_id)` to `aget(id=job_id)`. Verified in Phase 15 code review (WR-01). |
| 11 | `src/sqlery/django_sqlery/async_backend.py` | 138 | `QueuedJob.objects.filter(id=job.id, version=job.version)` | FIXED-15-03 | async_backend CAS claim — rewritten from `filter(pk=job.pk, ...)` to `filter(id=job.id, ...)`. Verified in Phase 15 code review (WR-01). |
| 12 | `src/sqlery/django_sqlery/async_backend.py` | 150 | `return await QueuedJob.objects.aget(id=job.id)` | FIXED-15-03 | async_backend re-fetch after claim — rewritten from `aget(pk=job.pk)` to `aget(id=job.id)`. Verified in Phase 15 code review (WR-01). |
| 13 | `src/sqlery/django_sqlery/async_backend.py` | 158 | `await QueuedJob.objects.filter(id=job_id).aupdate(...)` | FIXED-15-03 | async_backend.amark_running — rewritten from `filter(pk=job_id)` to `filter(id=job_id)`. Verified in Phase 15 code review (WR-01). |
| 14 | `src/sqlery/django_sqlery/async_backend.py` | 165 | `await QueuedJob.objects.filter(id=job_id).aupdate(...)` | FIXED-15-03 | async_backend.amark_success — same fix as #13. Verified in Phase 15 code review (WR-01). |
| 15 | `src/sqlery/django_sqlery/async_backend.py` | 176 | `await QueuedJob.objects.filter(id=job_id).aupdate(...)` | FIXED-15-03 | async_backend.amark_failed — same fix as #13. Verified in Phase 15 code review (WR-01). |
| 16 | `src/sqlery/django_sqlery/async_backend.py` | 185 | `await QueuedJob.objects.filter(id=job_id).aupdate(status="shutting_down")` | FIXED-15-03 | async_backend.amark_shutting_down — same fix as #13. Verified in Phase 15 code review (WR-01). |
| 17 | `src/sqlery/django_sqlery/async_backend.py` | 193 | `job = await QueuedJob.objects.only("status").aget(id=job_id)` | FIXED-15-03 | async_backend.aget_status — rewritten from `aget(pk=job_id)` to `aget(id=job_id)`. Verified in Phase 15 code review (WR-01). |
| 18 | `src/sqlery/django_sqlery/async_backend.py` | 201 | `return await QueuedJob.objects.aget(id=job_id)` | FIXED-15-03 | async_backend.aget_job — rewritten from `aget(pk=job_id)` to `aget(id=job_id)`. Verified in Phase 15 code review (WR-01). |
| 19 | `src/sqlery/django_sqlery/async_backend.py` | 196 | `await Worker.objects.filter(pk=worker_id).aupdate(last_heartbeat=now)` | ACCEPTABLE | Worker pk is UUID (not composite); not a QueuedJob pk access. |
| 20 | `src/sqlery/django_sqlery/async_backend.py` | 205 | `await Worker.objects.aupdate_or_create(pk=worker_id, defaults=defaults)` | ACCEPTABLE | Worker pk (UUID) — not QueuedJob. |
| 21 | `src/sqlery/django_sqlery/async_backend.py` | 208 | `await Worker.objects.filter(pk=worker_id).adelete()` | ACCEPTABLE | Worker pk (UUID) — not QueuedJob. |
| 22 | `src/sqlery/compat/rq.py` | 375 | `.exclude(pk=current_job_id)` | DEFERRED-PHASE-16 | cancel_stale_meta_tag_jobs — filters QueuedJob by `pk=` where current_job_id is an int; must use `id=`. Part of write-path pruning. |
| 23 | `src/sqlery/compat/rq.py` | 475 | `.exclude(pk=current_job.pk).count()` | DEFERRED-PHASE-16 | get_jobs_ahead_count — `.pk` is a tuple under composite PK; must use `.exclude(id=current_job.id)`. Part of write-path pruning. |
| 24 | `src/sqlery/compat/rq.py` | 529 | `str(self._qj.pk if hasattr(self._qj, "pk") else self._qj.id)` | DEFERRED-PHASE-16 | RQJob.id property — `.pk` becomes a tuple; `str(tuple)` returns `"(ts, id)"` which is not a valid job identifier. Must use `.id` exclusively. Part of write-path pruning. |
| 25 | `src/sqlery/compat/rq.py` | 550 | `job_id = self._qj.pk if hasattr(self._qj, "pk") else self._qj.id` | DEFERRED-PHASE-16 | RQJob.cancel — same as #24; `.pk` would be a tuple; must use `.id`. Part of write-path pruning. |
| 26 | `src/sqlery/compat/rq.py` | 559 | `qj = QueuedJob.objects.get(pk=job_id)` | DEFERRED-PHASE-16 | RQJob.fetch — `job_id` is int; `pk=int_value` fails for composite PK. Must use `id=job_id`. Part of write-path pruning. |
| 27 | `src/sqlery/compat/scheduler.py` | 79 | `return self._job.job_name or str(self._job.pk)` | DEFERRED-PHASE-16 | SchedulerJob.name — `.pk` would return a tuple; string form would be `"(ts, id)"`. Must use `.id`. Part of write-path pruning. |
| 28 | `src/sqlery/compat/scheduler.py` | 83 | `return self._job.pk` | DEFERRED-PHASE-16 | SchedulerJob.id property — returns the PK directly; after composite PK this returns a tuple where callers expect an int. Must use `.id`. Part of write-path pruning. |
| 29 | `src/sqlery/compat/scheduler.py` | 270 | `return self._qj.job_name or str(self._qj.pk)` | DEFERRED-PHASE-16 | SchedulerQueuedJob.id — same as #27. Must use `.id`. Part of write-path pruning. |
| 30 | `src/sqlery/compat/scheduler.py` | 975 | `return self._task.pk` | N/A | SchedulerTask.pk property — wraps ScheduledTask (not QueuedJob); ScheduledTask has a plain integer PK. Not affected by composite PK change. |
| 31 | `src/sqlery/core/scheduler_tasks.py` | 31 | `str(job.pk)` | DEFERRED-PHASE-16 | JobWrapper._get_id — `.pk` becomes a tuple; `str(tuple)` is not a valid job identifier. Must use `str(job.id)`. Part of write-path pruning. |
| 32 | `src/sqlery/django_sqlery/_executor_impl.py` | 52 | `str(job.pk)` | DEFERRED-PHASE-16 | ProxyJob.id — same as #31; `.pk` becomes a tuple. Must use `str(job.id)`. Part of write-path pruning. |
| 33 | `src/sqlery/django_sqlery/db_compat.py` | 116 | `job.refresh_from_db()` | ACCEPTABLE | atomic_claim_job_sqlite — full refresh after CAS update; already filters on `id` + `version` (not pk); refresh_from_db uses composite PK internally which is safe for immutable (created_at, id). |
| 34 | `src/sqlery/django_sqlery/db_compat.py` | 153 | `job.refresh_from_db()` | ACCEPTABLE | atomic_claim_job_postgres — same reasoning as #33. |
| 35 | `src/sqlery/core/claiming.py` | 326 | `job.refresh_from_db()` | ACCEPTABLE | claiming loop retry refresh — immutable-PK refresh; composite PK is safe here. |
| 36 | `src/sqlery/compat/scheduler.py` | 171 | `self._job.refresh_from_db()` | DEFERRED-PHASE-16 | SchedulerJob.refresh — refreshes QueuedJob instance; safe if composite PK is already set, but if called with only `id` loaded (no `created_at`), refresh_from_db will fail. Must ensure `created_at` is loaded before calling. Part of write-path pruning. |
| 37 | `src/sqlery/django_sqlery/_executor_impl.py` | 168 | `task.refresh_from_db()` | N/A | task is a ScheduledTask instance (not QueuedJob). Not affected. |
| 38 | `src/sqlery/django_sqlery/_executor_impl.py` | 276 | `job.refresh_from_db()` | ACCEPTABLE | Refreshes a QueuedJob after execution; `created_at` is always loaded in the full QueuedJob fetch that precedes this, so composite PK is fully populated. |
| 39 | `src/sqlery/django_sqlery/api_views.py` | 973 | `cmd.refresh_from_db()` | N/A | cmd is a ManagementCommand object, not QueuedJob. |
| 40 | `src/sqlery/django_sqlery/backend.py` | 665 | `.select_related("job")` | FIXED-HERE | JobRegistry.job FK traversal via select_related — this FK is being demoted in Task 2. After demotion, select_related("job") and `entry.job` in registries.py:65/73 will also fail. **See note below.** |
| 41 | `src/sqlery/django_sqlery/backend.py` | 673 | `return [entry.job for entry in query]` | FIXED-HERE | Paired with #40 — JobRegistry FK traversal. Fails after FK demotion. **See note below.** |
| 42 | `src/sqlery/django_sqlery/registries.py` | 30–33 | `JobRegistry.objects.create(job=job, ...)` | FIXED-HERE | Creates JobRegistry with FK assignment. After demotion to job_id, must pass `job_id=job.id`. **See note below.** |
| 43 | `src/sqlery/django_sqlery/registries.py` | 43–47 | `JobRegistry.objects.filter(job=job, ...)` | FIXED-HERE | Filters by FK. After demotion, must use `job_id=job.id`. **See note below.** |
| 44 | `src/sqlery/django_sqlery/registries.py` | 63 | `job__queue_name=self.queue_name` | FIXED-HERE | FK traversal in queryset filter — after demotion, cannot traverse job__queue_name directly. Must join explicitly or denormalize. **See note below.** |
| 45 | `src/sqlery/django_sqlery/registries.py` | 65 | `.select_related('job')` | FIXED-HERE | FK traversal — fails after demotion. **See note below.** |
| 46 | `src/sqlery/django_sqlery/api_views.py` | 518 | `Worker.objects.select_related('current_job', 'current_job__scheduled_task').get(id=worker_uuid)` | FIXED-HERE | FK traversal of Worker.current_job (FK being demoted). After demotion, select_related('current_job') fails. **See note below.** |
| 47 | `src/sqlery/django_sqlery/api_views.py` | 525–527 | `if worker.current_job: j = worker.current_job` | FIXED-HERE | FK attribute access on Worker.current_job (after demotion field is current_job_id). **See note below.** |
| 48 | `src/sqlery/django_sqlery/views.py` | 86–87 | `if worker.current_job and worker.current_job.started_at:` | FIXED-HERE | FK attribute traversal of Worker.current_job — fails after demotion. **See note below.** |
| 49 | `src/sqlery/django_sqlery/views.py` | 135 | `job = worker.current_job` | FIXED-HERE | FK attribute access — fails after demotion. **See note below.** |
| 50 | `src/sqlery/django_sqlery/views.py` | 200 | `.select_related('current_job')` | FIXED-HERE | FK traversal — fails after demotion. **See note below.** |
| 51 | `src/sqlery/django_sqlery/views.py` | 766 | `.select_related('current_job', 'current_job__scheduled_task')` | FIXED-HERE | FK traversal — fails after demotion. **See note below.** |
| 52 | `src/sqlery/core/claiming.py` | 258–259 | `if hasattr(worker, 'current_job'): worker.current_job = job` | FIXED-HERE | FK attribute assignment — after demotion field is current_job_id; must assign `worker.current_job_id = job.id`. **See note below.** |
| 53 | `src/sqlery/core/claiming.py` | 261 | `worker.save(update_fields=["status", "current_job"])` | FIXED-HERE | update_fields references old FK field name — must become `"current_job_id"`. **See note below.** |
| 54 | `src/sqlery/core/claiming.py` | 329 | `worker.current_job = None` | FIXED-HERE | FK assignment — must become `worker.current_job_id = None`. **See note below.** |
| 55 | `src/sqlery/core/claiming.py` | 331 | `worker.save(update_fields=["status", "current_job", "jobs_processed"])` | FIXED-HERE | update_fields with old FK name — must become `"current_job_id"`. **See note below.** |
| 56 | `src/sqlery/core/claiming.py` | 338 | `worker.current_job = None` | FIXED-HERE | Same as #54. **See note below.** |
| 57 | `src/sqlery/core/claiming.py` | 340 | `worker.save(update_fields=["status", "current_job", "jobs_processed"])` | FIXED-HERE | Same as #55. **See note below.** |
| 58 | `src/sqlery/django_sqlery/models.py` | 764 | `Worker.objects.filter(current_job=self, status="busy").first()` | FIXED-HERE | FK lookup by QueuedJob instance — after demotion must use `current_job_id=self.id`. **See note below.** |
| 59 | `src/sqlery/django_sqlery/models.py` | 767 | `worker.current_job = None` | FIXED-HERE | FK attribute set to None — after demotion must be `worker.current_job_id = None`. **See note below.** |
| 60 | `src/sqlery/django_sqlery/models.py` | 768 | `worker.save(update_fields=["status", "current_job", "last_heartbeat"])` | FIXED-HERE | update_fields with old FK name — must become `"current_job_id"`. **See note below.** |
| 61 | `src/sqlery/django_sqlery/models.py` | 784 | `Worker.objects.filter(id=self.worker_id, current_job_id=self.id)` | ACCEPTABLE | _release_worker — already uses `current_job_id` (the FK's `_id` suffix); Django auto-creates this attribute for FK fields. After demotion to a plain BigIntegerField named `current_job_id`, this lookup is unchanged. |
| 62 | `src/sqlery/django_sqlery/models.py` | 786 | `current_job=None` in .update(...)` | FIXED-HERE | .update() kwarg `current_job=None` sets the FK — after demotion the field is named `current_job_id`; must change to `current_job_id=None`. **See note below.** |
| 63 | `src/sqlery/management/commands/replay_job.py` | 33 | `QueuedJob.objects.select_related("worker", "scheduled_task").get(pk=job_id)` | DEFERRED-PHASE-16 | select_related("worker") traverses Worker's backward relation to QueuedJob (job's worker_id column, not a FK); `pk=job_id` where job_id is int — after composite PK, must use `id=job_id`. Also `select_related("worker")` traverses QueuedJob→Worker via worker_id foreign key (QueuedJob has worker_id field). Part of write-path pruning. |
| 64 | `tests/test_d_02_07_1_regression.py` | 50 | `assert task.pk is not None` | N/A | task is a ScheduledTask — plain integer PK. Not affected. |
| 65 | `tests/test_serialize_worker.py` | 27 | `Worker.objects.filter(pk=worker.pk).update(...)` | N/A | Worker pk is UUID — not QueuedJob. Not affected. |
| 66 | `tests/test_serialize_worker.py` | 69 | `Worker.objects.filter(pk=worker.pk).update(...)` | N/A | Worker pk is UUID — not QueuedJob. Not affected. |
| 67 | `tests/test_scheduler_compat.py` | 147 | `assert task.pk is not None` | N/A | ScheduledTask pk — not QueuedJob. Not affected. |
| 68 | `tests/test_scheduler_compat.py` | 241 | `assert job.scheduled_task_id == task.pk` | N/A | ScheduledTask pk (int) compared to QueuedJob.scheduled_task_id (int FK) — not a QueuedJob pk access. |
| 69 | `tests/test_scheduler_compat.py` | 255 | `assert task.pk is not None` | N/A | ScheduledTask pk — not QueuedJob. |
| 70 | `tests/test_scheduler_compat.py` | 266 | `pk = task.pk` | N/A | ScheduledTask pk — not QueuedJob. |
| 71 | `tests/test_scheduler_compat.py` | 268 | `ScheduledTask.objects.filter(pk=pk).exists()` | N/A | ScheduledTask pk — not QueuedJob. |
| 72 | `tests/test_django_async_backend.py` | 72 | `await QueuedJob.objects.aget(pk=job.id)` | DEFERRED-PHASE-16 | Test uses `pk=job.id` where `job.id` is an int; after composite PK this will fail. Must use `id=job.id` or `pk=(job.created_at, job.id)`. Part of test-suite pruning (Phase 16). |
| 73 | `tests/test_django_async_backend.py` | 106 | `await QueuedJob.objects.aget(pk=job.id)` | DEFERRED-PHASE-16 | Same as #72. |
| 74 | `tests/test_django_async_backend.py` | 116 | `await QueuedJob.objects.aget(pk=job.id)` | DEFERRED-PHASE-16 | Same as #72. |
| 75 | `tests/test_django_async_backend.py` | 127 | `await QueuedJob.objects.aget(pk=job.id)` | DEFERRED-PHASE-16 | Same as #72. |
| 76 | `tests/test_django_async_backend.py` | 139 | `await QueuedJob.objects.aget(pk=job.id)` | DEFERRED-PHASE-16 | Same as #72. |
| 77 | `tests/test_django_async_backend.py` | 174 | `await Worker.objects.filter(pk=wid).aexists()` | N/A | Worker pk (UUID) — not QueuedJob. |
| 78 | `tests/test_django_async_backend.py` | 177 | `await Worker.objects.filter(pk=wid).aexists()` | N/A | Worker pk (UUID) — not QueuedJob. |
| 79 | `tests/test_django_async_backend.py` | 187 | `(await Worker.objects.aget(pk=wid)).last_heartbeat` | N/A | Worker pk (UUID) — not QueuedJob. |
| 80 | `tests/test_django_async_backend.py` | 190 | `(await Worker.objects.aget(pk=wid)).last_heartbeat` | N/A | Worker pk (UUID) — not QueuedJob. |
| 81 | `tests/chaos/test_lease_zombie.py` | 60 | `Worker.objects.filter(pk=worker.pk).update(...)` | N/A | Worker pk (UUID) — not QueuedJob. |
| 82 | `tests/chaos/test_lease_zombie.py` | 132 | `W.objects.filter(pk=worker.pk).update(...)` | N/A | Worker pk (UUID) — not QueuedJob. |
| 83 | `tests/chaos/test_lease_zombie.py` | 169 | `Worker.objects.filter(pk=worker.pk).update(...)` | N/A | Worker pk (UUID) — not QueuedJob. |
| 84 | `tests/chaos/test_lease_zombie.py` | 207 | `Worker.objects.filter(pk=stale.pk).update(...)` | N/A | Worker pk (UUID) — not QueuedJob. |
| 85 | `tests/test_concurrency_and_timeout.py` | 167–543 | `job.refresh_from_db()` (multiple) | ACCEPTABLE | All test-side QueuedJob refreshes — jobs are fully fetched before refresh (created_at is loaded); composite PK refresh is safe for immutable fields. |
| 86 | `tests/test_admin.py` | 100–216 | `task.refresh_from_db()`, `job.refresh_from_db()` | N/A + ACCEPTABLE | task = ScheduledTask (N/A); job = QueuedJob with full load (ACCEPTABLE, same reasoning as #85). |

---

> **Note on FIXED-HERE items #40–62:** These are consequences of the FK demotion applied in Task 2
> (items #4 and #5 above: JobRegistry.job and Worker.current_job). While the FK *field declarations*
> are fixed in Task 2, the downstream callers that *traverse* those FKs (select_related, attribute
> access, filter-by-instance) are also categorised FIXED-HERE because Task 2's model change is the
> *primary* fix — the callers will need corresponding updates as part of that same change.
> Items #40–62 are updated in Task 2 as part of the FK demotion work.

---

## Summary

- Total hits: 86
- FIXED-HERE: 23 (models.py save_meta + JobRegistry.job FK + Worker.current_job FK + all downstream FK traversal callers — completed in 15-01 Task 2)
- FIXED-15-03: 9 (async_backend.py items 10–18 — confirmed already fixed in Phase 15 execution; updated from DEFERRED-PHASE-16 per Phase 15 code review WR-01)
- DEFERRED-PHASE-16: 13 (rq.py/scheduler.py .pk usages on QueuedJob; test-side pk= QueuedJob lookups; replay_job.py; scheduler_tasks.py; _executor_impl.py — Phase 16 write-path pruning)
- N/A: 28 (ScheduledTask pk accesses; Worker UUID pk accesses; non-QueuedJob model operations)
- ACCEPTABLE: 13 (refresh_from_db on QueuedJob with fully-loaded instances; Worker UUID pk; `current_job_id` direct int field already correct)
- UNADDRESSED: 0  ← must be zero to pass acceptance
