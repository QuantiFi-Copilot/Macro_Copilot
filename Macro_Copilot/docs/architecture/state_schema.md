# State Schema (`copilot_state`)

The Phase 0 state foundation lives in its own Postgres schema, `copilot_state`,
separate from the market-data schema `macro_data`. This document is the
authoritative reference for what's in that schema, why it's shaped this way,
how it evolves, and how the project's runtime code is expected to interact
with it.

The schema is managed by Alembic. The operational guide for running and
authoring migrations is in [`migrations/README.md`](../../../migrations/README.md).
This document is for understanding *what's there*; the migrations README is
for understanding *how to change it*.

---

## Intent

Phase 0 turns three properties from claims into runtime behavior:

1. **Conversations are durable.** A server restart does not lose an active
   conversation.
2. **Artifacts are addressable across turns.** Turn 3 of a conversation can
   reference an artifact produced by turn 1 by name.
3. **Workspaces are URL-addressable, replayable, six-month-stable.** Opening
   a workspace URL today and opening it in 90 days reproduces the same numbers,
   assuming the underlying market data has not been revised.

Every table in `copilot_state` exists to support one of those three
properties. The schema is not a forecast of every future feature — it is the
minimum durable structure that lets Phase 1 / 2 / 3 build the visible loop
behavior on top of a solid foundation.

---

## Why a separate schema from `macro_data`

Two schemas, two lifecycles, two ownership models:

| | `macro_data` | `copilot_state` |
|---|---|---|
| What it holds | Market data: instrument master, daily observations, load audit. | Conversation, artifact, workspace, DAG, methodology state. |
| Lifecycle | Stable. Bloomberg ingestion writes; analytics read. The schema rarely changes. | Evolving. Phase 0 weeks 3–8 will add columns / tables as the runtime state model grows. |
| Schema source of truth | Hand-written SQL in `Macro_Copilot/database/schema.sql`, bootstrapped by `docker-compose tsdb`'s `docker-entrypoint-initdb.d` mount. | Alembic migrations under `migrations/versions/`. |
| Extensions | `timescaledb` (used by `market_data_daily` hypertable). | None — plain Postgres 14+. |

Keeping them in separate schemas means:

- The two lifecycles do not interfere. An evolving `copilot_state` does not
  risk breaking the stable `macro_data`, and vice versa.
- Migrations under `migrations/versions/` are explicitly scoped: each
  `op.create_table(...)` carries `schema="copilot_state"`. Forgetting it is a
  visible mistake (objects would land in `public`), caught by the schema-
  isolation test in `tests/state/test_migrations.py`.
- Queries can be reasoned about by their target schema. A read-only consumer
  of market data needs no access to conversation state; an LLM orchestrator
  reading workspaces does not need access to instrument master.

---

## The twelve tables

In dependency order — each table only references tables defined above it.

### `application_version`

```
id           BIGSERIAL PRIMARY KEY
git_commit   VARCHAR(40) NOT NULL UNIQUE  (CHECK length=40)
deployed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
notes        TEXT
```

One row per code revision. Used by Phase 0 week 6's "replay against original
methodology" to detect the case where lineage hashes match but the producing
code has changed (a meaningful divergence signal — same hash, different code).
Not load-bearing yet; the slot is reserved.

### `methodology_versions`

```
id                  BIGSERIAL PRIMARY KEY
yaml_path           TEXT NOT NULL                          -- metadata only, not in identity
yaml_content_hash   VARCHAR(64) NOT NULL UNIQUE            -- SHA-256 hex of canonical YAML
yaml_content        JSONB NOT NULL                         -- the parsed YAML body
recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
```

One row per *distinct* tool-config YAML content snapshot, deduplicated by
content hash. When an artifact is produced under a specific tool config, the
artifact records the `methodology_versions.id` it used. This is what makes
"replay this workspace under its original methodology six months later" work:
the YAML body is captured at construction time, not just its filesystem path.

### `artifact_metadata`

```
hash                       VARCHAR(64) PRIMARY KEY           -- SHA-256 hex, content-addressed
artifact_type              VARCHAR(64) NOT NULL              -- Series / EventSet / Panel / ...
units                      VARCHAR(32)
frequency                  VARCHAR(32)
row_count                  INT
byte_size                  BIGINT NOT NULL DEFAULT 0         -- (CHECK >= 0)
payload_uri                TEXT                              -- object storage URI
inline_payload             JSONB                             -- inline body
lineage                    JSONB NOT NULL                    -- Lineage chain serialized
methodology_version_ids    BIGINT[]                          -- not a FK array, see comment
created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
```

Constraint: **exactly one of** `payload_uri` / `inline_payload` is non-null
(enforced by `ck_artifact_metadata_payload_exactly_one`). Small artifacts
(default cap: 100 rows or 8KB JSON) live inline; large artifacts live in
object storage as Parquet keyed by `hash`. This split is what makes Phase 0
week 4's artifact store efficient — workspace open does a single Postgres
read for summary metadata plus zero network round-trips for the payloads of
small nodes.

`methodology_version_ids` is a plain `BIGINT[]` rather than a foreign-key
array because PostgreSQL does not natively enforce FK constraints on array
elements. Application-layer integrity is acceptable here because deletes
from `methodology_versions` are rare and orphaned IDs are harmless — the
display layer just shows "methodology not captured" for the unresolvable
entry.

### `dags`

```
hash         VARCHAR(64) PRIMARY KEY      -- SHA-256 hex of topology + node params
topology     JSONB NOT NULL                -- full DAG shape, denormalized for speed
created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
```

