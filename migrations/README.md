# Alembic migrations

This directory owns the `copilot_state` Postgres schema that backs the Phase 0
state foundation (sessions, turns, artifacts, DAGs, workspaces, …).

The other Postgres schema in the project — `macro_data` — is **not** managed by
Alembic. It is hand-written SQL in `Macro_Copilot/database/schema.sql` and
bootstrapped via the Docker entrypoint-initdb mount in `docker-compose.yml`.
That separation is intentional: `macro_data` is stable market-data tables
(`instrument_master`, `load_audit`, `market_data_daily`) that rarely change;
`copilot_state` is the evolving substrate where Alembic earns its keep.

The full design rationale for the `copilot_state` schema lives in
[`docs/architecture/state_schema.md`](../Macro_Copilot/docs/architecture/state_schema.md).
This file is the operational guide.

---

## Quickstart

```bash
# from the worktree root, with the conda env active (or pip-installed dev deps)
pip install -e ".[dev]"

# point at a database; defaults match database.database.get_db_engine
export DB_USER=quantuser
export DB_PASSWORD=myStrongPass
export DB_HOST=localhost
export DB_PORT=5433
export DB_NAME=macrodata

# apply all pending migrations
alembic upgrade head

# undo everything alembic created
alembic downgrade base

# create a new revision (hand-written; we do not use --autogenerate yet)
alembic revision -m "describe what this migration does"

# print the current applied head
alembic current

# print full revision history
alembic history --verbose
```

Connection-string convention matches `database.database.get_db_engine`. If your
dev DB is already configured for the existing pipeline, migrations work without
extra setup.

---

## File layout

```
migrations/
├── env.py                                      # Alembic environment
├── script.py.mako                              # template for new revisions
├── README.md                                   # this file
└── versions/
    ├── 0001_initial_copilot_state.py           # creates the 12 tables
    └── 0002_noop_sentinel.py                   # no-op for round-trip tests
```

`alembic.ini` lives at the **worktree root**, not inside this directory.

---

## Schema versioning policy

Two distinct version concepts:

| Concept | Where it lives | Owns |
|---|---|---|
| **DB schema version** | `copilot_state.alembic_version` (managed by Alembic) | Which DDL migrations have been applied. |
| **Workspace data format version** | `copilot_state.workspaces.schema_version` (integer column) | The shape of workspace data objects — slot bindings, override summaries, lineage chain layout. Distinct from DDL because the same DDL can carry V1 and V2 workspace data side-by-side. |

Readers must refuse to read a workspace whose `schema_version` is ahead of the
reader's code. Rolling forward is a **data migration** (a coordinated app
release that knows how to read both old and new shapes), not a **DDL
migration** (Alembic).

---

## Writing a new migration

1. **Run** `alembic revision -m "<concise description>"` from the worktree root.
   This generates a new file under `versions/` with a UUID-suffix revision ID
   that automatically chains to the current head.

2. **Rename** the generated file to use a 4-digit prefix matching its position
   in the chain (e.g. `0003_add_workspace_lineage_cache.py`). The prefix is
   for human readability of the directory listing — Alembic itself uses the
   `revision` string inside the file as the identity.

3. **Write** `upgrade()` and `downgrade()` using `op.create_table` /
   `op.drop_table` / `op.add_column` / etc. Hand-write both directions — we
   do **not** use `--autogenerate` because we do not maintain a SQLAlchemy
   model `MetaData` at the schema level yet. Autogenerate becomes useful
   once Phase 0 introduces typed state-layer models; until then, raw `op`
   calls are the discipline.

4. **Always** include `schema=SCHEMA` (= `"copilot_state"`) on every
   `op.create_table` / `op.drop_table` / `op.create_index`. Forgetting it
   creates the object in the default `public` schema and produces silent
   schema split.

5. **Test the round trip locally** against a temporary Postgres:

   ```bash
   alembic upgrade head        # forward to your new revision
   alembic downgrade -1        # back one step (run YOUR downgrade)
   alembic upgrade head        # forward again (run YOUR upgrade again — idempotent)
   ```

   The `tests/state/test_migrations.py` suite enforces this on every PR via
   the CI `migrations` job. A migration that does not survive its own
   round-trip test does not merge.

6. **Update** `docs/architecture/state_schema.md` if the change is
   observable at the API/UX layer (new table, dropped column, changed
   constraint that callers rely on).

---

## Conflict-resolution etiquette

Two PRs that both add a migration at the same head will conflict on
`down_revision`. Resolution rules:

- **Linear is preferred.** If both PRs are still open, rebase one on top of
  the other so the chain stays linear. Change the later PR's `down_revision`
  to point at the earlier PR's revision ID, and renumber its 4-digit prefix.
- **Branches are last resort.** Alembic supports branch labels and merging,
  but the operational complexity (especially for the `alembic upgrade head`
  semantics) is rarely worth it for this project's scale.
- **Never edit a merged migration.** Once a migration has been applied to any
  shared environment (CI, staging, prod), its `revision` string and its
  `upgrade` body are immutable. To fix a mistake, add a NEW migration that
  corrects it forward.

---

## Reverting Alembic to a clean slate (dev only)

If you need to nuke the entire `copilot_state` schema and start over:

```bash
psql "$DB_URL" -c "DROP SCHEMA IF EXISTS copilot_state CASCADE;"
alembic upgrade head
```

The `DROP SCHEMA … CASCADE` removes the `alembic_version` table along with
every migration's worth of tables. Re-running `upgrade head` then bootstraps
the schema from scratch.

**Never** do this on a shared environment with real data.
