### `t/smoke/tests/test_worker.py`

**Worker isolation (spawned child processes)**
- Enqueue 1 job, run the worker in "spawn" child mode; expect: job done, result true, it ran inside a spawned child process.
- Enqueue a 30s job with a 3s hard time limit; expect: job fails with a time-limit error. Then enqueue a 1s job; expect: it succeeds (the killed child was replaced, worker still healthy).
- Enqueue a job that uses lazy/deferred loading under spawn mode; expect: job done with correct result (2+3=5).

**Worker restart (pool restart enabled, late acknowledgement)**
- Start a 5s job, restart the worker mid-job (each supported restart method); expect: job still completes successfully.
- Chain job 1 then job 2; restart the worker after job 1 is done; expect: job 2 still runs and completes.

**Shutdown signals (graceful vs forced)**
- Start a 5s job, send graceful stop (warm); expect: worker finishes the running job, then exits; job done.
- Send graceful stop 3 times in a row; expect: repeats are harmless, job still finishes, worker exits.
- Start a 5s job, send immediate stop (cold); expect: job not completed, worker exits, result never arrives (waiting times out).
- Start a 420s job, graceful stop then immediate stop twice; expect: escalation to forced exit, worker exits without waiting for the job.
- Start a 420s job, immediate stop twice; expect: forced exit, worker exits.
- Remap graceful stop signal to immediate stop: 5s job + stop; expect: cold shutdown, job not finished, worker exits. Stop twice on a 420s job; expect: forced exit.
- Connection keepalive (2s heartbeat, ~4s tolerance): enqueue 3 jobs of 10s, graceful stop; expect: connection to the store is not lost during shutdown, all 3 jobs done (state success), worker exits.

**Soft shutdown (grace period before cold shutdown)**
- With a 10s grace period, start a 5s job, send immediate stop; expect: "soft shutdown" announced, job finishes inside the grace window, worker exits, job done.
- With a grace period, start a 420s job, send immediate stop twice; expect: worker skips the wait and proceeds to cold shutdown; a third stop still exits cleanly.
- Job lasting half the grace period; expect: completes during soft shutdown, state success.
- Grace 16s, hard limit 15s, soft limit 10s, 8s job, stop; expect: job finishes and is reported succeeded; no hard-time-limit error and no kill is logged for it.
- Remapped graceful-stop signal with soft shutdown: 5s job; expect: soft shutdown, job done. 420s job with two stops; expect: waits for cold shutdown then exits.

**Unfinished work returns to the queue on shutdown**
- Start a 15s job, soft-shutdown expires before it ends; expect: the unfinished job is put back on the queue ("1 unacknowledged restored"), worker exits; after worker restart, the job completes.
- Enqueue a group of a 3s job and a 15s job; shutdown mid-way; expect: the finished one stays done, only the unfinished one (1) is restored.
- Enqueue a group of two 15s jobs; shutdown; expect: both (2) restored; after restart both complete, group result is [true, true].
- Same single-job and group-one-finishes cases with the remapped stop signal; expect: identical outcomes.

**Idle worker and scheduled-for-later jobs**
- Idle worker (no jobs), soft shutdown on idle enabled, send stop; expect: soft shutdown is announced, then cold shutdown, worker exits.
- Enqueue a job scheduled for later than the grace period (countdown), worker has received it, send stop; expect: the not-yet-due job is put back on the queue (1 restored); after restart the job runs and completes.

**Queue names and aliases**
- Worker listens on queue alias plus default; send job to the real queue name; expect: consumed. Cancel consumption by alias; expect: confirmation reply, worker then listens only on the default queue, and a new job on that queue is never consumed (waiting times out).
- Cancel consumption (by real name, or by alias), then force-drop every connection to the store; expect: worker detects the loss, reconnects, and does NOT resume the cancelled queue (job there still unconsumed), while a job on the default queue (sent with retry policy) completes.

**Routing**
- Auto-creation of missing queues disabled, only one named non-default queue exists, worker listens on it; send job (1+2) explicitly to that queue; expect: accepted and done, result 3 (no reliance on a default queue).