Content-addressed DAG record. The `topology` JSONB carries the entire DAG
shape (nodes, edges, slot bindings) for fast round-trip; the normalized
`dag_nodes` + `dag_edges` tables below carry the same information in
queryable form for cross-DAG queries.

### `dag_nodes`

```
dag_hash      VARCHAR(64) FK -> dags.hash      ON DELETE CASCADE
node_id       TEXT                              -- caller-assigned identifier within DAG
kind          VARCHAR(32) NOT NULL              -- "primitive" | "operator"
name          VARCHAR(128) NOT NULL             -- tool / operator name
params        JSONB NOT NULL DEFAULT '{}'
artifact_hash VARCHAR(64) FK -> artifact_metadata.hash  ON DELETE RESTRICT
PRIMARY KEY (dag_hash, node_id)
```

One row per node in a DAG. `artifact_hash` is null for unexecuted nodes
(e.g. a workspace plan that has not run yet); set once the node produces an
artifact. The reverse index on `artifact_hash` makes "which DAGs produced this
artifact" a fast lookup, used by garbage collection in Phase 0 week 4+.

### `dag_edges`

```
dag_hash    VARCHAR(64) FK -> dags.hash       ON DELETE CASCADE
from_node   TEXT NOT NULL
to_node     TEXT NOT NULL
slot_name   VARCHAR(64) NOT NULL              -- which input slot this edge populates
PRIMARY KEY (dag_hash, from_node, to_node, slot_name)
```

Directed edge with the input slot it binds. A node may have multiple incoming
edges (one per slot — e.g. `signal`, `target` for an event_study primitive).
The composite PK allows multiple distinct (from_node, to_node) pairs only if
they bind different slots, which is the right semantics for the operator
algebra.

### `sessions`

```
id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id       VARCHAR(128)                                -- nullable in V0
started_at    TIMESTAMPTZ NOT NULL DEFAULT now()
archived_at   TIMESTAMPTZ                                  -- null = active
label         TEXT                                         -- user-given session label
```

One row per conversation thread. UUID PK so a session has a stable URL handle
from the moment it's created. `user_id` is nullable in V0 (single-org
assumption) and becomes required when multi-tenancy lands.

### `turns`

```
id                  UUID PRIMARY KEY DEFAULT gen_random_uuid()
session_id          UUID FK -> sessions.id          ON DELETE CASCADE
sequence_no         INT NOT NULL                    -- monotonic per session
user_message        TEXT NOT NULL
assistant_response  TEXT                            -- null while in flight
dag_id              VARCHAR(64) FK -> dags.hash     ON DELETE SET NULL
status              VARCHAR(16) NOT NULL DEFAULT 'running'
                       (CHECK status IN ('running','completed','failed','cancelled'))
started_at          TIMESTAMPTZ NOT NULL DEFAULT now()
completed_at        TIMESTAMPTZ
UNIQUE (session_id, sequence_no)
```

One row per turn within a session. `sequence_no` is monotonic per-session
(unique constraint enforces this); the LLM-facing layer sets it as turns are
created. `dag_id` references the DAG produced for this turn, if any; SET NULL
on delete preserves the turn even if its DAG is later garbage-collected.

### `message_events`

```
id           BIGSERIAL PRIMARY KEY
turn_id      UUID FK -> turns.id              ON DELETE CASCADE
sequence_no  INT NOT NULL                     -- monotonic per turn
event_type   VARCHAR(64) NOT NULL              -- token, tool_call, route_decision, ...
payload      JSONB NOT NULL
emitted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (turn_id, sequence_no)
```

Append-only stream record. One row per `SessionEvent` emitted over the
WebSocket during a turn. Replay reconstructs the user-facing stream from this
table; the orchestrator's 13-value event enum is documented in
`orchestrator/events.py` and is **not** enforced via a DB CHECK constraint
to leave room for additive evolution.

### `workspaces`

```
id                    UUID PRIMARY KEY DEFAULT gen_random_uuid()
name                  TEXT                                              -- user-given label
dag_hash              VARCHAR(64) FK -> dags.hash         ON DELETE RESTRICT
focus_node            TEXT                                              -- UI focus pointer
parent_workspace_id   UUID FK -> workspaces.id            ON DELETE SET NULL
schema_version        INT NOT NULL DEFAULT 1                             -- data-format version
created_by            VARCHAR(128)
created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
```

One row per workspace. URL is `/workspace/:id` where `:id` is the UUID; an
optional human-readable slug uses `name`. Forks set `parent_workspace_id` to
the source workspace; SET NULL on parent delete preserves the fork tree.

`schema_version` is the workspace **data-format** version — distinct from the
DB schema version which Alembic owns. See "Schema versioning policy" below.

### `workspace_variants`

```
id                BIGSERIAL PRIMARY KEY
workspace_id      UUID FK -> workspaces.id         ON DELETE CASCADE
variant_dag_hash  VARCHAR(64) FK -> dags.hash      ON DELETE RESTRICT
override_summary  JSONB NOT NULL                    -- what changed; shape app-defined
created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
```

One row per side-by-side variant within a workspace. Variants arise from
convention-override forks ("show me this under ACT/365 instead of ACT/360");
each variant references its own `dag_hash` because the override produces a
distinct content-addressed DAG.

### `working_set`

