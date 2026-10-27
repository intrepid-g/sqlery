### `t/unit/contrib/test_abortable.py`

**Abortable jobs (cooperative cancellation)**
- Enqueue an abortable job; expect: the handle returned for it supports abort/is-aborted queries.
- Enqueue an abortable job and do not abort it; expect: "is aborted" is false for that job id.
- Ask "is aborted" for a job whose handle is not an abortable handle (plain result type); expect: false, no error.
- Enqueue an abortable job, then request abort on it; expect: "is aborted" is now true for that job id (the worker/job can poll this flag and stop cooperatively).
- Query "is aborted" by explicit job id, or by the currently running job's id (from the request context); expect: both lookups work.
