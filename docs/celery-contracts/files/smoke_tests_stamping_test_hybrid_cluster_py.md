### `t/smoke/tests/stamping/test_hybrid_cluster.py`

Mixed-version worker clusters (current, legacy, latest release): metadata ("stamps") attached to a job must survive and stay scoped. sqlery analogue: job metadata/tags and workers of different versions sharing one database.

**Metadata reaches every worker**
- Attach metadata to a job, enqueue it to each worker's queue in turn; expect: job result returned within the timeout, and each worker's log shows the metadata.

**Metadata follows a job across workers**
- Chain job A (worker 1's queue) then job B (worker 2's queue), metadata set on the whole chain; expect: both jobs done, both workers saw the metadata.

**Metadata stays scoped to its own job**
- Chain A (own metadata X, worker 1) then B (own metadata Y, worker 2), plus shared metadata on the chain; expect: worker 1 sees X but never Y, worker 2 sees Y but never X, shared metadata on both.

**Replacement jobs across versions**
- Dev-worker job replaces itself with a job (own metadata) routed to a legacy worker, twice in a chain, chain-level metadata set; expect: dev worker sees the chain metadata but not the replacement metadata; legacy worker sees the replacement metadata but not the chain-only metadata.

**Cluster shape**
- Hop, multi-metadata and replacement scenarios need 2+ workers (and the right worker kinds); with fewer, they are skipped, not failed. Every scenario runs against: dev only, legacy only, dev+legacy, dev+latest, and all three.
