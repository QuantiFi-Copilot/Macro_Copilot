# State architecture — Phase 0 close

This is the one-page tour of the durable substrate Phase 0 builds.
Five stores, each with a load-bearing invariant; one transactional
boundary discipline; one GC policy.

Per-store schema details + migration history live in
[`state_schema.md`](state_schema.md).  Replay semantics live in
[`replay.md`](replay.md).  This doc is the overview.

---

## The five stores

| Store | Tables | Purpose | Load-bearing invariant |
|---|---|---|---|
| Artifact store | `artifact_metadata` + object storage backend | Content-addressed typed-artifact payloads (Series, SeriesSet, EventSet, Panel, WindowedPanel) | Two artifacts with identical lineage have identical hashes (PR 2). |
| DAG store | `dags`, `dag_nodes`, `dag_edges` | Normalized DAG topology — query-friendly view of the lineage chain that produced an artifact | DAG hash is content-addressed via the same `_canonical_json` recipe as lineage (PR 10). |
| Workspace store | `workspaces`, `workspace_variants` | URL-addressable handle for a DAG + variant authoring scaffold | Slug derived once at create time; rename never re-derives — URL stability across renames (PR 10). |
| Conversation store | `sessions`, `turns`, `message_events`, `working_set` | Multi-turn chat lifecycle + per-session named bindings | Working set is append-mostly (retire, don't delete); historical resolution via `as_of_turn` reconstructs the binding active at any past turn (PR 8). |
| Version-pin store | `application_version`, `methodology_versions` | Code-revision + YAML-content registries pinned on every artifact | Identity is content (canonical-JSON of the parsed dict) not raw text; cosmetic edits do NOT create new rows (PR 9). |

The LangGraph framework's checkpoint tables live in a SEPARATE
schema (`langgraph_checkpoint`) — see
[`state_schema.md`](state_schema.md#langgraph-checkpoint-state-langgraph_checkpoint-schema--phase-0-pr-5)
for why we don't manage that schema with Alembic.

---

## Transactional boundaries

Every state mutation goes through the **`*, conn` keyword
convention** from PR 3.  The caller owns the transaction; helpers
never open sub-transactions.

A typical "save the result of a turn" call sequence wraps every
mutation in one `engine.begin()` block:

```python
with engine.begin() as conn:
    artifact_hash = put_artifact(art, conn=conn, object_storage=...)
    dag_hash      = persist_dag_from_lineage(art.lineage, conn=conn,
                                             head_artifact_hash=artifact_hash)
    workspace     = create_workspace(dag_hash, conn=conn, name=user_name)
    working_set.add(user_alias, artifact_hash, turn_id,
                    session_id=session_id, conn=conn)
    commit_turn(turn_ctx, conn=conn, status="completed",
                terminal_artifact_hash=artifact_hash,
                save_as=user_alias)
```

Any exception unwinds all four writes atomically.  FK directions
enforce ordering: artifact must exist before the DAG references it;
DAG must exist before the workspace references it.

The artifact store + DAG repo use **`psycopg2-binary` via SQLAlchemy
Engine** (sync path, market-data + state writes).  The LangGraph
checkpointer uses **`psycopg[binary,pool]` via `AsyncConnectionPool`**
(async path, conversation state).  Same Postgres; two drivers; two
pools; the split is intentional and documented in
[`state_schema.md`](state_schema.md#two-drivers-one-postgres).

---

## Idempotency keys

Every mutation in the state layer is idempotent under its content
key, so retries are safe:

- **`put_artifact`** — keyed on `artifact.lineage.head_hash`.  Re-put
  is a no-op.
- **`persist_dag_from_lineage`** — keyed on canonical-JSON topology
  hash.  Re-persist is a no-op.
- **`create_workspace`** — keyed on the workspace UUID; the slug's
  8-char UUID suffix gives a 2^32 namespace per name.  The partial
  unique index `ix_workspaces_slug_active` is the DB-level backstop.
- **`register_yaml`** — keyed on `yaml_content_hash`.  Cosmetic
  reformatting (whitespace, key order) registers as the SAME row.
- **`register_application_version`** — keyed on `git_commit`.

Working-set bindings are NOT idempotent — `add` of an existing name
RETIRES the old row and inserts a new one (append-mostly semantics).
This is by design: a rebind is meaningful state, not a retry.

---

## GC policy

Three GC surfaces, two implemented + one deferred:

1. **Artifact reference walk** (PR 7) —
   `state.gc.find_unreferenced_artifacts` returns hashes whose
   metadata row is older than `older_than_days` AND has no
   reference from `dag_nodes` or `working_set`.
   `state.gc.purge_artifact` deletes the row + the blob.  Shipped,
   tested, **NOT wired to a scheduler**; the Prefect worker is the
   natural future host.

2. **Working-set retirement** (PR 8) — a rebind retires the prior
   binding rather than deleting it.  Retired rows are excluded
   from the partial unique index but remain queryable for
   historical resolution.  No reaper yet — retired rows are
   small + bounded by the rebind cadence.

3. **Workspace last-accessed reaper** (PR 11) — the
   `workspaces.last_accessed_at` column is in place; the sweep
   that populates + reads it is Phase 1+.  Deliberate dead weight
   in the schema today, documented in
   [`state_schema.md`](state_schema.md).

---

## Phase 0 performance baseline

PR 11 ships
[`tests/integration/test_performance_baseline.py`](../../tests/integration/test_performance_baseline.py)
as a CI guardrail.  Local measurements at Phase 0 close (single
machine, `postgres:14`, `LocalFSBackend`):

| Metric | Target | Local p95 | Alarm bound |
|---|---|---|---|
| Working-set load (50 active rows) | < 50 ms | ≈ 1 ms | 400 ms |
| Artifact fetch (inline payload) | < 10 ms | ≈ 3 ms | 100 ms |
| Artifact fetch (blob, cold) | < 300 ms | ≈ 2 ms | 1.5 s |
| Workspace open (12-node DAG, summaries only) | < 500 ms | ≈ 18 ms | 2 s |
| Cache hit (Redis, when configured) | < 5 ms | ≈ 1 ms | 100 ms |

The CI alarm bound is intentionally generous so flaky-runner
variance doesn't false-positive; a 5×+ regression trips loudly.

---

## Optional Redis cache

PR 11 adds an opt-in Redis bytes cache (`state.cache`) in front of
the artifact store's blob-fetch path:

- **Default**: `NullCache`.  Set `MACRO_COPILOT_REDIS_URL` to
  enable `RedisBytesCache`.
- **Read-through, not write-through**: `put_artifact` doesn't
  populate the cache; the first `get` after a put warms it.  Avoids
  the cache-vs-DB ordering question on writes.
- **Non-load-bearing**: every cache call is wrapped in
  `try/except → log + fall through`.  A Redis outage degrades
  performance but never affects correctness.  The
  [`test_cache.py`](../../tests/state/test_cache.py) suite asserts
  that disabling the cache mid-flight produces byte-identical
  responses.
- Metadata reads (artifact_metadata row, lineage JSONB) go straight
  to Postgres — only the bytes payload is cached.

---

## Closed Phase 0 tech-debt items

- **#1**  Non-atomic delete+upsert in ingestion — closed by PR 3.
- **#6**  Persistent LangGraph checkpointer — closed by PR 5.
- **#20** Normalized data hash stability — closed by PR 2.

Full closure annotations in
[`docs/technical_debt.md`](../technical_debt.md).
