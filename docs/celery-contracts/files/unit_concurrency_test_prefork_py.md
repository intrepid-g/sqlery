### `t/unit/concurrency/test_prefork.py`

Portable guarantees of the prefork worker pool (child worker processes, pipes to them, shutdown). Mapped to sqlery: worker = parent that forks children; job hand-off = claim and dispatch.

**Child worker startup**
1. Start a child worker; expect: it ignores/resets the right signals, initialises the app, fires a "worker process init" event carrying the worker hostname, and sets its process title.
2. Start a child created by spawn (fresh interpreter); expect: it is flagged as freshly spawned and re-runs startup optimisations. A forked child is not flagged and does not re-run them.
3. Start a child; expect: it is told to die (SIGKILL) if its parent dies, so no orphans.
4. Register an init listener for host db@ only, start children for web@ then db@; expect: listener not called for web@, called once for db@.
5. Configure a per-child log file name template; expect: it is honoured per child.

**Child worker exit**
6. Child exits with code -3 and no hostname known; expect: a "worker process shutdown" event with pid, exit code, no sender.
7. Child exits with code 0 and hostname known; expect: the same event with the hostname as sender.

**Dispatch and event loop robustness**
8. Distinguish a transfer that has not begun from one in progress or failed; expect: "not started" is true only before the first step, including after an error.
9. Poll descriptors; expect: ready ones returned. Interrupted poll (EINTR): empty result, descriptors kept. Bad descriptor (EBADF): descriptor dropped, no crash. MemoryError or an unknown OS error: propagates, not swallowed. Real files report readable/writable.
10. Hub raises OSError or AttributeError (connection lost, socket is None) while cleaning descriptors; expect: all stale descriptors removed from list/set/dict, nothing raised.
11. Descriptor becomes ready with no matching inqueue; expect: its reader is not removed from the hub.
12. Buffer empty while writers are active; expect: idle writers are removed, the writer for a still-active descriptor is kept.
13. A job finishes writing; expect: only the writer is removed for that descriptor, the reader stays.
14. Worker input pipe stays unwritable (over 100 consecutive EAGAIN), on header or on body; expect: "Process inqueue damaged" logged, the child terminated, the job put back on the outbound buffer for redispatch, writer cleaned up, the write ends cleanly (no RuntimeError).
15. Acknowledgement write for a child that no longer exists; expect: ack discarded, writer ends cleanly.
16. Register the pool with the event loop twice (consumer restart); expect: the periodic tick registered once, no leak.
17. Time limit armed, pool re-registers (broker reconnect), task then finishes; expect: the timer is still the same and gets cancelled and forgotten.
18. Create a pool with one process; expect: a "before create process" signal fires with the pool as sender.
19. Untrack a child with no sentinel attribute (cold-shutdown race); expect: no error, hub untouched. With a sentinel: removed from hub and cleared.

**Flush on reconnect (jobs in flight)**
20. Flush, acks off, one accepted and one unaccepted job; expect: unaccepted discarded (so it can be redelivered), accepted untouched.
21. Flush, acks on; expect: unaccepted job cancelled, accepted untouched.
22. Flush with a job whose child is dead; expect: pending writer dropped (no infinite loop), job discarded.
23. Flush with a job whose child is alive; expect: its writer is flushed to completion.
24. Flush with an ack write half done; expect: it is advanced to completion, not dropped (dropping would hang the child waiting for an ack).
25. Ack write raises OSError or EOFError during flush; expect: that writer discarded, flush completes.
26. Flush with a job whose write never started; expect: job discarded and writer removed, safe for redelivery.
27. Accepted job running on a live child; expect: that child stays marked busy (also when only the "scheduled for" link is known), so no task is queued behind a long-running one while another child idles.
28. Unaccepted job, or accepted job on a dead child; expect: its child is released from busy.
29. Mixed busy set (one accepted, one unaccepted); expect: one flush keeps the first busy and frees the second.

**Receiving results**
30. Result readiness for an unknown descriptor; expect: ignored, no crash. Known descriptor: message read and state updated.
31. Result half-read by the event loop when flush runs, rest then arrives; expect: message completed and delivered once, never via a blocking read that could hang.
32. Tail arrives late from a live child; expect: wait and complete, deliver once.
33. Child dies mid-message; expect: descriptor dropped, no delivery, no waiting forever.
34. Live child stalls past the deadline; expect: descriptor dropped, one warning, nothing delivered.
35. Pipe closes (EOF) mid-message, or polling fails (closed under us); expect: descriptor dropped, nothing delivered, no retry.
36. Flush when no partial read is pending (descriptor not on hub, no reader registered, not registered at all); expect: nothing to finish, no polling.

**Pool shutdown sentinels**
37. Shutdown with a child that already exited holding half a "stop" message, plus a live child with a full one; expect: exited child skipped, live child's message consumed.
38. A child looks alive but dies mid-read, leaving a truncated message; expect: shutdown survives and still handles the healthy child.

**Pool lifecycle and control**
39. Start, stop (close and join), stop again, start, terminate twice; expect: all repeatable without error, terminated recorded.
40. Restart; expect: pool restarted and a no-op job submitted. Report whether start succeeded.
41. Close when pool is running closes it; when not running, does nothing.
42. Stop with an event loop; expect: pool closed and joined, timers kept firing on a background daemon thread (joined within 1s) so late timeouts/acks complete.
43. A timer tick raises during shutdown; expect: loop continues to the next tick.
44. Late acks queued during shutdown, one failing; expect: all run, the failure reported through the error hook, the others unaffected. The transport reader is not run but put back for later close, so it feeds no deliveries to a closed pool.
45. Draining after join fails (hub lacks the hook); expect: warning logged with traceback, stop does not raise.
46. Stop without an event loop; expect: close and join only.
47. Submit work, grow by 1, shrink by 2; expect: sizes 10, 11, 9. Reported concurrency, per-child task limit ("N/A" if unset), timeouts (soft, hard) and process count are correct.
48. Child liveness timeout; expect: default 4s, configurable (8s).
49. Start with fork enabled; expect: fork mode used, start method untouched. Start with spawn: start method set to spawn and spawned children flagged as fresh interpreters.

---

_Generated by [Claude Code](https://claude.ai/code)_
