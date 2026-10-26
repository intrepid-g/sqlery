# Queue robustness on PostgreSQL

Goal: no unbounded degradation of the queue tables when PostgreSQL cannot
vacuum (pinned xmin horizon, long transactions), preferring robustness over
speed. Every step must work in both integration modes (Django, standalone)
and on SQLite, where the same tables exist without partitioning.

Issues: #135 claim starvation, #136 index audit, #137 xmin guards,
#138 attempt table, #139 ready queue, #140 history, #141 rotation.

## Tables (end state)

| Table | Contents | Writes per job |
|-------|----------|----------------|
| `sqlery_queued_job` | live job payload + status (queued/running) | insert, status updates (no claiming indexes on it after #139) |
| `sqlery_job_attempt` | one row per execution attempt | insert on claim, child_pid, finish |
| `sqlery_job_ready` | narrow claimable entries | insert on enqueue, delete on claim |
| `sqlery_job_blocked` | jobs whose dependencies are unmet | insert/delete when blocking/unblocking |
| `sqlery_job_history` | terminal jobs, insert-only | one insert |

## #138 Attempt table

`sqlery_job_attempt(id bigint pk, job_id bigint, token varchar(36), worker_id,
worker_pid, child_pid null, claimed_at, finished_at null, outcome null)`;
`outcome in ('success','failed','abandoned')`. Index `(job_id)` and a partial
index on `(worker_id) WHERE finished_at IS NULL`.

- Claim: in the claim transaction insert an attempt with a fresh token.
  The claimed job carries its attempt id and token in memory.
- child_pid is written to the attempt, not the job row.
- Finish (success/failure/retry) in one transaction:
  `UPDATE attempt SET finished_at=now(), outcome=:o WHERE id=:id AND token=:t
  AND finished_at IS NULL`; rowcount must be 1, else the completion is stale:
  raise `JobFencingError` (already exists) and do not touch the job row.
  The existing `version` CAS stays as a second fence.
- Recovery (zombie detection) closes the open attempt with outcome
  `abandoned` (same predicate) before requeueing; a late completion from the
  abandoned attempt is then rejected.
- Readers that used `queued_job.child_pid` / `worker_pid` (kill, zombie
  detection, dashboard) read the open attempt. The old columns stay but are no
  longer written (`# #CLEANUP:` note).
- Workers refuse to start when the attempt table is missing (clear error:
  run migrations, stop old workers first). Docs: stop all workers before
  migrating.
- Claim abandons any attempt still open for the job it claims (a leftover of a
  requeue that skipped closing), so a job never has two open attempts and the
  previous holder is fenced.
- A completion that carries no attempt handle (admin stop, recovery sweeps,
  watchdog, a job that was never claimed) closes every open attempt of the job:
  `success` for a success, otherwise `abandoned`.
- Readers fall back to the old `worker_pid` / `child_pid` columns only for jobs
  that have no attempt (claimed before the migration).
- Attempt rows are not pruned by this step.

## #139 Ready queue

`sqlery_job_ready(slot smallint default 0, job_id bigint, queue_name,
priority, created_at, scheduled_at null)`, primary key `(slot, job_id)`.
On PostgreSQL it is `PARTITION BY LIST (slot)` with partitions for slots
0, 1, 2 (only slot 0 is used until #141 is enabled). On SQLite it is a plain
table. Claim index: `(queue_name, priority DESC, created_at, job_id)`.

- Enqueue inserts the job row and its ready entry in the same transaction.
  Far-future jobs keep using the existing `ScheduledJob` staging; promotion
  inserts the ready entry.
- Claim (one short READ COMMITTED transaction): select ready entries in
  order (`FOR UPDATE SKIP LOCKED` on PostgreSQL), check eligibility, delete the
  ready entry (`(slot, job_id)`), set the job running, insert the attempt.
  SQLite: `DELETE ... WHERE slot=? AND job_id=?` with rowcount 1 is the claim.
- Dependency-blocked candidates are moved to `sqlery_job_blocked` (delete
  ready entry, insert blocked) instead of being skipped every claim. When a job
  reaches a terminal state, its dependents in the blocked set are re-checked
  and moved back to ready (or failed, as today). Tag-limited candidates stay
  in ready (the condition is transient and bounded by the limit).
- Retries, recovery requeues and cancellations insert/delete ready entries.
- Data migration: every queued job gets a ready entry (or blocked entry).
- After claiming no longer reads `sqlery_queued_job`, drop its claiming
  indexes so status updates on the job row can be HOT.

### #139 implementation notes

What the code does where the section above leaves room, and where it deviates.

- **Blocked set shape.** `sqlery_job_blocked(job_id, dependency_id, created_at, blocked_at)`,
  primary key `(job_id, dependency_id)`, index on `dependency_id`: one row per dependency of
  the blocked job, so "who waits on X" is an index lookup, and `created_at` (the job's
  partition key) lets a re-check read the job by primary key.
- **Every path that makes a job claimable or unclaimable writes its entry in the same
  transaction as the job row**: enqueue (`create_job`, so also bulk / canvas / group / chain),
  staging promotion (same transaction as the insert), scheduler-created jobs and retries
  (both go through `create_job`), requeues (`release_job`, `retry_failed_jobs`, admin
  re-queue), cancel / TTL expiry / dependency failure / archive (entries are removed),
  `job_name` replacement (entries removed, jobs that waited on the replaced job fail as a
  missing dependency always did), `claim_job_by_id` (Lambda / HTTP trigger), the async
  workers and the legacy `run_jobs` executor. In Django mode `QueuedJob.save()` /
  `.delete()` and `QueuedJob.objects.update(status="queued" | queue | priority | scheduled_at)`
  carry the bookkeeping, so code that goes through the ORM cannot forget it; in standalone
  mode a session hook does the same for ORM writes, and Core `UPDATE` statements are
  handled explicitly in the backend.
- **Lock order is ready entry first, then job row** in every writer. A claimer holds the
  entry (`FOR UPDATE SKIP LOCKED`) and then updates the job row; a writer that did it the
  other way round (job row, then entry) could deadlock with it.
- **The claim does not use the job `version` as its mutex** any more: deleting the entry
  (rowcount 1) is the claim, and the job update still requires `status = 'queued'`. If the
  update finds the job changed under an entry the claimer owns, the entry is put back when
  the job is still queued, so a claim can never lose a queued job.
- **Stale entries are harmless.** An entry whose job is gone or not queued is dropped by the
  claim that meets it (the claim reads the job row by `(created_at, id)`, falling back to the
  id), so cancels done by raw SQL cannot make a job runnable.
- **Dependency release.** When a job reaches a terminal state (success, final failure,
  cancel, zombie failure, deletion) the jobs blocked on it, or on its retry ancestors, are
  re-checked: all met -> back to ready, one failed -> failed (`dependency_failed`) and its
  own dependents are released in turn. A failed dependency that is tagged `retry_pending`
  and has no retry yet counts as pending, not failed (standalone mode; the worker releases
  after the retry exists). After a job is moved to the blocked set it is re-checked once
  more after the claim transaction commits, so a dependency that finished while the rows were
  not yet visible cannot be missed.
- **Sweep (addition).** `maintain_ready_queue`, run by the scheduler leader of a queue
  (default every 60 s, `READY_QUEUE_SWEEP_INTERVAL`, 0 disables; PostgreSQL statements are
  capped by `READY_QUEUE_SWEEP_TIMEOUT`, default 10 s): queued jobs with no ready or blocked
  entry get a ready entry, entries of jobs that are no longer queued are dropped, and a page
  of blocked jobs is re-judged. It is the safety net for raw SQL and third-party code that
  changes `sqlery_queued_job` without going through the library: a lost entry delays a job by
  one sweep, it never loses it.
- **Migration.** Every queued job gets a ready entry (none is put in the blocked set; the
  first claim that meets a dependency-blocked job moves it). Workers refuse to start when the
  tables are missing (like #138's attempt table); stop all workers before migrating.
- **Indexes.** Only the claiming-only partial index `sqlery_job_pending_idx` is dropped. The
  `status`, `started_at`, `finished_at`, `worker_id` and `scheduled_at` indexes stay because
  other queries need them (dashboards, rate limits, cleanup, zombie detection), so a status
  update is still not HOT; what goes away is the partial-index entry written and killed by
  every claim.
- **Async workers** read the ready queue but, as before, apply no dependency / tag / TTL
  checks to what they claim (the sync claim and the Django async claim, which shares it, do).

## #140 History

Terminal transition = one transaction: `INSERT INTO sqlery_job_history
SELECT ...` + `DELETE FROM sqlery_queued_job`. Read paths (get by id,
results, dependencies "succeeded?", dashboard/API lists and stats, retry
chains) consult history too. Retention: history is partitioned by
`finished_at` on partitioned PostgreSQL installs and reclaimed by DROP via
`core/partitioning.py`; otherwise batched deletes as today.

Attempt retention: `sqlery_job_attempt` is pruned under the same retention as
history. On PostgreSQL it is `PARTITION BY RANGE (claimed_at)` and dropped
with the same tick (a partition with an open attempt is kept); elsewhere the
finished attempts of every history row that retention deletes are deleted in
the same batch.

### #140 implementation notes

- **Tables.** `sqlery_job_history` has every column of `sqlery_queued_job`
  (PostgreSQL: created with `LIKE`, so the types always match), primary key
  `(finished_at, id)`, indexes on `id`, `parent_job_id`, `(status, finished_at
  DESC)`, `job_name`, `created_at DESC`. `sqlery_job_all` is a view
  (`UNION ALL` of the live table and history) that every reader of "any
  job" uses (Django `JobAll`, SQLModel `JobAll`); `get_job_by_id` returns the
  live row, else the history row (`JobHistory`, read-only).
- **The transition** is `INSERT .. SELECT` (the final values replace the
  columns that change) + `DELETE`, both fenced by the row's `version` (and
  status where the caller needs one), after the attempt close and, on
  PostgreSQL, a `FOR UPDATE` row lock. Zero rows in either statement is a stale
  completion: `JobFencingError`, nothing is written. A completion for a job that
  is already in history is stale too (an unfenced caller is ignored).
  Entry points: `mark_job_success/failed`, cancel, TTL expiry, dependency
  failure, zombie/recovery failure, admin/intervention `update(status=<terminal>)`
  (the Django `QuerySet.update` and `QueuedJob.save` do it for any terminal
  status), `release_claimed_job`, the async backends.
- **Retries.** A failure that will be retried carries the existing
  `retry_pending` marker and stays in the live table, marked `failed`, until
  the retry exists; `mark_job_archived` then moves it as `archived` and
  `settle_retry_pending` moves it as `failed` (no retry owed). The chain
  (`parent_job_id`) is resolved across both tables (`get_latest_retry`,
  dependency judgement, `AsyncResult`, blocked-set release). The async workers
  mark a failure in place only when a retry budget is left, with the same effect.
  `retry_failed_jobs` can no longer re-queue a history row: it creates a fresh
  job with `parent_job_id` = the failed job (once per failure).
- **Settle sweep.** `maintain_ready_queue` (scheduler leader) also moves
  terminal rows that were left in the live table (raw SQL, a crash between the
  steps of a retry) into history once they are older than
  `HISTORY_SETTLE_GRACE_SECONDS` (60); rows owing a retry are left alone.
- **Webhooks (Django).** Delivery bookkeeping is mutable, so a job with a
  `webhook_url` stays in the live table, finished, until the delivery resolves
  (sent, or out of retries); `send_webhook_with_retry` then moves it.
- **Retention.** `cleanup_jobs` only ever touches history (never live jobs);
  its age is the time since `finished_at`. Partitioned PostgreSQL: the daemon
  creates and drops daily partitions of the live table, history and the attempt
  table (`core.history.ensure_partitions / reclaim_partitions`); the archive
  hook sees history partitions. Elsewhere: batched deletes of history and the
  finished attempts of the deleted rows.
- **Migrations.** Django 0039 (schema) + 0040 (data, batched, resumable, not
  atomic); Alembic `20261026_0021`; standalone start-up creates the same objects
  and moves the rows. The reverse moves the rows back.
- **Indexes.** The `started_at DESC` / `finished_at DESC` indexes of the live
  table are dropped (Django): finished jobs are found in history. `status`,
  `queue_name`, `priority`, `parent_job_id`, `job_name`, `scheduled_at`,
  `worker_id`, `scheduled_task_id` stay: live `queued` / `running` lookups, retry
  chains, named jobs, scheduled-task checks and orphan detection use them. Because
  `status` stays indexed, a status update is still not HOT (see deviations).

### #140 deviations from the section above

- `sqlery_job_attempt` is partitioned on PostgreSQL (migration converts it) so it
  can be reclaimed by DROP; its primary key becomes `(claimed_at, id)`.
- A failed job owing a retry, and a finished job whose webhook is being
  delivered, are in the live table for a short while (the live table is
  "queued / running" plus these transient rows).
- Status updates on the live table are not HOT: the `status` index is still
  needed to find `running` / `queued` jobs. Making them HOT needs running-job
  lookups to move to the open-attempt table (follow-up).
- A final failure is `failed` in history even if someone later creates a retry
  for it by hand (history is not rewritten to `archived`).

## #156 Live-table storage bound (partitioned PostgreSQL)

`sqlery_queued_job` holds only work in flight (finished jobs live in history), so a
`created_at` partition that has passed and has **no row at all** contains nothing but
dead tuples. With the xmin horizon pinned VACUUM cannot clear them, so waiting for
`SQLERY_PARTITION_RETENTION` (30 days) would let the live table grow by retention x rate.
Retention therefore no longer applies to the live table (history and attempts keep it):

- **Drop rule.** A live partition is dropped when it is not the DEFAULT partition, its
  upper bound is older than now minus `SQLERY_LIVE_PARTITION_GRACE` (default `1 hour`,
  never below 1 minute) and it holds no row of any status (queued, running,
  retry-pending, webhook-pending, anything). The decision is the pure function
  `partitioning.decide_partition_reclaim`.
- **Storage bound.** With the default width of one hour the live table holds at most
  (rows in flight) + about 2 hours of dead tuples per past partition that could not yet
  be dropped, independent of retention and of the horizon.
- **Order of operations per partition**, in one maintenance tick under the existing
  advisory lock (one maintainer at a time): (1) cheap unlocked `EXISTS` look; a partition
  with rows is never locked. (2) A transaction with `SET LOCAL lock_timeout = 50ms`:
  `LOCK TABLE part IN ACCESS EXCLUSIVE MODE NOWAIT` as its first statement, emptiness
  re-checked under the lock (an insert needs a lock on the partition, so none can slip in:
  it either committed before, and is seen, or waits and then fails or re-routes after the
  drop), `DETACH PARTITION`, `DROP TABLE`, commit. A late `created_at` (backfill, clock
  skew) into a dropped range lands in the DEFAULT partition, which `check_default_partition`
  reports and is never dropped.
- **No stalls.** The partition lock is NOWAIT; the parent lock that DETACH needs gets
  50 ms and is abandoned after that (a pending ACCESS EXCLUSIVE request makes later
  claims and enqueues queue, so the window is bounded). Any failure rolls back and the
  partition is retried next tick. The emptiness scans have a 5 s statement timeout.
  A REPEATABLE READ transaction that read the partition is simply skipped.
- **`DETACH ... CONCURRENTLY` is not used.** PostgreSQL refuses it on a table with a
  DEFAULT partition (this table has one, so stray values are never rejected), and it cannot
  run inside a transaction block, so the emptiness check and the detach could not be
  atomic; an interrupted run also leaves a half-detached partition to FINALIZE. The
  plain DETACH is held for milliseconds on a table whose partition is already empty and
  locked.
- **Width.** `SQLERY_LIVE_PARTITION_INTERVAL` (default `1 hour`) applies to the live table
  only; `SQLERY_PARTITION_INTERVAL` (`1 day`) stays for history and attempts. Coverage
  is premade at least `max(premake x width, 1 day)` ahead (`ensure_live_partitions`).
  Existing partitions are never repartitioned: the walk starts at the bucket holding now,
  jumps over ranges that exist (the daily ones of older installs) and clips a new
  partition to the next existing one, so mixed widths and off-grid boundaries leave
  neither overlap nor gap. Set the interval to `1 day` to keep the old shape.
- **Maintenance cadence.** The standalone default of
  `PARTITION_MAINTENANCE_INTERVAL_MINUTES` is 5 (it was 1440, which would drop drained
  partitions once a day); the daemon warns when a configured cadence exceeds the live width.
- **Health.** The `xmin_horizon` block gains `live_partitions`: the number and age
  (seconds past the drop deadline) of drained partitions that still exist, and the number
  still holding rows. Health is `degraded` when one has been undroppable for longer than
  `PG_LIVE_PARTITION_STUCK_DEGRADED_SECONDS` (900; 0 disables). Candidates are judged by
  `n_live_tup` and, for the oldest ten, an exact `EXISTS` with a 3 s cap.

## #141 Rotation (opt-in, PostgreSQL only)

Reclaims `sqlery_job_ready` by `TRUNCATE` instead of VACUUM.

State is derived, not stored: `current = last_value(sqlery_ready_gen_seq) % 3`
is the slot writers use; any other non-empty slot is draining; empty slots are
free. A sequence is used because reading it is not MVCC: writers in
REPEATABLE READ still see the current generation.

Writers (enqueue, promotion, unblock, retry, recovery requeue), inside their
transaction: `pg_advisory_xact_lock_shared(K)`, then read the sequence, then
insert with that slot. Claims (deletes) take no lock.

Rotator (single instance under the daemon lease, each step its own short
transaction, safe to crash and rerun at any point):

1. Switch, when the rotation interval has elapsed: `pg_try_advisory_xact_lock(K)`
   (never wait). If not acquired, retry next tick. If the next slot is not
   empty, do not switch (see "falling behind"). Otherwise `nextval` and commit.
   Holding K exclusively proves no writer that read the old generation is
   still open; later writers read the new one.
2. Drain each draining slot in bounded batches: move rows to the current slot
   with `DELETE ... RETURNING` + `INSERT` in one statement, rows chosen with
   `FOR UPDATE SKIP LOCKED` (rows being claimed are left to the claimer).
3. Reclaim: `LOCK TABLE <slot partition> IN ACCESS EXCLUSIVE MODE NOWAIT`.
   If it fails, retry next tick. Under the lock, move any remaining rows
   (a claimer may have rolled back), then `TRUNCATE` the partition.

Falling behind: if a slot cannot be reclaimed (a transaction keeps the
partition locked) the ring stops switching and the current slot keeps
growing, i.e. it degrades to the non-rotating behaviour, never worse, and
health reports degraded with the lag. No admission failures.

Restriction: internal queue transactions are short READ COMMITTED. A reader
holding a snapshot older than a TRUNCATE may not see moved entries; this
affects only long external readers (e.g. ad-hoc reporting), never claims.

Guarantee: ready-table storage is bounded by the rotation interval x write
rate, provided the rotator obtains its locks; otherwise health is degraded
and behaviour equals the non-rotating mode.

### #141 implementation notes

- **Settings** (`DJANGO_SQL_JOBS` / env `SQLERY_<NAME>` standalone): `READY_ROTATION_ENABLED`
  (default off), `READY_ROTATION_INTERVAL` (60 s), `READY_ROTATION_DRAIN_BATCH` (1000),
  `READY_ROTATION_LAG_DEGRADED_SECONDS` (0 = 3 x interval + 60). Django migration 0041 and
  Alembic `20261026_0022` (and the standalone start-up) create three sequences on PostgreSQL:
  `sqlery_ready_gen_seq` (generation), `sqlery_ready_switch_ts_seq` and
  `sqlery_ready_reclaim_ts_seq` (epoch seconds). Reversible; nothing happens until enabled.
- **Lock key** `int.from_bytes(b"SQLEROTA", "big")`. Fenced writers: Django `ready.add` and
  `ready.after_update` (so `QueuedJob.save()`, `QuerySet.update`, bulk create, retries, release,
  unblock, sweep restore, claim put-back), staging promotion (`core/scheduler.py`), standalone
  `create_job` / retries through the `before_flush` hook, `_restore_entry_if_queued`,
  `_recheck_blocked`, the sweep restore, and the async backend's put-back and unblock. Writers
  that only update or delete an entry take no lock.
- **Rotator**: `Scheduler.run_due_tasks` (leader loop) calls `Rotator.tick()` after the ready
  sweep. One tick advances as far as possible (at most one switch, bounded drain batches), each
  protocol step its own transaction. Decisions: `core.rotation.decide_rotation_step`.
- **Health**: `check_horizon` adds `ready_rotation` (`lag_seconds` since the last reclaim, or
  since the last switch before the first reclaim) and reports `degraded` past the threshold.

### #141 deviations from the section above

- The switch time and the reclaim time are kept in two extra sequences (non-transactional, no
  dead tuples) so the interval survives restarts and health can read the lag from any process.
- "Needs reclaim" is derived as "visible rows, or `pg_relation_size(partition) > 0`", so a
  drained slot is truncated exactly once and a crash between drain and reclaim is repaired.
- The drain also takes the shared key (and re-reads the generation) so that two overlapping
  rotators cannot drain into a slot that is being retired.
- A writer that finds the sequence missing (rotation enabled before migrating) uses slot 0
  without a lock instead of failing; it logs a warning and re-checks every 30 s.
- A job can have two entries (one per slot) if a writer re-inserts while a drain moved the
  first; the claim treats the second as a stale entry and drops it.
- The rotator refuses to run inside an outer Django transaction (its locks must end with
  each step).