```
id                   BIGSERIAL PRIMARY KEY
session_id           UUID FK -> sessions.id              ON DELETE CASCADE
name                 TEXT NOT NULL
artifact_hash        VARCHAR(64) FK -> artifact_metadata.hash  ON DELETE RESTRICT
introduced_at_turn   UUID FK -> turns.id                 ON DELETE RESTRICT
retired_at_turn      UUID FK -> turns.id                 ON DELETE RESTRICT   -- null = active
UNIQUE (session_id, name) WHERE retired_at_turn IS NULL  -- partial index
```

Per-session map of `{name → artifact_hash}`. Append-mostly:

- When a user introduces a name in turn N, a new row is inserted with
  `introduced_at_turn = N`, `retired_at_turn = NULL`.
- When the user re-binds the same name in turn M > N, the OLD row is updated
  with `retired_at_turn = M`, and a NEW row is inserted with
  `introduced_at_turn = M`.

The partial unique index on `(session_id, name) WHERE retired_at_turn IS NULL`
guarantees at most one ACTIVE binding per name per session, while letting
prior turns continue to resolve historical bindings via lookups that include
`retired_at_turn IS NULL OR retired_at_turn > <turn>`.

---

## FK topology

Independent (no incoming FKs):

```
application_version
methodology_versions
artifact_metadata    (.methodology_version_ids is BIGINT[], not FK)
dags
sessions
```

One-level dependents:

```
sessions    ─→  turns                (CASCADE on delete)
                  └→  message_events  (CASCADE)
                  └→  working_set     (CASCADE)
dags        ─→  dag_nodes             (CASCADE)
            ─→  dag_edges             (CASCADE)
            ─→  turns.dag_id          (SET NULL — keep turn, drop DAG ref)
            ─→  workspaces.dag_hash   (RESTRICT — refuse to delete DAG in use)
            ─→  workspace_variants.variant_dag_hash  (RESTRICT)
artifact_metadata ─→ dag_nodes.artifact_hash         (RESTRICT)
                  ─→ working_set.artifact_hash       (RESTRICT)
workspaces  ─→  workspaces.parent_workspace_id       (SET NULL — preserve forks)
            ─→  workspace_variants                   (CASCADE)
turns       ─→  working_set.introduced_at_turn       (RESTRICT)
            ─→  working_set.retired_at_turn          (RESTRICT)
```

`CASCADE` is used only where the relationship is strictly parent–child
(sessions own turns own events; DAGs own their nodes and edges; workspaces
own their variants). `RESTRICT` is used where the referenced object is shared
or its lifetime is independent (artifacts persist beyond the DAGs that
produced them; DAGs persist beyond the workspaces that point at them).
`SET NULL` is used where the referencing row should outlive the referenced
one — a turn outlives its (possibly garbage-collected) DAG; a fork outlives
its possibly-deleted parent.

---

## Indexing strategy

Beyond primary keys and foreign-key indexes, the migration creates the
following indexes to support common access patterns:

| Index | Table | Purpose |
|---|---|---|
| `ix_artifact_metadata_artifact_type` | `artifact_metadata` | Filter by type when scanning recent artifacts. |
| `ix_artifact_metadata_created_at` | `artifact_metadata` | Garbage-collection sweeps by age. |
| `ix_dag_nodes_artifact_hash` | `dag_nodes` | "Which DAGs produced this artifact" reverse lookup. |
| `ix_dag_nodes_kind_name` | `dag_nodes` | "Find all DAGs that used this operator" queries. |
| `ix_sessions_user_active` | `sessions` | Active-session list per user (partial: `WHERE archived_at IS NULL`). |
| `ix_sessions_started_at` | `sessions` | Time-ordered session list. |
| `ix_turns_session_status` | `turns` | "Show me failed turns in this session" diagnostics. |
| `ix_message_events_event_type` | `message_events` | Filter event stream by type. |
| `ix_workspaces_name` | `workspaces` | Human-readable URL lookup (partial: `WHERE name IS NOT NULL`). |
| `ix_workspaces_parent` | `workspaces` | Fork-tree walk (partial: `WHERE parent_workspace_id IS NOT NULL`). |
| `ix_workspaces_created_by` | `workspaces` | Per-user workspace list (partial). |
| `ix_workspace_variants_workspace` | `workspace_variants` | Render all variants of a workspace. |
| `ix_working_set_session` | `working_set` | Per-session working-set scan. |
| `uq_working_set_session_name_active` | `working_set` | UNIQUE partial: at most one active binding per name per session. |

Partial indexes (`WHERE …`) are used where the indexed predicate is the
common-case query — they trade index size for selectivity. The
`uq_working_set_session_name_active` partial unique is what enforces the
"at most one active binding per name" invariant; full unique on
`(session_id, name)` would fail as soon as a user re-binds.

---

## Schema versioning policy

Two distinct versioning concepts live in this schema:

### 1. DB schema version — owned by Alembic

`copilot_state.alembic_version` (single-column, single-row table managed by
Alembic) tracks which migrations have been applied. Migrations are linear,
hand-written, and reversible (every `upgrade` has a matching `downgrade`).
Operationally:

- A migration that has been applied to any shared environment (CI, staging,
  prod) is **immutable**. To fix a mistake, add a new migration that corrects
  it forward.
- The forward chain is verified by CI on every PR via
  `tests/state/test_migrations.py`.

### 2. Workspace data-format version — owned by application code

`copilot_state.workspaces.schema_version` is an integer column on every
workspace row. It tracks the shape of the workspace's data — slot bindings,
override summaries, lineage chain layout — which can evolve independently of
DDL because two formats can coexist in the same `JSONB` columns.

