### `t/integration/test_loader.py`

**Task auto-discovery (sqlery analogue: job registration from modules)**
- Point discovery at a list of packages (one real, one that exists but has no jobs module), asking for a given module name; expect: the real package's job is registered under its full dotted name, and nothing is registered from the package lacking that module (no error, no phantom jobs).
- Point discovery at a list containing a real package and a package that does not exist; expect: discovery fails loudly with a "module not found" error naming the missing package (the missing import is not silently swallowed), rather than quietly skipping it.
- Discovery can be forced to run immediately instead of lazily; expect: jobs are registered as soon as the call returns.
- Job registered via a shared/app-independent decorator is visible in the app's job registry after discovery.
