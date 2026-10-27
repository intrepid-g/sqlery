### `t/unit/app/test_routes.py`

Routing: deciding which queue a job goes to at enqueue time.

**Static route table (job name -> destination)**
- Route maps job name to `{queue: foo}`; enqueue that job; expect it lands in queue foo. A job name with no entry; expect no route (falls through), not an error.
- Route maps job name to a full destination (exchange, type, routing key); expect all those settings carried through unchanged.
- Glob patterns: `proj.tasks.bar*` beats the broader `proj.tasks.*` when listed first; expect first matching rule wins, and `proj.tasks.bar.baz` also matches the glob.
- A bare string value (`'routeA'`) is shorthand for a queue name.
- Regex patterns are accepted as rule keys (`video.*`, `image.*` both go to queue media).
- Job name matching no pattern at all; expect no route.
- Route points to a queue that does not exist and auto-creation of queues is off; expect a clear "queue not found" error, not a silent misroute.

**Router with multiple route rules**
- Two rule sets both match the same job (bar, then foo); expect the first one wins.
- First rule set does not match, second does; expect the second is used (rules are traversed in order).
- Job matches no rule; expect it goes to the default queue.
- Router built with no queues; expect an empty queue set, no crash.

**Explicit options at enqueue time**
- Caller passes `queue='testq'` plus unset extras (exchange=None, routing_key=None, immediate=False); expect those None values do not override the queue; with auto-create on, the queue testq is created with its own name as exchange and routing key, and other options (immediate=False) are preserved.
- Destination given as a plain queue name; expect it resolves to the configured queue.
- Destination given as an already-built queue object; expect that exact object is used, not copied.

**Custom routers**
- A router function receives (job name, args, kwargs, options, job object); enqueue with args (2,2), kwargs, priority 3; expect the router sees the real args/kwargs and the options include priority 3.
- Legacy router object with a `route_for_task(name, args, kwargs)` method; expect it works and is called with the job name, args and kwargs.
- Legacy router returns a queue name for one job and nothing for another; expect named job goes to that queue, the other to default.
- A router returning nothing means "no opinion"; expect next rule/default is used.

**Configuration normalisation**
- Route config may mix dicts, dotted-path strings naming a router class, and ready router objects; expect dicts become map routes, strings are lazily resolved to router instances, objects are kept as-is.
- A single object or single dict (not a list) is accepted and wrapped into a one-item list.