The rule:

- **Writers** stamp every new workspace with the current code's
  `WORKSPACE_FORMAT_VERSION`.
- **Readers** refuse to read a workspace whose `schema_version` is ahead of
  the reader's code. The expected failure surface is a clean error like
  "this workspace was created by a newer release; please update", NOT a
  silent misread.
- **Rolling forward** is a coordinated app release that can read both old and
  new formats. The migration is in *code*, not in Alembic DDL.

The first stamped version is `1`, the value the column defaults to. Phase 3's
Workspace redesign (or any earlier change that breaks workspace data shape)
will bump to `2` and ship a reader that handles both.

---

## Dev workflow

The runtime stack is conda-managed (because Bloomberg's `xbbg` / `blpapi` is
not PyPI-installable). Migrations work with either conda or pip:

```bash
# Option A: conda env (matches production)
conda env create -f Macro_Copilot/environment.yml
conda activate macro-env
pip install "alembic>=1.13" "psycopg2-binary>=2.9"   # already in environment.yml's pip section

# Option B: pip-only (lightweight; sufficient for migrations + state tests)
pip install -e ".[dev]"

# Start the project's Postgres locally
cd Macro_Copilot && docker compose up -d tsdb
cd ..

# Apply migrations
alembic upgrade head

# (or undo them, then re-apply)
alembic downgrade base
alembic upgrade head
```

Default connection env vars (from `database.database.get_db_engine` and
`migrations/env.py`):

```
DB_USER=quantuser
DB_PASSWORD=myStrongPass
DB_HOST=localhost
DB_PORT=5433
DB_NAME=macrodata
```

These defaults match the docker-compose `tsdb` service; they are
**deliberately** unsafe outside the local Docker network and must be
overridden in CI / staging / prod.

---

## CI gate

`.github/workflows/ci.yml` runs a dedicated `migrations` job on every PR
and every push to main. The job:

1. Brings up a Postgres 14 service container.
2. Installs Alembic + psycopg2 + SQLAlchemy + psycopg3 +
   langgraph + langgraph-checkpoint-postgres.
3. Runs `alembic upgrade head` → `alembic downgrade base` →
   `alembic upgrade head` (the idempotence pass) against the service.
4. Runs `pytest Macro_Copilot/tests/state/test_migrations.py`, which
   re-runs the round trip plus the schema-invariant spot checks (CHECK
   constraints present, `schema_version` default, alembic_version in
   the right schema, etc.).
5. Runs `pytest Macro_Copilot/tests/state/test_postgres_checkpointer.py`
   and `test_session_restart.py` (Phase 0 PR 5) which exercise the
   `AsyncPostgresSaver` end-to-end against the same Postgres.

A migration or checkpointer test that fails any step does not merge.

---

## LangGraph checkpoint state (`langgraph_checkpoint` schema) — Phase 0 PR 5

In addition to the `copilot_state` schema documented above, this
project also uses a separate Postgres schema called
`langgraph_checkpoint` for the LangGraph framework's internal
conversation-state tables. This section documents why it's separate,
what's in it, and how it's bootstrapped.

### Why a separate schema (not in `copilot_state`)

Two reasons:

1. **Lifecycle separation.** The four tables in `langgraph_checkpoint`
   are owned by the `langgraph-checkpoint-postgres` library, not by
   this project. Their shape can change between library versions; if
   they lived in `copilot_state` we would have to mirror the library's
   DDL in our Alembic migrations and chase upstream changes whenever
   the library schema evolves.
2. **Test cleanliness.** The schema-isolation test
   (`test_creates_all_expected_tables`) asserts that `copilot_state`
   contains exactly 12 tables plus `alembic_version`. Polluting that
   set with framework-managed tables would either break the test or
   weaken it into a less-precise superset assertion.

By giving LangGraph its own schema, both responsibilities stay clean:
Alembic owns `copilot_state` (our application schema), and
`AsyncPostgresSaver.setup()` owns `langgraph_checkpoint`'s tables.

### What's in the schema

After `AsyncPostgresSaver.setup()` runs (called once at API server
startup; idempotent), the schema contains four tables:

| Table | Purpose |
|---|---|
| `checkpoints` | One row per checkpoint write — thread state at a point in time. Indexed by `(thread_id, checkpoint_ns, checkpoint_id)`. |
| `checkpoint_blobs` | Serialized large values referenced from `checkpoints` rows. Storing them out-of-line keeps the main checkpoint rows small. |
| `checkpoint_writes` | Per-checkpoint write log, used by LangGraph for replay / resumption semantics. |
| `checkpoint_migrations` | The framework's internal version tracker (analogous to `copilot_state.alembic_version` but managed by `langgraph-checkpoint-postgres`, not by Alembic). |

The exact column layouts are intentionally not duplicated here — they
are framework-internal and may change between
`langgraph-checkpoint-postgres` versions. Treat them as opaque from
our application's perspective.

### Bootstrap pipeline

