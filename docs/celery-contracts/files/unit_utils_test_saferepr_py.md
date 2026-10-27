### `t/unit/utils/test_saferepr.py`

Portable guarantee: the text shown for a job's args/kwargs/result (logs, dashboard, failure messages) is bounded, never crashes, and never hides a problem.

**Faithful display**
- Enqueue jobs with ints, floats, decimals, complex numbers, strings, bytes, tuples, lists, sets, frozensets, dicts, and subclasses of these (with or without custom repr); expect: displayed text identical to the normal Python repr.
- Enqueue a job with empty values (empty list, dict, set, string, bytes, tuple); expect: shown as empty, not blank or an error.
- Enqueue a job with a function, a range, or a 100-item list as args; expect: shown exactly as normal repr (small enough to fit).
- Enqueue a job with a dict having numeric keys, or a mixed numbers dict; expect: same display as normal repr.
- Enqueue a job with a string containing an apostrophe (`{"foo's": "bar's"}`); expect: displayed text can be parsed back to the original value.
- Enqueue a job with non-ASCII bytes (UTF-8 "oystein"); expect: shown readably as bytes, not mangled.
- Enqueue a job with binary (non-text) bytes; expect: displayed, with the raw content visible (e.g. hex-like content present), no decode error.
- Enqueue a job mixing plain bytes and non-ASCII bytes in a list; expect: displayed without error.

**Size limits (huge payloads)**
- Enqueue a job with a very long string value and a max display length; expect: output ends with an ellipsis, shorter than the input.
- Same for a very long non-ASCII byte string and a very long binary byte string; expect: truncated with ellipsis, still readable.
- A nested dict containing a huge text value, limit 100; expect: output truncated with `...` and the closing braces still present.
- Truncated value is the last item in a list, tuple, set, dict, or nested list/dict; expect: closing brackets are kept.
- Truncated value is followed by more items; expect: remaining items elided with `...`, brackets closed.
- Limit reached right before a nested container; expect: that container is omitted entirely (neither bracket shown), with `...` after it.
- Container deeper than the allowed nesting depth; expect: shown as a short placeholder with `...`, kept whole.
- Any payload (nested lists, dicts, tuples, sets, empties) with every limit from 1 up to full length; expect: brackets always balanced, never half-open.
- Display with a depth limit of 1 on a deeply nested dict; expect: no error.

**Unexpected input**
- Self-referencing structure (dict containing itself); expect: no infinite loop, output marks "Recursion on".
- Object whose repr raises an exception; expect: no crash, output says "Unrepresentable" instead, so a failing job's report is still produced.
