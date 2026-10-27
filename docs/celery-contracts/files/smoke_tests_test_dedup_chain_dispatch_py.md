### `t/smoke/tests/test_dedup_chain_dispatch.py`

Setup for all scenarios: successful-job deduplication is on and acknowledgement happens after the job finishes (late ack), so a redelivered job whose result is already stored is skipped instead of re-run.

**Redelivery after rejection**
- Chain job A then job B (B takes A's result). A rejects the message and requeues it on its first run, then succeeds on the second; expect: the chain completes, B's result is A's second-run value.

**Deduplicated redelivery still continues the workflow**
- Chain job A then job B. A stores success, then rejects/requeues the message, so it is redelivered; expect: the redelivered copy is skipped as a duplicate (not re-run), yet B is still enqueued and runs exactly with A's stored first-run result.
- Job A with a success callback (dependent job) attached. A stores success then rejects/requeues; on redelivery the duplicate is skipped; expect: the callback still runs, receiving A's first-run result.

**Guarantee for sqlery**
- Skipping an already-completed duplicate must not drop its downstream work (dependencies/callbacks): a job already marked done and then redelivered must still trigger its dependents, once, with the stored result.
