### `t/unit/bin/test_worker.py`

**Worker startup (CLI)**
- Start the worker with the single-process (solo) pool against an app whose broker is unreachable or has no work source; expect: it exits with code 1 (failure), not a hang or a silent success.
- Start the worker with "skip startup checks" enabled; expect: the skip-checks flag is exported to the environment for child processes, and startup still exits 1 under the same failing condition.
- Start the worker with the "disable prefetch" flag; expect: the worker starts cleanly (exit 0) and the option reaches the worker as true.

**Prefetch limiting (claim only what you can run)**
- Disable-prefetch on, enqueue more jobs than there are free worker slots (4 reserved, 4 slots); expect: worker refuses to claim another job.
- Disable-prefetch on, 2 of 4 slots busy, queue has work; expect: worker claims one more.
- Disable-prefetch on, 2 of 4 slots busy, but the underlying queue says nothing is claimable; expect: worker does not claim.
- Disable-prefetch off or unspecified (none); expect: claiming behaviour is unchanged from default.
- Disable-prefetch on with a backend that does not support it (non-Redis broker); expect: setting is silently ignored, default claiming behaviour kept.

**Concurrency option parsing**
- Concurrency given as "4"; expect: 4 workers.
- Concurrency given as "auto", in any case or with surrounding whitespace ("AUTO", " auto ", tab/newline padded); expect: normalised to auto.
- Concurrency empty, missing, or "0"; expect: falls back to the configured default (a number or auto).
- Concurrency "garbage"; expect: rejected as a bad parameter, nothing starts.
- Negative concurrency ("-1", "-8"); expect: rejected as a bad parameter.
- Rejection message for invalid concurrency; expect: it mentions "auto" so the user knows the valid alternative.

**Portable guarantee:** configuration is validated up front with clear errors, a worker never over-claims beyond its free capacity, and failed startup is reported with a non-zero exit.
