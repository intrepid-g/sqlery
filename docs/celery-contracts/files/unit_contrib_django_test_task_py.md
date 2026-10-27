### `t/unit/contrib/django/test_task.py`

Small file: two tests of the Django-only "enqueue after the transaction commits" helpers. The portable guarantee is commit-deferred enqueueing.

**Enqueue on commit (delay shortcut)**
- Inside a database transaction, call the "enqueue after commit" shortcut for a job; the enqueue is registered with the transaction's on-commit hook (not run immediately). When the commit fires, the job is enqueued. The call itself returns nothing (no job handle), because the job does not exist yet at call time.

**Enqueue on commit (full-options variant)**
- Same contract for the variant that takes full enqueue options (queue, schedule time, retries, and so on): it is deferred to commit and returns nothing to the caller.

**Implied guarantees worth keeping**
- A job enqueued from inside a transaction that rolls back is never enqueued (workers never see work whose data was not committed).
