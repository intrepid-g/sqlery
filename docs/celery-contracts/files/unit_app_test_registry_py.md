### `t/unit/app/test_registry.py`

**Job registry: lookup by name**
- Register a job function under the name "A" and another (scheduled/periodic type) under "B"; expect both retrievable by name from the registry, and the registry behaves like a plain mapping (can be copied into a dict, `in` works, indexing returns the same job object).
- A job registered in the registry can be looked up by name when a worker restores/deserialises a reference to it (name only, or name plus module hint); expect the same registered job returned either way.

**Register / unregister**
- Unregister a registered job (by object or by name); expect it no longer in the registry.
- Unregister the same job twice; expect the second attempt to raise a "not registered" error.
- Re-register a previously unregistered job; expect it present again by name.
- Register, unregister by name, re-register a scheduled job; expect it absent in between and present afterwards.

**Bad input**
- Register a job that has no name; expect an "invalid task" error and nothing added.
- The "not registered" error carries a readable message naming the missing job (e.g. "tasks.add").

**Registered jobs stay runnable**
- After all the register/unregister churn, running each job directly still returns its result.

**Regular vs scheduled listing**
- The registry can list regular jobs and scheduled (periodic) jobs separately; expect both listings available and non-empty when both kinds are registered.
