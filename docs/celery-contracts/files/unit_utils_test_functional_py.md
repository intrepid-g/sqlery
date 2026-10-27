### `t/unit/utils/test_functional.py`

Celery's small functional helpers (lazy sequences, argument-signature checks). Few queue semantics here; the portable guarantees are about lazy sequences (job batches), validating job arguments before running, and errors never being swallowed.

**Wrapping a job run**
- Run a job inside a no-op context; expect: job completes normally.
- Job raises (KeyError) inside that context; expect: the same error reaches the caller, not swallowed.

**Padding / defaults**
- Pad a 3-item list to 3; expect: unchanged.
- Pad a 2-item list to 3; expect: filler is None.
- Pad a 3-item list to 4 with default "Earth"; expect: filler is the default.

**Picking the first handler that answers**
- Handlers that lack the method; expect: no result (None), no crash.
- Several handlers, the 4th returns a value; expect: that value, earlier empty ones skipped.
- A handler supplied lazily; expect: resolved at call time, same answer.
- Method itself raises AttributeError (bug inside the handler); expect: error propagates, not mistaken for "missing method".
- Find first item matching a test in 0..9 (match at 5); expect: 5, tested exactly 6 times (stops early).
- No item matches; expect: None, every item tested once.

**Small helpers**
- Iterate items with their successor; expect: last item pairs with None.
- Wrap a scalar into a list, leave a list alone, leave None as None.
- A lazy value computed once; expect: second call returns cached value without recomputing, repr shows value.

**Lazy re-iterable sequence (batch of jobs from a generator)**
- Given a real list; expect: the same object returned.
- Wrap a generator, read it twice; expect: identical both times, length hint 0 once fully consumed, pickling yields a plain list.
- Read items by index in any order (7, 6 ... 0, then 8); expect: correct values, cached.
- Index past the end; expect: IndexError, and a full iteration afterwards still yields all 10.
- Negative indexes; expect: same as list.
- Index types: objects with __index__ and True accepted; "x", None, 1.0 raise TypeError; iteration unaffected.
- Slices (head, middle, tail, step, negative, reversed, empty, full); expect: identical to a list, for both generator and list sources.
- Truthiness check; expect: consumes only the first item (never the second); empty source is falsy.
- A deque source compared repeatedly; expect: equal to the original list every time.
- repr; expect: does not consume the source ("..." shown).
- Source fails midway after yielding everything; expect: error propagates, sequence is not marked done, re-iterating duplicates elements (known bug #6794, tracked as expected-fail).
- Length hint before consuming; expect: the source's count (10).
- Repeated access of an already-read index; expect: no new work; reading one past pulls only one more item.
- Done flag; expect: set exactly when the last item is yielded, not one step late.
- Iterating over already-cached items; expect: nothing further pulled from source (chord headers must stay lazy, #3021).
- Calling len() or reading [-1] while iterating; expect: iteration is not cut short.
- Apply a map transform while iterating a cached sequence; expect: final contents all transformed.
- Source's own iterator consumes the cache when started; expect: still yields [1, 2] correctly.
- Nested loops over the same sequence; expect: full cross product.
- Look-ahead is at most one item: bool, [0], and a single next do not overconsume; reading [1], a full loop, or the whole data do read to the end, and if the source then raises, the error surfaces but items read so far stay cached.

**Validating job arguments without running the job**
- Call the signature check on a callable object or function with too few args; expect: TypeError; correct positional and keyword calls pass.
- Function with *args and keyword-only default; expect: valid calls pass, missing required positional raises TypeError (regression 3678).
- Keyword-only parameters, some required; expect: any missing required one (or positional use) raises TypeError, all required given passes.
- Type-annotated function; expect: same behaviour.
- Class method, bound vs unbound; expect: argument list matches the form used.
- Annotations naming types that are not importable at runtime (Python 3.14); expect: no NameError, normal validation.
- Function wrapped by a variadic decorator (injects extra kwargs); expect: validated against the wrapper's signature, so any args accepted.
- Does the callable take argument "foo"? Yes for **kw, a named parameter, *args, or at/after a stated position; no if absent, if no params, or if "foo" sits before the stated position.
- Does the callable accept **kwargs? Yes for plain functions and callable objects with **kwargs; no for fixed args, *args only, or defaults only.
- Same check with unresolvable annotations (Python 3.14); expect: correct True/False, no NameError.

**Sequence concatenation**
- Concatenate two sequences (tuple/list mixes); expect: result keeps the first one's type.
- Append an item to a tuple or list; expect: same type as the input.

**Numeric check**
- 5, 5.0, 0, 0.0 are numeric; True, "value", "5", "5.0", None are not (bool explicitly excluded).

---

_Generated by [Claude Code](https://claude.ai/code)_
