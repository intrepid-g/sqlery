### `t/unit/contrib/test_migrate.py`

Portable guarantee: moving pending jobs between queues or between databases is selective, lossless for what is moved, bounded, observable, and tolerant of missing queues.

**Selective move (predicate)**
- Pending jobs, predicate matches none; expect: nothing acked, nothing re-enqueued, jobs stay where they are.
- Predicate matches a job; expect: job re-enqueued at its destination and the original acknowledged (removed from the source).
- Predicate returns a different target queue for a job (transform); expect: destination queue declared if missing, job published there.
- No explicit destination given; expect: job keeps its original queue and routing key.
- Explicit destination given; expect: it overrides the original queue and routing key.
- Move with limit=1; expect: first match moved, then the scan stops.
- Move with a per-job callback; expect: callback invoked for each moved job.

**Filtering by job name / id**
- Filter on job names ["add","mul"]; expect: matching jobs passed on, other jobs (e.g. "div") ignored.
- Move by name-to-queue map; expect: jobs of a mapped name are moved to that queue, others untouched.
- Move by id-to-queue map; expect: only the job with that id moves, to its mapped queue.
- Move a single job by id; expect: that job goes to the given queue, others are not matched.
- Id match against a list or a single id; expect: true for a listed/equal id, false otherwise.

**Scanning queues**
- Scan several queues given as a comma-separated string or a list; expect: all are consumed.
- Scan restricted to job names; expect: callback fires only for those names.
- Scan with an on-declare-queue hook; expect: hook called as queues are declared.
- Scan with a limit; expect: stops after N matches and the progress count reflects matches.
- Queue name that is a known queue name vs. an already-built queue object; expect: both resolve to the queue.
- Progress reporting; expect: total shows "?" until known, then the count; status line printed per job and state is printable at any time.

**Migrating everything between two databases**
- Source holds 3 jobs, destination empty; expect: all 3 arrive at destination in the same order (FIFO) and bodies intact.
- Migrate with a callback; expect: callback called per job moved.
- Migrate with a custom migrate function; expect: it is used instead of the default.
- Migrated job that was compressed; expect: still carries its compression setting, content type, encoding, exchange and routing key, and no duplicate/stale compression header.
- A queue disappears or raises a channel error while probing it; expect: error is tolerated, migration of the others continues, no crash.
- Source is empty; expect: nothing moved, callback never called.
