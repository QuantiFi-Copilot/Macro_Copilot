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
2. Installs Alembic + psycopg2 + SQLAlchemy.
3. Runs `alembic upgrade head` → `alembic downgrade base` →
   `alembic upgrade head` (the idempotence pass) against the service.
4. Runs `pytest Macro_Copilot/tests/state/test_migrations.py`, which
   re-runs the round trip plus the schema-invariant spot checks (CHECK
   constraints present, `schema_version` default, alembic_version in
   the right schema, etc.).

A migration that fails any step does not merge.