```
┌──────────────────────────────────────────────────────────────┐
│  1. Alembic migration 0003_langgraph_checkpoint_schema.py   │
│     CREATE SCHEMA IF NOT EXISTS langgraph_checkpoint        │
│     (idempotent; empty namespace)                            │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  2. API server startup (api/server.py lifespan)              │
│     init_checkpointer_pool() opens an AsyncConnectionPool   │
│     with options="-c search_path=langgraph_checkpoint,..."  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  3. AsyncPostgresSaver(pool).setup()                         │
│     CREATE TABLE IF NOT EXISTS for the 4 framework tables   │
│     inside langgraph_checkpoint (resolved via search_path)  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  4. WebSocket handler creates CopilotSession(                │
│        checkpointer_pool=get_checkpointer_pool()             │
│     )                                                        │
│     Each DomainAgentSession gets an AsyncPostgresSaver       │
│     bound to the shared pool — durable across restart.       │
└──────────────────────────────────────────────────────────────┘
```

### Two drivers, one Postgres

Two Postgres client libraries coexist in the runtime:

| Driver | Used by | Purpose |
|---|---|---|
| `psycopg2-binary` | SQLAlchemy engine in `database.database.get_db_engine` | Market-data ingestion + REST routes. Sync. |
| `psycopg[binary,pool]` (psycopg3) | `AsyncConnectionPool` for `AsyncPostgresSaver` | LangGraph checkpointer. Async. Required by `langgraph-checkpoint-postgres`. |

Both connect to the same Postgres instance; they just speak through
different abstraction layers. The SQLAlchemy ingestion path was built
against psycopg2 (PR 3's transactional refactor in particular relied on
that), and there's no value in churning it now. A future PR can
migrate the ingestion path to psycopg3 if there's an operational
reason.

### Thread-id convention

The orchestrator's `CopilotSession._child_thread_id(turn_label, domain)`
selects the LangGraph thread id per child, with two modes:

| `stateless` | `checkpointer_pool` | Thread-id format | Saver | Survives restart? |
|---|---|---|---|---|
| `True` (opt-in) | any | `{session_id}-{turn_label}-{domain}` | `MemorySaver` | no |
| `False` (default) | `None` | `{session_id}-{domain}` | `MemorySaver` | no (in-proc only) |
| `False` (default) | provided | `{session_id}-{domain}` | `AsyncPostgresSaver(pool)` | **YES** |

The stable thread-id format `{session_id}-{domain}` means a single
session keeps accumulating per-domain conversation history across
turns, which is what the deck describes as "the loop is the product."
Reconnecting after a server restart with the same `session_id` resumes
the conversation cleanly.

### Operational: how to wipe the checkpoint state

In dev, to start over with empty checkpoint state:

```sql
DROP SCHEMA langgraph_checkpoint CASCADE;
CREATE SCHEMA langgraph_checkpoint;
```

`AsyncPostgresSaver.setup()` will recreate the four tables on the next
API server startup. The Alembic migration is unaffected — it manages
only the schema namespace, not the tables inside it.

**Never** do this on a shared environment with real conversation
history.

---

## Artifact store (`state/` package) — Phase 0 PR 7

Phase 0 PR 7 ships the runtime surface for persisting and retrieving
typed `Artifact`s (`Series`, `SeriesSet`, `EventSet`, `Panel`,
`WindowedPanel`) via the `copilot_state.artifact_metadata` table from
PR 4. The code lives in `Macro_Copilot/state/`:

```
state/
├── __init__.py            # public API re-exports
├── schemas.py             # ArtifactSummary, ObjectStorageConfig, StoredArtifact
├── object_storage.py      # ObjectStorageBackend Protocol + LocalFS / GCS impls
├── artifact_store.py      # put / get / get_summary + serializers
└── gc.py                  # find_unreferenced + purge (implemented, not wired)
```

### Public API

```python
from state import (
    put_artifact, get_artifact, get_artifact_summary,
    LocalFSBackend, GCSBackend, build_backend,
    find_unreferenced_artifacts, purge_artifact,
)
```

- **`put_artifact(artifact, *, conn, object_storage) -> hash`** —
  Idempotent persistence.  Re-puts of the same hash short-circuit
  before any object-storage write.  Returns
  `artifact.lineage.head_hash` (the content-addressed identity from
  PR 2).

- **`get_artifact(hash, *, conn, object_storage) -> Artifact`** —
  Rehydrate the typed artifact.  Fast inline path for small
  artifacts; blob-fetch for large ones.

- **`get_artifact_summary(hash, *, conn) -> ArtifactSummary`** —
  Lightweight metadata-only view (units, frequency, row_count,
  byte_size, payload_uri, plus a bounded 16-point sparkline preview
  for inline artifacts).  Does NOT touch object storage — used by
  the Workspace renderer to populate per-node cards quickly.

### Inline-vs-blob decision

Small artifacts (default ≤ 100 rows AND ≤ 8KB serialized JSON) live
inline in `copilot_state.artifact_metadata.inline_payload` (JSONB).
Larger artifacts get written to the configured `ObjectStorageBackend`
and the `payload_uri` column points at the blob.  The PR 4 CHECK
constraint enforces "exactly one of `inline_payload` / `payload_uri`
is non-null"; PR 7's serializer respects it by design.

Both thresholds are configurable via env vars at runtime:

```
ARTIFACT_INLINE_ROW_LIMIT          (default: 100)
ARTIFACT_INLINE_SIZE_LIMIT_BYTES   (default: 8192)
```

The disjunction (blob if EITHER cap exceeded) biases toward smaller
Postgres rows.

### Serialization format

All artifacts serialize to the **same JSON shape** in both inline and
blob mode.  No Pickle, no Parquet — just JSON via Pydantic.  Reasons:

- **Cross-version durability.**  JSON is forward-compatible; the
  same bytes round-trip cleanly across Python / NumPy / Pandas
  versions in a way Pickle does not.
