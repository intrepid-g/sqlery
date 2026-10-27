### `t/unit/utils/test_threads.py`

**Temporary timeout scoping (default socket timeout)**
- Run a block under a temporary timeout of 5s; expect: the timeout applies inside the block and the previous setting (none) is restored afterwards.
- Run a block under a temporary timeout and have the block raise; expect: the error propagates AND the original timeout is still restored (nothing leaks to later connections in the process).

**Bounding connections to an unresponsive database/broker during shutdown (best effort)**
- Worker shuts down while the backing store is unresponsive; every already-open client connection of every channel (main and subscription clients) gets a bounded read timeout; expect: all report the new timeout.
- Single-socket transport: its one open socket gets the bounded timeout, so a blocking read during shutdown cannot hang forever.
- Teardown must never reconnect: reading properties that would redial a dead store must never happen; expect: no reconnect attempt, no hang (covers both the connection-level and transport-level reconnecting properties).
- Teardown must never open a new client on a channel; expect: no new connection is dialled.
- Connection with no sockets at all; expect: silently nothing to do.
- Connection object that errors when inspected (connection is gone); expect: error swallowed so shutdown carries on to close.
- Socket already closed; expect: no error.

**Background worker thread crash**
- Background thread whose body raises; expect: the error is logged and the process exits hard with status 1 (a dead helper never leaves a zombie worker half-alive).
- Background thread with no body implemented; expect: a "not implemented" error.

**Per-thread/per-context local state**
- Set a value on a local; expect: it is listed under the current thread id; delete it; expect: no longer listed; delete again; expect: attribute error.
- Calling a local with a factory returns a proxy to it.
- Local stack: pop on empty returns nothing; release works on empty; reading the top of an empty stack raises a runtime error; push then read gives the value; after pop, reading raises again.
- Fast local stack: push 2 items; expect top is the last and length 2; pop; expect top is the first; pop again; expect top is none.
- Local manager: defaults to no locals; accepts a list or a single local; custom identity function is used by the manager and propagated to its locals; cleanup releases each managed local; has a printable repr.
