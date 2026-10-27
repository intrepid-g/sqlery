### `t/unit/utils/test_objects.py`

Mostly Celery-specific helpers (a keyword-bag object and a "use this resource, else a fallback" context manager). Portable guarantees are about resource lifetime and error propagation.

**Config/option bag**
- Build an options object from keyword arguments (foo='foo', bar=2); expect each value readable as an attribute.

**Borrowed vs. owned resources (e.g. DB connection/session for a worker)**
- Caller supplies a connection; run work inside the scope; expect the supplied connection is used as-is and the fallback factory is never called, and the caller's connection is never opened, closed or otherwise touched.
- Caller supplies nothing; run work in scope; expect a fallback connection is created on entry, handed to the work, and released exactly once on exit (works for a None or a real value).
- Fallback is released using the manager that created it, not the object it handed out; expect the handed-out resource is not closed itself.

**Failure handling**
- Setting up the fallback fails (e.g. cannot connect); expect the original error is raised, the work body never runs, no release is attempted, and the half-built manager is not kept alive (no leak).
- Work body raises while using a fallback resource; expect the fallback sees the exact exception (cleanup/rollback hook), and when it does not suppress it the same exception object reaches the caller unchanged.
- Work body raises and the fallback suppresses it; expect the caller sees no error.