- **Inspectable.**  `jq` can read both inline payloads (via Postgres
  `SELECT inline_payload FROM ...`) and blob payloads (via `cat
  blob.bin | jq .`) without special tooling.
- **Inline / blob symmetry.**  The same serializer feeds both paths;
  `get_artifact` doesn't need branch-specific deserialization
  logic.

Pandas / NumPy types are converted to JSON-safe forms during
serialization (`DatetimeIndex` → ISO strings, `pd.Series` →
`{index, values}` dict, `NaN` → `None`, `np.ndarray` → nested lists).

### Object-storage backend selection

Configured via env vars at app startup; consumed by
`api/dependencies.init_object_storage`:

```
ARTIFACT_STORAGE_BACKEND      = 'localfs' (default) | 'gcs'
ARTIFACT_STORAGE_LOCAL_ROOT   = filesystem path (localfs only)
ARTIFACT_STORAGE_GCS_BUCKET   = bucket name (gcs only; required)
ARTIFACT_STORAGE_GCS_PREFIX   = key prefix (gcs only; default 'artifacts')
```

URI format is content-addressed and consistent across backends:

- LocalFS: `file://{root_abs}/{hash[:2]}/{hash[2:]}.bin`
- GCS:     `gs://{bucket}/{prefix}/{hash[:2]}/{hash[2:]}.bin`

The two-char fan-out gives reasonable per-directory entry counts at
100K+ artifacts.

### Garbage-collection sweep (implemented, NOT yet wired)

`state.gc` provides two functions:

- **`find_unreferenced_artifacts(conn, older_than_days=30, limit=1000)`**
  — returns hashes that no `dag_nodes.artifact_hash` or
  `working_set.artifact_hash` row references AND are older than the
  age gate.  The age gate is the safety belt against deleting an
  artifact that was just put but hasn't yet been wired into a
  working_set entry.
- **`purge_artifact(hash, *, conn, object_storage)`** — deletes the
  blob (if any) THEN the metadata row.  The FK RESTRICT constraints
  on `dag_nodes.artifact_hash` and `working_set.artifact_hash`
  provide the final safety check: if a concurrent transaction added
  a reference between the find and the purge, the metadata-row
  delete fails and the artifact survives (the blob is now orphaned
  but a future re-put produces the same URI by content-addressing).

**Not yet enabled in Phase 0.**  The Prefect worker service in
`docker-compose.yml` is the natural future host; Phase 4 (production
deployment) wires the sweep to a scheduled job.  The code is shipped
+ tested now because the reference-graph logic has non-trivial
correctness conditions that are cheaper to pin in tests today than
after a production incident.

### Orphan blobs

If `put_artifact`'s metadata-insert step fails after the
object-storage write succeeded, the blob is orphaned in storage.
These orphans are benign:

- Content-addressed — a future re-put of the same artifact yields
  the same URI and is a no-op at the metadata layer.
- The GC sweep's orphan-blob extension (future PR) will list blobs
  without matching metadata rows and remove them.  Not in Phase 0
  scope; orphans don't grow unboundedly because typical retry
  patterns re-put the same content.

### Two drivers, two pools, one Postgres

For completeness — Phase 0 now has three distinct Postgres access
layers, each with its own purpose:

| Driver | Pool | Purpose |
|---|---|---|
| `psycopg2-binary` | SQLAlchemy `Engine` | Market-data ingestion (PR 3 transactional refactor) + **artifact store metadata reads / writes (PR 7)** |
| `psycopg[binary,pool]` (psycopg3) | `AsyncConnectionPool` | LangGraph `AsyncPostgresSaver` (PR 5 + PR 5 follow-up) |

The artifact store deliberately uses the SQLAlchemy engine path
(psycopg2) rather than the checkpointer pool: the connection-injection
pattern from PR 3 is the right interface for transactional state
writes, and the artifact-metadata workload is sync.

---

## Working set + orchestrator state machine — Phase 0 PR 8

PR 8 lights up the conversation-state half of the schema. Three Phase 0
tables that PR 4 created but no code wrote to — `sessions`, `turns`,
`working_set` — become live, and a new substrate ties them to the
orchestrator's turn loop.

### Public API

Located in `state/working_set.py` and `orchestrator/state.py`:

```python
# state.working_set — per-session name <-> artifact_hash map
state.working_set.add(name, artifact_hash, introduced_at_turn,
                      *, session_id, conn) -> NamedArtifact
state.working_set.retire(name, retired_at_turn, *, session_id, conn) -> NamedArtifact
state.working_set.resolve(name, *, session_id, conn,
                          as_of_turn=None) -> NamedArtifact
state.working_set.list_visible(*, session_id, conn) -> list[NamedArtifact]

# orchestrator.state — turn lifecycle
orchestrator.state.create_session_if_needed(session_id, *, conn,
                                            user_id=None, label=None)
orchestrator.state.begin_turn(user_message, *, session_id, conn) -> TurnContext
orchestrator.state.commit_turn(turn_ctx, *, conn, status="completed",
                               assistant_response=None,
                               terminal_artifact_hash=None,
                               save_as=None) -> Optional[str]
orchestrator.state.fail_turn(turn_ctx, *, conn, error_message=None)
```

All ops take `conn` keyword-only (the PR 3 connection-injection
convention).  The caller owns the transaction.

### Turn lifecycle

