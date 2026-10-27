### `t/unit/events/test_cursesmon.py`

Terminal job monitor: one-line-per-job rows that must fit the screen. Portable guarantee is "a status listing never overflows its width and degrades predictably".

**Row layout**
- Show a job row (id, worker, task name, time, state) on a 91-column screen; expect: full 36-char job id, long worker name cut with a trailing "...", long task name shortened in the middle with "[.]", time and state intact, row ends with one space.
- Show the same row on a narrower 80-column screen; expect: job id cut to 20 chars plus "...", other columns still shown in order.
- Show a header row (UUID, TASK, WORKER, TIME, STATE); expect: labels padded to column widths, in the order id, worker, task, time, state.

**Adapting to screen size**
- Show the long row on a very wide 140-column screen; expect: id stays full, spare width goes to worker and task columns (less truncation), total row length is exactly 136 (screen width minus 4).
- Any width: expect: values longer than their column are truncated, never overflowing the screen.
