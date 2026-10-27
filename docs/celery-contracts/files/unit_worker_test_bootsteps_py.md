### `t/unit/worker/test_bootsteps.py`

Celery "bootsteps" are the worker's startup/shutdown components. Portable idea for sqlery: the worker is built from ordered components that start, stop and terminate cleanly in dependency order.

**Component lifecycle**
- Define a component, create it; expect it constructs and can be asked to create its underlying object.
- Component enabled; expect it is included and its object is created and registered with the worker.
- Component disabled; expect it is not included, nothing is created, nothing registered.
- Start then stop a component; expect start and stop are forwarded to the underlying object.
- Start a component whose object was never created (None); expect a quiet no-op, no error.
- Terminate a component with no object; expect no error.
- Terminate a component whose object has no terminate capability; expect it falls back to a normal stop.
- A component has a readable text representation (for logs/debugging).

**Consumer-style components**
- Base consumer component asked for its consumers without an implementation; expect NotImplementedError.
- Start, stop, shutdown with one consumer; expect consume called on start, cancel on stop, channel closed on shutdown.
- Start with zero consumers; expect no error.
- Close when a consumer has no channel; expect no error.

**Ordering and dependencies**
- Components A requires C, C requires B, D marked last; expect start order B, C, A, D and lookup by name works.
- Component requiring an unknown/unimportable dependency; expect ImportError at assembly; after removing the requirement, assembly succeeds.
- Dependency sort fails internally (KeyError); expect the error propagates, not swallowed.
- Only components belonging to this worker's namespace are claimed; foreign-namespace components are excluded.
- Blueprint with no components, asked for its "last" component; expect None.
- Two component sets connected; expect combined graph contains components from both.

**Shutdown and unexpected errors**
- Broadcast an action (close/stop) to components where some are None; expect skipped silently.
- A component raises during a broadcast with propagate off; expect error swallowed and remaining components still run.
- Same with propagate on; expect the error is raised to the caller.
- Stop requested when state is already terminating; expect no error, no repeated work.
- Close with no on-close hook; expect close still broadcast to all components, in forward order.
- Waiting for shutdown completion raises an error on the configured ignorable list; expect it is ignored, wait was called with the given timeout.

**Diagram/formatting (non-contractual)**
- Graph formatting of nodes/edges (last and conditional components styled differently): cosmetic, no portable guarantee.