```
WebSocket /api/chat connects
   │
   ▼
create_session_if_needed(new_uuid)        # copilot_state.sessions row
   │
   ▼  (per user message)
begin_turn(user_message)  ──▶  copilot_state.turns row (status='running')
   │                          sequence_no = max(...) + 1
   │
   ▼
reference resolver (LLM)  ──▶  ReferenceResolution(save_as, referenced_names)
   │                          — see orchestrator/reference_resolver.py
   │                          — visible names come from list_visible
   │
   ▼
supervisor route + domain children (existing flow, augmented with the
working-set block in the user message so the LLM sees current names)
   │
   ▼
commit_turn(status, assistant_response, save_as, terminal_artifact_hash)
   │                          ──▶  copilot_state.turns row updated to
   │                               status='completed' / 'failed' / 'cancelled'
   │                               + assistant_response stamped
   │
   ▼ (when terminal_artifact_hash is provided)
working_set.add("turn_<seq>_result", hash, turn_id)   # auto-named binding
working_set.add(save_as, hash, turn_id) if save_as    # user alias
```

### Append-mostly semantics of `working_set`

The `working_set` table is **append-mostly**: a rebind of an existing
name does NOT update the row in place.  It RETIRES the old row
(sets `retired_at_turn`) and inserts a new active row.  Two reasons:

1. **Historical resolution.**  `resolve(name, as_of_turn=earlier_turn)`
   recovers the binding that was active when an older turn ran.  The
   replay path uses this when reconstructing a workspace from DAG
   history.

2. **Foreign-key safety.**  `working_set.artifact_hash` is
   `ON DELETE RESTRICT` against `artifact_metadata.hash`.  Deleting
   a working-set row that holds the only reference to an artifact
   would let GC reclaim it; retiring keeps the reference intact.

The integrity invariant is the partial unique index
`uq_working_set_session_name_active` on `(session_id, name) WHERE
retired_at_turn IS NULL`: at most one ACTIVE binding per
(session, name).  The retirement transition in `add` is atomic
inside the caller's transaction — the old row's retire and the new
row's insert happen under the same `engine.begin()` block.

### Reference resolver

`orchestrator/reference_resolver.py` is a small structured-output
LLM call that runs ONCE per turn, BEFORE the supervisor.  Output:

```python
class ReferenceResolution(BaseModel):
    save_as: Optional[str]              # user explicitly said "save as X"
    referenced_names: List[str]         # names from the visible set
```

The resolver is deliberately separate from the supervisor:

- **Bounded scope.**  Two-field structured record; never prose,
  never numbers.  Tiny max_tokens; trivially testable.
- **Cache discipline.**  The supervisor's prompt should not grow
  knowledge of every session's working-set names.  Putting the
  per-session visible-names list in the resolver's user message
  keeps the supervisor's static prefix cache-stable.
- **Hallucination defence.**  The resolver is told "only return
  names from the VISIBLE WORKING-SET NAMES block" and the
  `_sanitize` step at the resolver layer drops anything the LLM
  fabricates anyway.

Failure modes (timeout / network / parsing error) return an empty
`ReferenceResolution()` — the turn proceeds as if the user made a
fresh query.  No turn ever fails because the resolver failed.

### Sequence-no monotonicity

`begin_turn` takes an explicit `FOR UPDATE` lock on the
`sessions` row before computing `sequence_no = max(...) + 1`.  This
serialises concurrent `begin_turn` calls for the same session; the
unique constraint `uq_turns_session_sequence` is the backstop, the
explicit lock is the happy path.  In production, each WebSocket
connection serialises turns at the application layer too, so this
is belt-and-suspenders.

### Degraded operation

The PR 8 wiring is **opt-in at the session level**:

- `CopilotSession(engine=None, session_id=None, ...)` — the test /
  CLI path; no DB writes, no resolver call.  Existing tests keep
  working unchanged.
- `CopilotSession(engine=eng, session_id=sid, ...)` — the
  WebSocket / production path; every turn writes a `turns` row,
  every save-as / reference goes through the resolver and the
  working set.

If the DB engine is unavailable at WebSocket-connect time, the
chat handler logs a warning and constructs a `CopilotSession`
WITHOUT the engine — degraded operation, same contract as
PR 5's checkpointer pool and PR 7's object-storage backend.  A
DB outage does not break the chat; it just means the turn doesn't
get persisted.

### Tests

Real-Postgres integration tests live under `tests/state/`:

- `test_working_set.py`           — add / retire / resolve / list_visible,
  including historical resolution and the partial-unique invariant.
- `test_turn_state_machine.py`    — begin_turn / commit_turn / fail_turn
  lifecycle and FK enforcement.
- `test_reference_resolver.py`    — sanitisation + LLM-stub resolver
  behaviour (no real model call; langchain stubbed via `sys.modules`).
- `test_multi_turn_reference.py`  — 3-turn end-to-end exercise of the
  working set + turns + resolver with a scripted resolver.

All four run in CI's `state-layer` job against the postgres:14
service container.

---

## Methodology version pinning — Phase 0 PR 9

PR 9 lights up the two registries PR 4 created but no code wrote to:
`methodology_versions` (one row per distinct YAML content snapshot)
and `application_version` (one row per distinct git commit).  Every
`put_artifact` now records BOTH, and a new
`/api/v1/workspace/{artifact_hash}` route surfaces the captured
provenance.

### Migration 0004

Adds `artifact_metadata.application_version_id BIGINT NULL` FK to
`application_version.id` (ON DELETE RESTRICT) with a partial index
on the NOT-NULL rows.  Nullable because PR 7 / PR 8 wrote rows
before PR 9 — the column is best-effort backwards-compatible, not
mass-backfilled.

