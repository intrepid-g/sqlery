### `t/unit/app/test_utils.py`

Portable guarantees: configuration behaves like a real mutable mapping, copies are isolated, and secrets never leak into diagnostic output (fail closed).

**Configuration object**
- Load config (new-style, namespaced, and legacy key names), copy it with either copy method, configured or not; expect: copy has the same values and custom defaults, and changing the copy leaves the original unchanged.
- Copy a config that has deprecated-key tracking, before or after clearing it; expect: contents equal, deprecated-key info preserved (unless cleared).
- Ask whether a value was explicitly set; expect: true for a configured key (any naming style), false for an untouched one.
- Config is both a read-only Mapping and a MutableMapping.
- Look up an option by short or fully qualified name; expect: found, with its type and default (e.g. eager mode defaults to false).
- Look up a value by key parts or short key; expect: dynamic/custom keys resolvable, defaults returned.
- Clear a config that was preconfigured with an override; expect: the override is gone and the default returns.
- Render the config table with/without defaults and with/without censoring; expect: all four render non-empty.

**Hiding secrets in settings output**
- Settings with non-string keys (tuples, nested); expect: filtering does not raise.
- Broker URL, read URL and write URL with passwords; expect: no password appears in any.
- Multi-server result-store URL with per-host credentials; expect: each password masked, usernames and hosts kept.
- Azure connection strings (account key, shared access signature, SAS token starting with "?", key containing "#", signature inside an endpoint query, any letter case); expect: secret fully masked, unrelated parts (account name, endpoint, other query params) kept.

**Bug report output**
- Connection has no transport/driver info; expect: report still produced, no crash.
- Multi-server result store without credentials; expect: shown unchanged.
- Passwords (multi-server, Redis with commas and "@", Sentinel, Azure variants); expect: never appear in the report, masked as "********".

**URL sanitising**
- None, empty string, numbers, empty list; expect: returned unchanged.
- Plain URL, URL with password, the word "disabled"; expect: only the password is masked.
- Multi-host URLs separated by ";" or ","; with or without trailing slash, path or query; expect: structure preserved, each host's password masked.
- Empty chunk between hosts (";;"); expect: dropped.
- Sentinel URLs with per-host or single shared password, and mixed auth/no-auth hosts; expect: only passwords masked.
- Password containing a separator (";", ",", "@"); expect: fully masked, no fragment leaks.
- Ambiguous host boundaries; expect: the whole ambiguous value masked rather than guessing.
- Separator inside the query string; expect: query kept intact.
- Username without password; expect: unchanged.
- Malformed URL with no credentials; expect: returned as is.
- Malformed URL with a password (bad port); expect: fails closed, password still masked.
- Sanitiser itself hits an unexpected error while parsing; expect: falls back to regex masking, or returns "<unparsable url>" if no credentials found, never the secret.
