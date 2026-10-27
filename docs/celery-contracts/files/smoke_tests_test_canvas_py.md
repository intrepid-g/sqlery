### `t/smoke/tests/test_canvas.py`

Smoke tests against a real worker + broker + result backend. Canvas (chain/group/chord) maps to sqlery job dependencies and fan-out/fan-in.

**Basic round trip**
- Enqueue one job on a named queue, run the worker; expect: the job's return value is retrievable within the timeout.

**Fan-out (group)**
- Enqueue nested and differently-built groups (nested groups, list input, generator input) of 2 jobs each (1+1, 2+2); expect: all jobs run, results come back flat and in submission order: [2,4,2,4,2,4].

**Sequential dependencies (chain)**
- Enqueue job A -> job B -> job C, each on the worker's queue; expect: all run in order, final result is C's.
- Chain with a middle step that fans out to 2 jobs where one raises; followed by 2 more jobs; expect: the worker never logs an internal error ("task_id must not be empty"); fetching the final result re-raises the original exception; downstream jobs do not run.
- Chain of start, then 4 stages of (2 parallel jobs, then 1 rejoin job); expect: exactly 13 executions, each exactly once; start first; each stage's 2 branches (any order) finish before its rejoin job; no duplicates, no reordering.
- Chain where a fan-out is followed by another fan-out (implicit join), then a final job; expect: groups upgraded to a join automatically; join job runs once with all results; no skipped or double-run jobs (5 "a" runs, 1 "b" run in the upgrade case).
- Chain: job (2+2), then an EMPTY group, then job (+2); expect: empty group is a no-op, final result is 6.
- Chain: 1 job, group of 2, group of 2 summing jobs, sum job, +1 job; run both with and without a caller-supplied chain id; expect: every job id is unique (9 ids total including group result handles), no job held twice, final result 11.

**Fan-in (chord: parallel header, then one body)**
- Several joins in one group, built three ways (implicit group-then-job, explicit join, generator header); each header has 2 jobs; expect: each body runs once after its header, results are ["body"] x 3.
- Join whose header has one succeeding and one failing job, for each body kind (single job, chain, group, nested join); expect: no internal "task_id must not be empty" error in the worker log; fetching the result re-raises the failing job's original exception; body never produces a normal result.

**Result bookkeeping / cleanup**
- Submit a large nested workflow (jobs, nested groups, chains, joins), then discard/forget its result handle; expect: before forgetting, the backend tracks pending results; after forgetting, none remain (no leaked pending-result entries).
