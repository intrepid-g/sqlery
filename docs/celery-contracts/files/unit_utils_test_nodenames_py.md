### `t/unit/utils/test_nodenames.py`

**Worker identity (node names)**
- Start a worker with no name given; expect: it is named `<default>@<this machine's hostname>`.
- Start a worker with only a host (`example.com`); expect: the default name is prefixed, giving `<default>@example.com`.
- Start a worker with a full `name@host`; expect: it is kept exactly as given.
- Start a scheduler/other component with its own default name (e.g. `scheduler`) and a bare host or no host; expect: its default name replaces only a missing name, so `scheduler@example.com` or `scheduler@<local host>`.
- Same component given a full `name@host`; expect: the given name wins over the component default.

**Per-worker queues**
- Ask for the dedicated queue of a worker when handed an already-built queue object; expect: the same object comes back unchanged (no duplicate or rewrap).

**Log/pid file name templates**
- Template contains escaped percent sequences (`%%n`, `%%i`, `%%I`); expect: they come out as the literal `%n`, `%i`, `%I`, not substituted.
- Template mixes an escaped `%%n` with a real `%n`; expect: only the real one is replaced by the worker's name, the escaped one stays literal.
