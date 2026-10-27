### `t/smoke/tests/test_redis_backend_reentrant.py`

Portable guarantee: cleanup of abandoned result handles (garbage collection) must never corrupt or deadlock an in-progress result wait. The Redis pub/sub specifics are backend wiring; the guarantees below are what carries over to sqlery.

**Re-entrancy during result waits**
- Submit 20 jobs one after another from a client; before each, create and abandon 5 pending result handles (each waiting on a result, referenced only weakly, held in unreachable cycles) while forcing garbage collection in the middle of every wait/read call; expect: all 20 jobs return correct results (i + i, in order), no deadlock, no error.
- Same scenario with the 20 jobs submitted concurrently as cooperative greenlets (gevent) rather than sequentially; expect: identical outcome, results correct and matched to the right job.
- During either scenario, track every call on the result-listening connection; expect: zero overlapping calls, so cleanup triggered by a finalizer never writes into the middle of another in-flight operation.

**Resource release**
- After all jobs finish and garbage is collected, drain pending events once; expect: no leaked subscriptions, and within 10 seconds no result channel remains registered on the server.

**Hang protection**
- If the client does not finish within twice the result timeout; expect: the test fails explicitly as a suspected deadlock rather than hanging.
- Client exits non-zero or prints anything other than "ok"; expect: failure with the client's stderr.
