### `t/unit/tasks/test_result.py`

Contract of the job result handle (what a caller gets back after enqueueing), job-set results, and in-process (eager) results.

**Looking up a single job's outcome**
- Create a result handle without a job id; expect: rejected as invalid input.
- Look up a job id that was never stored; expect: state pending, result is empty, no traceback, not ready, not successful, and `exists()` is a real boolean False.
- Look up stored done / failed / retrying jobs; expect: `exists()` is True for all of them.
- Job done with value "the"; expect: get returns "the", state done, successful; a done job whose value is None returns None and is still done (not confused with pending).
- Job done and a callback is given to get; expect: callback called once with (job id, value).
- Job failed with an exception; expect: get re-raises that same exception type with the original args, and the remote traceback frames are not spliced in by default.
- Same failed job with remote tracebacks enabled; expect: the worker-side traceback frames appear in the raised error's traceback.
- Failed job read with propagate off; expect: the exception is returned as a value, not raised.
- Job in retrying state; expect: result is the exception, not successful, not ready, has a traceback, and get with a short timeout raises a timeout error (even when the poll interval is longer than the timeout).
- Pending job and get with a short timeout and interval; expect: timeout error.
- Failed and retrying jobs expose a traceback; done and pending jobs do not.
- Ready means done or failed (final); retrying and pending are not ready.
- Job flagged as ignored (result not stored); expect: get returns None immediately without blocking.
- Backend wait returns None; expect: get returns None.
- Get called from inside a running job (would deadlock a worker); expect: refused with a runtime error unless the caller explicitly disables the sync-subtask guard; the guard check itself raises when joins would block and passes otherwise.
- A job whose parent failed; expect: get with propagate raises the parent's error and never waits on the child. Parent succeeded; expect: waits and returns the child's value.
- Forget a result; expect: its stored result and its chain of parents are cleared, the pending-result registration is removed, and it still works with no backend attached.
- Dropping a handle (garbage collection); expect: pending-result registration is removed, and no crash if there is no backend.
- Stored request metadata (task name, args, kwargs, worker hostname, retries, queue, date done); expect: all readable from the handle, date done is a datetime once finished and None while still retrying.
- Completion timestamp; expect: stored in the configured timezone with the correct offset (UTC, Los Angeles, Kwajalein, Berlin).
- Two handles with the same id are equal and hash equal; different ids differ; a handle equals its bare id string; not equal to unrelated objects.
- String form is the job id; repr is "AsyncResult: id".
- Handle survives pickle and copy round trips with the same id, and the legacy task_id alias reads and writes the id.

**Dependencies and result graphs (chains / nested jobs)**
- Children come from cached or stored metadata; a done job with no children returns None.
- Cache can be seeded with children given as tuples, and merged correctly.
- Walking dependencies yields (parent, child) pairs; walking an unfinished job raises an incomplete-stream error unless intermediate results are allowed.
- Graph, leaf (final value) and collect work over a chain; collecting a nested group of results works with both JSON and pickle result serializers.
- Setting an empty cache is harmless.

**Result sets (many jobs as one)**
- Equality depends on membership; a set is not equal to a non-set; empty set supports get and join without error; the set takes its app from the first result.
- Add is idempotent (adding the same job twice keeps one); discard of a missing job is harmless; update adds many; clear keeps the same container.
- Set get uses native join if the backend supports it, otherwise polling join.
- Inside a running job, set get is refused with a runtime error unless sync-subtask guard is disabled; with the guard disabled and jobs pending, a timeout error is raised.
- Nested groups (group of chain of group) join natively without mishandling nodes that lack a cache.
- Adding a job to a set that was already complete makes the set not ready again until the new job finishes.
- Completion callbacks (then) fire exactly once when the set completes, fire immediately if already complete, and a job added later delays later callbacks until it finishes.
- Native iteration on a set built by adding jobs one at a time finalizes its completion barrier.

**Group results**
- Group of 10 done jobs; expect: join returns values in order 0..9, successful, not failed, not waiting, ready, completed count 10, length 10, iteration yields members, indexable.
- Group of 10 done plus 1 failed; expect: join raises the failure, completed count 10, not successful, failed.
- Group of 2 pending jobs; expect: completed count 0, not ready, waiting, join with a short or 1s timeout raises timeout error.
- Join with timeout 0; expect: behaves as non-blocking: returns if all ready, raises timeout if any pending. Tiny timeout with one pending job also times out.
- Join with a callback; expect: callback called per job and nothing returned.
- Join with on_message on a backend that cannot support it; expect: configuration error.
- Native join returns ordered values, honours callbacks, and re-raises a failure when propagate is on; empty group iterates to nothing.
- Nested groups to any depth: with propagate off, failures appear as exception values in place, via both get and native join, with or without callbacks; with propagate on (default or explicit), the nested failure is raised.
- Nested groups inside a running job are refused unless the guard is explicitly disabled, at any depth; without native join, nested results come back as nested lists.
- Maybe-throw on a group delegates to each member with propagate on.
- Failed-join report lists only failed members whose result is cached.
- Forgetting a group forgets every member.
- Save then restore a group retrieves the same members; restore of a deleted group returns None; empty group saves and restores as empty; save or restore against an invalid backend raises; restore with no app falls back to the current app.
- Group equality compares id, members and parent; different parent means unequal.
- Group survives pickle and reduce; children are the same as results; repr works.

**Serialising result references**
- Handle (with or without a parent) converts to a tuple and list and back with identical id and parent type.
- Legacy tuple form still restores.
- Group with parent round-trips; its tuple holds id, parent tuple and a list of member tuples.

**In-process (eager) results**
- Eager job that raises; expect: wait raises it, wait with propagate off returns the error.
- Eager result in retry state; expect: wait returns without error and state/status stay retry; forget is a harmless no-op.
- Revoke on an eager result; expect: returns false (nothing to terminate), state becomes revoked, no traceback, get raises a revoked error, propagate-off returns that revoked error.
- Revoking a failed job replaces its original exception with the revoked error (original not raised); revoking a done job discards its value and raises a revoked error with message "revoked", not a bare exception.
- Eager get inside a running job is refused unless the guard is disabled.
- Name can be passed positionally or by keyword.
- Copy and pickle of an eager result preserve id, value, state, traceback, name, ready/successful/failed, and still raise the original exception (same args) on get; copy shares the same result object.
