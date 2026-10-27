### `t/integration/test_security.py`

Celery's signed-message mode (message signing with a key, certificate and certificate store). One real test, currently marked as a known failure (Celery issue #5269). The portable guarantee for sqlery is that job payload integrity/authenticity settings must never silently lose jobs.

**Signed job payloads**
- Configure the queue to sign job payloads with a private key and certificate, and to trust certificates from a store; enqueue 1 job (add 1+1), run the worker; expect: job done, result 2 returned within 10 seconds.
- Same setup, but the worker rejects or cannot verify the signed payload (security misconfiguration); expect: the job is NOT silently dropped, with the caller waiting forever. It must end as a visible failure (failed status with a clear error), or be processed. (Celery's known bug: the task is dropped and the caller just times out.)
- Waiting for a result with a timeout when the job never completes; expect: a timeout error surfaced to the caller, never a hang.

**Setup and cleanup**
- Test key/certificate files are created in a temp directory and removed afterwards; expect: no leftover files after the run.
- Result format stays plain (readable) even when job payloads are signed; expect: results are readable without signature handling.
