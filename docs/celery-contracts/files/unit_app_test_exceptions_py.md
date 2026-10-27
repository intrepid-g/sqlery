### `t/unit/app/test_exceptions.py`

**Retry signal (a job asks to be retried later)**
- A job raises a retry signal carrying a message, the underlying error and a datetime for when to retry; expect a human-readable description of the retry to be producible (no crash when the retry time is an absolute datetime).
- A retry signal is serialised and deserialised (as when crossing a process or database boundary); expect message, underlying error and retry time to survive unchanged, so a worker in another process sees the same retry request.

**Reject signal (a job is refused without being run to completion)**
- A job is rejected with a reason and requeue=true; expect the reason and the requeue flag to be readable, so the worker can decide to put the job back in the queue.
- A rejection can be printed or logged; expect a non-empty representation.
- A retry-style signal built with a bare flag instead of an error still round-trips through serialisation; expect no failure (control signals must always be transportable between processes).
