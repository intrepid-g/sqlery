### `t/unit/tasks/test_canvas.py`

Celery "canvas" = composing jobs: signature (a job spec), chain (sequential dependencies), group (parallel fan-out), chord (fan-out then one callback job once all finish). Mapped to sqlery: job specs, dependencies, batches, "run when all of these are done" jobs.

**Job spec (signature) basics**
- Build a job spec with args, kwargs, options; read each back unchanged; round-trip through JSON/serialization gives an equal spec.
- Call a spec directly with extra args: extra args are prepended to the spec's own args, kwargs merged.
- Immutable spec (ignores parent result): extra args/kwargs passed at run time are discarded; only options (e.g. task_id) merge.
- Partial spec gets the previous job's result prepended as first argument; immutable one does not.
- Set options (queue, priority, countdown, task_id, immutable) on a spec; toggle immutable on and off.
- Cloning a spec never shares its kwargs dict with the original or siblings (mutating a clone must not corrupt the source), for plain clone, clone with option overrides, clone with new args, and immutable specs. Copy is shallow: live objects inside kwargs (nested specs, lazy iterators) keep identity and are not consumed.
- Merging with no options or None options returns the existing options untouched.
- A group_id already set on a spec is never overwritten by a later merge (silently ignored); if unset it can be set.
- Linking the same success callback twice (or same error callback twice) stores it once.
- Single callback becomes a list when a second is added; on_error adds an error callback.
- Flatten callback links of a spec into an ordered list.
- Freezing a spec assigns and stores id, group id, chord, root id, parent id, group index in its options.
- A spec for a task name that is not registered can still be built and has result/apply handles.
- Invert operator (run and wait for result) returns the result value.
- Chaining a spec with a non-job object (None, int, arbitrary object) raises TypeError.
- Election (control broadcast) request returns a result carrying the frozen id.

**Map / starmap / chunks (bulk helpers)**
- Map and starmap specs apply with original iterable and route name; options like foo=1 pass through; serialize/deserialize equal; have a repr.
- Chunks of 100 items in size 10 produce 10 jobs; serialize round-trip equal; subclass type survives from_dict and clone.
- Chunks applied or called go through the group path with route name; works under always-eager mode.

