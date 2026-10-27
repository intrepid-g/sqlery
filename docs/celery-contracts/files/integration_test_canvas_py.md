### `t/integration/test_canvas.py`

Celery "canvas" = workflows: chain (sequential, output feeds next), group (parallel), chord (parallel then one callback), plus callbacks/errbacks. Mapped to sqlery: chain = job dependencies, group = sibling jobs, chord = job depending on N jobs, callback/errback = on-success / on-failure follow-up jobs.

#### Failure handlers (on-failure follow-up job)
- Enqueue a job that raises, with an on-failure handler attached; run worker; expect: job failed, handler ran once and received the exception, caller sees the original exception.
- Same, run synchronously/eagerly (no worker); expect same outcome.
- The failure handler itself fails once and retries; expect: original job still reported failed with its original exception, handler eventually succeeds.
- Handler declared by task name (serialised form) instead of an object; expect: it still runs on failure.
- Handler with pre-stored arguments (positional or keyword); expect: stored arguments are delivered along with the failure info, for both old-style (id only) and new-style (full request) handler signatures.

#### Sequential pipelines (chain)
- Chain 4+4 -> +8 -> +16; expect 32 (each job receives the previous result).
- Chain with a single job; expect its result. Chain of a chain holding a single job; expect the same.
- Chain, job 2 raises, job 3 enqueued; expect: job 1 done, job 2 failed, job 3 never runs, final result raises the job-2 exception, and the parent (job 2) result also raises.
- Chain with a failing job repeated 42 times, each with its own failure handler; expect: only the first runs and fails, handler called exactly once.
- Chain of 42 jobs each with a success callback; expect: all 42 callbacks run, once each, final result right.
- Every job in a chain records root id and parent id; expect: root id is the first job, parent id is the previous job, first job has no parent.
- Chain-level success callback / failure handler on a chain; expect: success callback runs once after the last job; failure handler runs once when any job fails.
- Chain with an ETA (delayed start 10s) and a failure handler; expect: group inside runs after ETA, result 10.
- Priority set on a job in a chain; expect it is honoured/passed through (result "Priority: 5" for the last).
- Chain given arguments at start (`apply_async(args=(1,))`); expect: args reach the first real job, in both async and eager mode.
- Freezing a chain assigns the last job its pre-set id.
- Chain containing empty steps (empty group, empty chain, nested chain starting with empty group); expect: empty steps are no-ops, partial args still reach the first real job, `chain(group(a,b), group(), group())` returns [a,b], no AttributeError/IndexError.
- Empty chain inside a group; expect: ignored, group has only the real jobs, results correct.
- A chain run eagerly from inside a task (always-eager); expect no error.

#### Parallel batches (group)
- Group of two identity jobs; expect [42, 42] in submission order.
- Nested groups are flattened: group(group(a,b)) -> [a,b]; group(a, group(b, group(c,d))) -> [a,b,c,d] in order.
- Group of 1000 jobs; expect all 1000 results in order.
- Group built from a generator, with len() called while iterating; expect every job is still iterated and enqueued exactly once (no truncation after the first).
- Group with a failing job anywhere (first, last, many): result get raises the job exception; group-level failure handler is called (once per failed job: 42 failing of 66 -> 42 calls); passing jobs unaffected.
- Group-level success callback; expect called once, after all jobs done.
- Per-job callbacks on 42 jobs; expect 42 callbacks, once each. Per-job failure handlers on 42 failing jobs; expect 42 calls, once each.
- Empty group: result is saved and restorable from backend with no children; ready() on a group containing a failing job eventually becomes true (no hang).
- Group result ids: every job and nested group has a unique id (no inner group reusing outer id), e.g. 6 distinct ids for 2 groups + 4 jobs.
- Jobs in a group after a chain share the same root and parent ids.
- A job that replaces itself with a group keeps result order across 20 repeats.
- Group serialised/round-tripped through the result store keeps nested structure.

