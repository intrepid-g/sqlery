### `t/unit/test_canvas.py`

Celery "canvas" = job templates (signatures) with callbacks, error callbacks and chords. Closest sqlery ideas: job templates, on-success / on-failure follow-up jobs, dependencies. The guarantees are about isolation and identity.

**Reply address / identity**
- Build a job template and freeze it (assign its identity); expect: it carries a valid UUID reply-to address equal to the current thread's own address.
- Freeze the same template from a second thread; expect: a different valid UUID reply-to address (reply-to is per-thread, never shared across threads).

**Cloning a job with follow-up jobs (on-success, on-failure, final chord callback): isolation**
- Template job with a follow-up attached (as a live object or as its serialized form), for each of success / failure / chord callback; clone it twice and stamp each clone's follow-up with a different workflow id; expect: each clone keeps its own id, the original follow-up has no workflow id and no stamp headers.
- Same setup, but freeze each clone's follow-up with a different explicit job id and root id; expect: each gets its own id and root id, the original follow-up still has no id and no root id.
- Follow-up that itself has a follow-up (nested); clone and stamp the clone; expect: both nested levels in the clone are stamped, neither original is touched.
- Same follow-up registered as both on-success and on-failure; clone; expect: in the clone both slots point to the same single copy (sharing preserved), and that copy is not the original; changing its queue leaves the original's queue unset.
- Job that is its own follow-up (circular) and is deep-copied; expect: no infinite loop, the copy refers to itself (not the original), original still refers to itself.

**Cloning keeps arguments untouched**
- Follow-up whose arguments are lazy iterators; clone the parent; expect: clone holds the very same iterator objects (not consumed, not copied), and each still yields its full sequence afterward.
- Follow-up already given an explicit job id; clone parent; expect: the clone keeps that id (freezing it again does not change it) and the original keeps it too.