### Public API

```python
# state.methodology_versions — both registries + canonicalisation
register_yaml(yaml_text, *, yaml_path, conn) -> int
get_yaml(version_id, *, conn) -> MethodologyVersionRecord
get_yaml_by_hash(content_hash, *, conn) -> Optional[MethodologyVersionRecord]
register_application_version(git_commit, *, conn, notes=None) -> int
current_application_version_id(*, conn) -> int
get_application_version(version_id, *, conn) -> ApplicationVersionRecord
canonicalize_yaml_content(parsed_dict) -> Dict[str, Any]
clear_caches()  # tests only

# shared.config.tool_config — version-stamp on load
load_tool_config(path, *, conn=None) -> ToolConfig
# ToolConfig now carries Optional[int] methodology_version_id

# shared.artifacts.lineage — pointer in the lineage chain
class PrimitiveStep:
    methodology_version_id: Optional[int] = None  # NOT in hash
```

### Hash-stability invariant

`PrimitiveStep.methodology_version_id` is metadata-only and **NOT**
folded into the canonical hash recipe.  The YAML content it points
at is already captured in `tool_config_hash`; adding the registry
id would couple the hash to per-DB auto-increment values (which
are not stable across deploys / restores) and break PR 2's pinned-
hash test.

Concretely:
- Building two `PrimitiveStep`s that differ ONLY in
  `methodology_version_id` produces the SAME hash.
- Building two `PrimitiveStep`s with different `tool_config_hash`
  produces DIFFERENT hashes — the YAML content drives identity.

The test `tests/state/test_hash_stability.py` is the CI gate; it
must continue to pass for the canonical hash recipe to be stable.

### YAML content canonicalisation

`register_yaml` does NOT hash the raw YAML text.  Whitespace, key
order, and comment formatting are NOT semantically meaningful, so:

1. `yaml.safe_load(text)` → parsed dict.
2. `shared.artifacts.lineage._canonicalize_for_hash(parsed)` —
   same canonicaliser the lineage layer uses for step hashes
   (recipe lives in ONE place).
3. SHA-256 the canonical JSON → `yaml_content_hash`.

Consequences:
- Two YAMLs that differ only in cosmetic ways register as the SAME
  row.
- A real semantic edit (e.g. `z_score_window_days: 252 -> 200`)
  registers as a NEW row.
- Stored `yaml_content` is the canonicalised dict — round-trips
  losslessly into `ToolConfig` via `ToolConfig.model_validate(...)`
  (verified per-YAML in `test_tool_config_round_trip.py`).

### Application-version resolution

`current_application_version_id` resolves the current process's git
SHA in this order:

1. `MACRO_COPILOT_GIT_COMMIT` env var (container / CI bake-in).
2. `git rev-parse HEAD` (local dev + CI checkouts).
3. `_UNKNOWN_COMMIT_SENTINEL` (40 'u' chars) — falls back when
   the process is running outside a git checkout.  Satisfies the
   `length(git_commit) = 40` CHECK so the registry still records
   _something_.

Resolution is process-cached: a long-running server does one
`git rev-parse` ever.

### Cache-staleness self-healing

`state.artifact_store._resolve_application_version_id` SELECTs the
cached id from `application_version` before stamping it on a new
artifact.  If the row was deleted out-of-band (a test fixture
wiped the table after the cache populated), the helper clears
caches and re-resolves once.  Steady-state cost stays zero
round-trips; rare-event cost is one round-trip + a retry.

### Workspace replay route

`GET /api/v1/workspace/{artifact_hash}?mode={original|current}`
returns a `WorkspaceReplayResponse`:

```json
{
  "artifact_hash": "...",
  "mode": "original" | "current",
  "produced_under_commit": "abc1234..." | null,
  "current_commit": "def5678...",
  "commit_differs": false,
  "methodology_version_ids": [1, 4],
  "methodology_diffs": [...],     // only in mode=current
  "reconstructed": [...],         // only in mode=original
  "notes": [...]
}
```

- `mode=original` (default): for each pinned methodology version,
  load the registry-stored YAML, validate it round-trips through
  `ToolConfig`, return the reconstructed config's identity hash.
  Replay-faithful: subsequent on-disk YAML edits cannot taint the
  response.
- `mode=current`: for each pinned methodology version, compare its
  stored content against what the same on-disk path holds NOW.
  Returns `methodology_diffs` listing every changed
  `conventions.<key>` (best-effort top-level diff).

Out of scope for PR 9: actual re-execution of primitives.  The
route surfaces the divergence signal; the executor that closes the
loop ("replay would produce this artifact under current YAML")
belongs to the workspace-persistence PR (Phase 0 PR 11).

### Tests

Real-Postgres integration tests under `tests/state/`:

- `test_methodology_versions.py`     — registry idempotency,
  canonicalisation invariance, SHA validation, current-commit
  resolution.
- `test_tool_config_round_trip.py`   — parameterised across all 16
  current tool YAMLs.  Asserts `ToolConfig` round-trips through
  both Pydantic AND the registry's JSONB storage without losing
  `conventions_hash`.
- `test_methodology_pinning.py`      — the brief's spec test:
  build artifact under YAML v1, mutate to v2, assert hash
  divergence + faithful original-mode reconstruction.  Plus
  TestClient coverage of the replay route.

All three run in CI's `state-layer` job alongside the PR 4 / 5 /
7 / 8 tests, against the same postgres:14 service container.