**Chain (sequential dependencies)**
- Chain of one job, chain of a chain of one job: same single task.
- Chaining flattens nested chains (4 tasks stays 4, not nested), consolidates chain|chain into one chain.
- Group followed by task collapses into a chord; chord followed by task extends the chord body; empty group skipped; group of one is unrolled, not turned into a chord.
- Chain led by a group nested inside a chain still composes later steps onto the chord body (nothing dropped), also when the chain arrives deserialized from JSON.
- Chain() constructor and the `|` operator must produce identical results for many shapes (drift guard), including chains with callbacks/errbacks attached.
- Last task in a chain is kept as passed by caller (not cloned), still linkable afterwards.
- Unsupported operand in a chain (also inside a nested chain) raises TypeError, not silent acceptance.
- Errback objects are not shared between steps of a chain (stamping one step must not leak into another).
- Building an n-task chain clones each task about once (no quadratic cloning; n=40 stays under 2n clones).
- Cloning a chain (and subclass) preserves tasks, kwargs, args, type.
- Apply a nested chain (depth 1 and 3) with positional, keyword, or mixed arguments: args reach the first real step; result correct (e.g. 50, 70); the workflow's serialized form is unchanged afterwards.
- Nested chain whose first task is immutable ignores the injected args; a task with a parameter named "tasks" still gets the kwarg.
- apply_async yields linked results: last result's parent is previous step, and so on; first has no parent.
- Splicing: chain inside chain frozen yields flat task list (4 tasks); dict-form tasks are converted to signatures bound to the app.
- Chain with no tasks: serializes/deserializes; calling or apply_async returns None/falsy, never errors; appending to an empty chain with `|=` works and runs (result 3).
- Chain accepts a generator of tasks.
- App resolution: chain falls back to default app when empty; when first step is a chord it finds the app through its tasks; with dict-form first step it falls back to the current app; applying works either way and returns an AsyncResult.
- Thread safety: applying a chain starting with a chord from a different thread than the one that set the current app must still find the right app and result backend (not the default app without a backend).
- Consecutive groups upgrade to a single chord; chord then group extends body; chain of chords stays flat (not nested bodies) and serialized size stays constant per chord.
- Message size stays roughly linear (not geometric) as number of groups in a chain/chord body grows, whether serialized, frozen, or used as chord body.
- Dispatching (apply_async) a frozen nested chord, direct or inside chain/header, serialized or not, does not mutate the workflow.
- Splitting a chord body chain keeps chain options (queue, priority) on its first task only, and keeps error callbacks on both parts.
- Preparing a chain twice leaves it as written and yields identical shape both times.
- Chord as first or last step of a chain: correct ordering of prepared tasks; a fixed final task id lands on the real last task, also after an implicit chord; frozen chain keeps its result id.
- If a chord header job fails inside a chain of chords, the chain's final result is marked FAILURE.
- Options on apply: task_id becomes final id; group_id and chord id go to last task; link goes to last task; link_error goes to every task; priority=None falls back to the spec's own priority.
- Always-eager mode: chain runs inline, result 16; chain started from inside another eager task works.
- Chain-level success callback fires exactly once, on the last task (also when set as option, and when chain ends in a chord, routed to chord body); callback must not leak into the caller's canvas (rerun without it must not refire; rerun with different one fires only the new one).
- apply() returns an eager result with parent chain intact (26, 16, 8, none); chord in chain with arguments gives 130.
- Job raising Ignore inside a chain: later steps do not run, state IGNORED, result None.
- Job raising Reject inside a chain: later steps do not run, state REJECTED, result None.
- Keyword args supplied at apply reach first step (result 20); single-expression chain has no parent.
- Chord result parents: group result sits between the right tasks; no id appears twice in the parent chain.
- Chain's single child: calling it applies the child once with an empty remaining-chain list and returns that result (also for a chain of a one-task group).
- Flattening an inner chain with callbacks keeps them: inner errback goes to every flattened task, inner success callback goes to the last task only.
- Group in the middle of chains freezes without error, with or without explicit task_id.
- Result tree (as_tuple) of consecutive groups retains every group's fan-out; every task appears exactly once, with or without task_id, via apply_async or freeze, both protocol versions (regression #8903, #9608).
- Group then task then chord then task: each task and group result appears once in result tree.
- Chord body that is a chain gets a real non-None id after freeze; header tasks carry the body's id (so a header failure can store the error against the body); chain error callbacks propagate to chord body so they fire when the header fails.
- Frozen chain id equals its returned result id; explicit id on freeze is kept.
- Empty-group handling in chains (many cases, regression #9772/#10321/#10646): empty group in front, middle, end, multiple, nested after splice, or given as serialized dict is skipped; start args go to the first real task; a chain of only empty groups or an empty group followed by empty chains keeps one empty group so a result still exists (freeze returns a result, apply_async returns result with get() == [], no IndexError/None); an empty chain at the head does not swallow args; freeze with nested leading empty group forwards args in place; eager apply agrees with worker path (lone empty group result []).
- A generator-backed group of unknown length is not assumed empty (becomes a chord); known-empty one is skipped.

**Group (parallel fan-out)**
- Repr readable; empty group repr says empty; module names are not mangled in repr.
- Serialize/deserialize round trip including subclasses and deeply nested groups (42x42); all children become signatures.
- Cannot attach a success or error callback via apply_async on a group: TypeError.
- Group built from a group reuses its tasks; single task or single-element list is a valid group; maybe_group wraps a single spec.
- apply runs all (8, 16); apply_async; dict-form and nested-group members work.
- Callback on a group goes only to its first child, made immutable (cannot pass siblings' results); error callback goes to every child (also dict-form, mutable errback stays mutable).
- Empty group: apply and apply_async and call return an empty result, no error.
- Calling a group inside a running task registers its result as a child of that task, once.
- Group ending in a chain which ends in a group gets its own id different from the outer group.
- Skew spreads countdowns 1,2,3,... across members; iteration yields keys.
- kwargs given at apply/apply_async/delay reach every member, eagerly too; partial members merge.
- Eager group whose member chain ends in a group: results nested ([2,[6,8]]) and the result backend is not written.
- Eager member runs under its own preset id (task_id honoured); member callbacks fire (result 2), failing member (1/0) stores ZeroDivisionError and its error callback fires; chord member inside eager group works (2, 10).
- Group from a generator: applies exactly N times, N children; empty generator applies zero times.
- Group containing chords: chord size is set on the backend just before the last header job is applied, equal to the number of tail tasks of the header (42 for group, 1 for chain, tail group size for chain ending in group, ignoring a trailing empty group, 1 for nested chord, 1 for empty chord); header of only empty chains must not hang: header skipped and body applied immediately. (Header that is an empty group is known-bad, marked xfail.)
- Prepared group members: dict and partial specs get parent result prepended, an AsyncResult and a group id each.
- Task replaced by a group inside eager mode keeps member order ([3, 2]).
- Empty chains inside a group are dropped everywhere (prepare, freeze, generator freeze) so frozen results match tasks actually applied.

**Chord (fan-out then callback)**
- Header lookahead of a generator does not exhaust it when resolving app.
- Serialize round trip incl. subclass; clone clones the body (not shared); clone with no body stays bodyless; clone keeps and merges kwargs.
- Group body does not take header's id (ids differ, header is parent).
- countdown/eta on group|task chord delays the header jobs (not just the body) while the unlock-retry keeps its own default countdown (#7851); same when set via .set(countdown).
- App resolution for chords: from app, header, body, empty header, headerless signatures, nested canvases, dict-form tasks, falling back to current app; works from another thread (#6197).
- Header id: existing task_id kept; none or missing gets a freshly generated one.
- Empty group body freezes into a result with a parent.
- Chord size (length hint): simple 1, group of N is N, chain flattened into header counts N, nested chain counts 1, nested group in chain counts only if last element, deserialized dict members are rebuilt and counted.
- link/link_error on a chord go to the body, not the chord or header; with the allow-header-errbacks flag on, error callback also goes to header jobs (immutable) while link option still goes only to body; header never receives link/link_error kwargs (previously raised TypeError).
- Callbacks passed at apply_async are added after the body's own callbacks (own first), do not mutate the user's signature, link=None keeps existing ones, link given at apply replaces link set on chord, link set on chord goes to body; works the same eager or not.
- Group body: success link goes once to first task, error link goes to each task keeping its own errbacks, group itself gets none; own (even empty) group errback means nested chord body is not linked again (no double call); nested group flattened drops its own errback so its tasks get it; chord inside group body gets errback on its body only, header none, no deprecation warning; empty group body still gets link; callback group not made immutable; option not remembered for next call.
- Chain callback applied to a chord step keeps body's own callbacks and adds chain's, none on header jobs, after serialization.
- Repr works, with and without body.
- Freezing a group of chords numbers body elements by group_index from 0 and does not mutate caller's original chord or body.
- Re-freezing after replacing tasks is fine; chord started from inside an eager task works.
- Deserialization: header group/chain unpacked into header tasks, body group stays a group (subclass kept), body chain becomes internal chain, nested groups all rebuilt.
- Error callback flag: off links body only; on links header (immutable clone) and body; works for header as single, list, or group, dict-form callback returns same errback; link_error on header lists each errback.
- chord|group of one stays a chord with chained body; chord with group body | task upgrades to nested chord.
- chord.run always freezes the body with a valid task id (supplied or generated, different from group id) so error handling never sees "task_id must not be empty" (#9773); parent_id of caller is not passed to body; root_id is; result id equals external task id.
- Link-based (protocol 1/link) body keeps final result id on inner chord.
- Chord header messages stay linear in size as body groups grow (serialized or not, nested chain or not); body unchanged after dispatch.
- Nested chord body keeps queue/priority on first task only, errbacks on all, callback on last.
- Stripping a chord's header links or clearing outer callback preserves nested chord header ids/args, body queue and errbacks; chord without body untouched.

**Helpers**
- maybe_signature: None stays None, dict becomes signature, existing signature returned as is.
- Merging option dictionaries: dicts merged recursively, lists concatenated, tuples/sets kept; None values handled predictably (None+None -> [None], scalar+list appended, etc.); aggregate_duplicates honoured at every nesting depth (duplicate scalars become list when True, first wins when False).
- maybe_unroll_group tolerates tasks with no len and no length hint (returns group unchanged).

**Portable guarantees for sqlery (summary)**
- Dependent jobs run in order; result of one feeds the next unless declared immutable.
- A failed, ignored, or rejected job stops dependents from running and marks the chain accordingly.
- "Run X when all of N jobs are done" waits for exactly N, handles N=0 immediately, and fires its failure handler when any job fails.
- Callbacks fire exactly once, never leak into reused definitions, never double-fire.
- Empty or degenerate compositions never crash and still return a result.
- Compositions are never mutated by being submitted; clones never alias.
- Message/row size grows linearly with composition size.
- Same behaviour whether executed inline (eager), serialized/deserialized, or from another thread.

---

_Generated by [Claude Code](https://claude.ai/code)_
