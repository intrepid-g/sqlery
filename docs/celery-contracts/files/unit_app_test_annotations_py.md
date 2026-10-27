### `t/unit/app/test_annotations.py`

Annotations are per-job-type option overrides (e.g. a default retry limit or rate limit applied to a named job without editing its code).

**Per-job overrides**
- Configure overrides for job "add" only; ask for the overrides of "add"; expect: exactly the configured options returned.
- Same configuration, ask for the overrides of job "mul" (not configured); expect: nothing returned (no overrides, no error).

**Wildcard overrides**
- Configure overrides under the wildcard "*"; ask for the any-job overrides; expect: the wildcard options returned.
- Configure no overrides at all; ask for the any-job overrides; expect: nothing returned (no error).

**Normalising configuration input**
- Provide overrides as a plain mapping of job name to options; expect: it is converted into a usable override rule.
- Provide a single rule, a list of one, or a tuple of one; expect: always normalised to a list of one rule.
- Provide no configuration (None); expect: an empty result, not a failure.
- Provide a rule as a dotted import path string (bare or inside a list); expect: the path is resolved to the real object, and its options (e.g. a default value of 65) are applied.
