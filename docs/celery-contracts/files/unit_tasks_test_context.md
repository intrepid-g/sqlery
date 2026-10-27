### `t/unit/tasks/test_context.py`

The per-job execution context (id, args, headers, time limits, stamps) that travels with a job.

**Defaults and isolation**
- Create a job context with no input; expect every field at its documented default, with an empty, per-instance children list.
- Update one job's context with id, args and an unknown extra field; expect that context to hold all of them, and a freshly created context to still show pure defaults (no leakage between jobs).
- Set fields directly on one job's context (id, args); expect the same result and no leakage into new contexts.
- Update a context, then clear it; expect it back to defaults (extra fields removed, children empty), and new contexts also default.
- Read every field through the safe `get` accessor instead of attribute access; expect identical values, including the extra field.

**Headers**
- Build a context from a job message with unrecognised top-level keys (e.g. a custom header); expect those keys collected into headers.
- Build a context from a message that already carries explicit headers plus other stray keys; expect the explicit headers kept as-is and not overridden.

**Stamps (metadata) carried to follow-up jobs**
- Context declares stamped header `team` and holds stamps `team` and `extra`; expect execution options to include `team` and exclude `extra`.
- Context with retries=2 and an undeclared stamp named `retries` = 99; expect retries stays 2 (stamps cannot hijack system fields).
- Context with an empty declared-stamps list but stamps present; expect no stamps passed on, and the declared list stays empty.
- Build an outgoing job message from a context with two declared stamps (one multi-valued), an undeclared stamp, a bogus `retries` stamp; expect message headers contain only the declared stamps with values intact, and the true retry count (2).

**Time limits**
- Update with `timelimit` = [hard 30, soft 20] as a dict; expect time_limit 30, soft_time_limit 20.
- Same update given as a list of (key, value) pairs; expect the same unpacking (regression: pair form was previously missed).
- Same update given as the items view of a dict; expect the same unpacking.
- Same update given as a keyword argument; expect the same unpacking.
- Set limits, then update with `timelimit` = None; expect both limits cleared.
- Set limits, then update with an unrelated field only; expect both limits preserved.