#### Fan-in (chord = N jobs then one body job)
- Header [add(1,1), add(1,1)] body sum; expect 4. Order of header results is preserved (0..9 -> [0..9]).
- Header of one job; expect body gets a one-element list. Empty header; expect body gets [] (or runs immediately) without hanging.
- Header of 1000 jobs, with and without a preceding chain step; expect body sees all (sum 499500).
- Header generated lazily; expect it is consumed incrementally, first job already running before the generator finishes.
- Parallel chords side by side [10, 10]; chords nested several deep; chord inside chains inside chords; chain/chord/chain/chord interleavings; all give the exact expected number.
- Nine chained chords (explicit or implicit via groups followed by sum) -> 29520; exactly-once execution of every job in such implicit chains (each of 1..9 recorded once).
- A group followed by a job is automatically upgraded to a chord; steps after it end up in the body, not dropped; also after serialisation round trip.
- Chain args and kwargs forwarded into a group-upgraded chord body.
- Countdown set on `group | task` must delay the header jobs (>= ~2s), not only the body.
- Slow bookkeeping (1s delay while counting header completions); expect chord still completes correctly.
- A header job that handles its soft time limit itself and returns normally; expect chord still gets all results.
- Header or body job replaced by a chain or another job (first/middle/last position); expect chord result unchanged.
- Jobs added to a running chord (from inside a header job), including an entire chord; expect body sees them.
- Chord task id and header id: group body must not take the header id; a chain body with a fixed task id must not cause "recursive result parents" when frozen.
- Chord ids preserved when a message is delivered twice (duplicate delivery): the header group id seen by both deliveries is identical.
- Redis subscription channels do not leak after many chords (known xfail).
- Chord inside chain also works when run eagerly inside a task.
- Chord with a chain of empty chains as header: body runs straight away on empty input (0); empty chains mixed with real jobs are ignored (sum 6).
- Chain-of-implicit-chords as body, frozen and/or serialised, with a pre-chosen task id; expect result id equals chosen id, final value 285, callback gets same value.
- Chord with link/link_error given at apply time: callback runs after body, once, body keeps its own callback; works on chain ending in a chord (34); on a group body runs once while per-job callbacks stay.

#### Failure inside fan-in
- Header job fails (simple, chain failing early or at tail, group): body never runs; result raises the job exception (or a chord error); same for body failures.
- Failure handler on the chord: header failure -> called once (body is a single job); header group with 42 failures and body single job -> still once; body group with 42 failing jobs -> called 42 times, once each; old-style and new-style handlers both work.
- Flag "allow error callback on header" ON: for headers like [fail], [fail,fail,fail], [fail,ok], [fail,ok,fail,ok,fail] etc.: body never runs; header-level handler called once per failed header job; body-level handler called once.
- Flag OFF (default): body never runs; only one chord-level handler call in total, not per failed job.
- Flag ON for upgraded (implicit) chords: failures + body handler counts = failed jobs + 1, no leftovers.
- Nested chord failing in inner header with flag OFF: outer body not run, exactly one handler call.
- Failing group header with different body types (single, chain, group, chord): original exception surfaces; no internal "task_id must not be empty" error; no internal TypeError for nested chain-with-group-and-failure.
- Chord header member that is a chain whose first job fails: chord must error (ExpectedException or chord error), not retry forever.
- Failure in group body: handlers of the group, of its jobs, and of the chord all fire exactly the expected number of times (group's own handler replaces its jobs' handlers, plus the chord's); same via apply option or method; chord nested in group body passes handlers down once.
- Failure in the body of a chain-of-implicit-chords (header failure or body failure): inner handler runs, result raises.
- Header failure fails the chords that follow it in a chain, so a surrounding group still finishes (state FAILURE).
- On a failed chord, results of successful header jobs are preserved in the stored group result (3 and 7 kept, 1 failure).
- Error from `add -> fail -> group(...)` and `add -> add -> group(ok, fail)` propagate as the job exception.

#### Replacement (a job swaps itself for a new workflow)
- Job replaced by a chain, group, empty chain (error: "Cannot replace with an empty chain"), or a chain containing a group; second-order replacement runs in the order A, B, C, B-out, A-out.
- Replacement keeps outer success callback, failure handler, and chain position; callback called once, after the whole replacement finishes.
- Replacement chain that raises: its inner handler and the outer handler both run; result raises original error.
- Replacement inside group, chain, chord header and chord body (first/middle/last); results and ordering unchanged. (Some xfail on rpc backend: timeout instead of exception.)
- Replacement with `add_to_all`-style group preserves result order.
- Replaced nested chords give [29, 38].
- Stamps (metadata) on the original are merged with new stamps on the replacement.

#### Result sets
- Two enqueued jobs collected in a result set -> [2, 4]; one raising: with propagate off, first failed, second successful.
- Adding results to a set later, even after it was already collected, works and re-collects ([2,4,6]).
- Join with a short timeout exhausted by slow callback: raises timeout (no unlimited wait), already-handled result delivered.
- `join_native(timeout=0)` polls once and gives up on unfinished results quickly; returns results that are already ready.

#### Workflow serialisation
- Nested combinations (chain/group/chord inside chain/group/chord, 9 combos) returned from a job and passing through the result store are rebuilt into usable workflows (no exception).

#### Stamping (job metadata)
- Visitor stamps every job, including callbacks and errbacks, in every shape (single, group, chain, chord, nested); every job received sees stamps listed in stamped_headers.
- Stamp value type (scalar or list) is preserved. Stamps do not pollute the message properties.
- Stamps visible to worker on receive; stamps survive a retry; linking a callback/errback after stamping stamps it too.
- Stamping a canvas whose link/link_error is a plain dict doesn't crash; failing job with dict errback still fails with ExpectedException.
- Example workflow (1+2+3 summed, then x10 thrice summed) = 180.

#### Environment-level guarantees
- Skip cleanly when result backend can't do chords; chain/group behaviour must not require Redis.
- Worker liveness check (ping) before tests; every wait has a bounded timeout, never hangs.
