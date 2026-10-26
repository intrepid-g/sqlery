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

## #140 History

Terminal transition = one transaction: `INSERT INTO sqlery_job_history
SELECT ...` + `DELETE FROM sqlery_queued_job`. Read paths (get by id,
results, dependencies "succeeded?", dashboard/API lists and stats, retry
chains) consult history too. Retention: history is partitioned by
`finished_at` on partitioned PostgreSQL installs and reclaimed by DROP via
`core/partitioning.py`; otherwise batched deletes as today.

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
