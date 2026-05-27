# ADR 0015 — Tool metadata DB table

**Status:** accepted (Phase 0 of the revamp branch)
**Date:** 2026-05-26
**Operationalises:** [`../03_standards/tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §3 (where each tool truth lives).
**Affects:** `database/schema.sql`, every tool's metadata source, the frontend Library + chart rendering path.

---

## Context

The lifecycle standard ([`tool_lifecycle.md`](../03_standards/tool_lifecycle.md)) requires every tool to expose seven axes of truth (theoretical reference, methodology, testing, input/output contracts, known limitations, frontend surfaces, user-facing copy).  Some of those truths are STATIC and CONSULTED OFTEN by multiple consumers — units of each output field, the institutional textbook reference, the desk-narrative description, the known-limitations list.

Today these truths are scattered across:

- **Backend Python** — `_PRIMITIVE_SPECS[tool_name].output_field_units` (units per output field)
- **YAML manifesto** — `manifesto/03_tool_manifest/<domain>_manifest.yml` (`references:`, `description:`, `validation_status:`)
- **Tool config** — `rates_agent/<domain>/tools/<tool>/config.yaml` (`conventions:` block)
- **Frontend module** — `src/modules/primitives/<tool_name>/module.ts` (`displayName`, `oneLineSummary`, `category`)
- **Tool docstrings** — inside Pydantic Input/Output classes

The same fact (e.g. "the OIS curve-spread output is in basis points") is duplicated across YAML, Python doc-strings, and frontend module specs.  Updating one without the others creates silent drift.  Adding a new field (e.g. `theoretical_reference`) means touching every tool's YAML + writing parsers — brittle and verbose.

## Decision

**Static, queried-often tool metadata moves to a dedicated database table `macro_data.tool_metadata`.**  YAML / config / docstrings continue to exist but become **mirrors** of the DB — the DB is the authoritative source.

The decision applies to:

- **Output field units** (multi-consumer: frontend chart rendering, backend operator validation, lineage system)
- **Theoretical reference** (Library page, per-tool README)
- **Known limitations** (Library page, per-tool README)
- **Desk narrative** (Library page, manifesto mirror)
- **Domain** (cross-cutting queries)
- **Category** (frontend grouping, Library filters)

The decision does NOT apply to:

- **Methodology conventions** (z-score windows, day-count rules, threshold values) — these stay in `config.yaml` because they are USER-OVERRIDABLE per [`tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §1.  The user reconstructs any prior methodology by editing the config; the DB doesn't track methodology history.
- **Status flags** that change frequently — `validation_status`, `built_date`, etc. continue to live in `manifesto/.../*.yml` because they are state, not metadata.
- **Frontend module display fields** — `displayName`, `oneLineSummary`, `category` — these stay in the frontend module spec because the frontend has its own load path; they are FACTS the frontend owns.  (Could be unified later if drift becomes a problem.)
- **Pydantic Input/Output schemas** — these are executable contracts, not metadata; they stay in Python.

## Schema

```sql
CREATE TABLE IF NOT EXISTS macro_data.tool_metadata (
    tool_name           VARCHAR(255) PRIMARY KEY,

    -- Static facts (mechanically derivable; populated by the seed script).
    domain              VARCHAR(64)  NOT NULL,
    category            VARCHAR(64),
    output_field_units  JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- Human-curated facts (NULL until per-tool Phase 1 work).
    theoretical_reference  TEXT,
    known_limitations      TEXT,
    desk_narrative         TEXT,
    source_material_verified  JSONB,  -- {verifier: str, date: ISO date, source: str}

    -- Audit columns.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_metadata_domain
    ON macro_data.tool_metadata (domain);
```

**Field rationale:**

| Field | Why this shape |
|---|---|
| `tool_name` (PK) | Backend-canonical name (e.g. `calculate_curve_spread_tool`).  Matches `_PRIMITIVE_SPECS` keys + `MODULE.toolName` in the frontend.  Already the de facto identifier. |
| `domain` | One of `sovereign_bonds`, `ois`, `inflation_indexed_bonds`, `inflation_swaps`, `policy_futures`, `bond_futures`.  Static — never changes for a given tool.  Cross-cutting queries (e.g. "all OIS tools") become a single index lookup. |
| `category` | Manifesto's `bucket` / `category` field (e.g. `curve_shape`, `yield_level`, `scanner`).  Drives Library page grouping. |
| `output_field_units` (JSONB) | `{ "time_series": "bps", "time_series_zscore": "z_score", ... }`.  Mirror of `PrimitiveSpec.output_field_units` in `rates_agent/workflows/__init__.py`.  Frontend chart renderers read this to label axes / format ticks. |
| `theoretical_reference` (TEXT) | Free-form citation (e.g. `"Tuckman 4e Ch. 5 §5.3 (pp 117-124) — Yield curves, principal components, and z-score windows"`).  Human-curated per tool; Phase 1+. |
| `known_limitations` (TEXT) | Free-form prose describing edge cases / dependencies / things the tool can NOT do.  Documented limitations only (no speculative failure-mode forecasting per [`tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §2). |
| `desk_narrative` (TEXT) | The "what macro question this answers + why a PM cares" framing.  Source for Library page user copy. |
| `source_material_verified` (JSONB) | `{verifier: "name", date: "2026-XX-XX", source: "Tuckman 4e Ch. 5"}`.  NULL until a human reviews the methodology against the theoretical reference.  Status flag, not state. |
| `created_at` / `updated_at` | Standard audit pair. |

## Architectural alternatives considered

### A. Keep everything in YAML (status quo)
- **Pro:** No new infra, no migration.
- **Con:** Drift across YAML / Python / frontend already happens.  Adding fields means editing every tool's YAML + writing parsers in every consumer.  Doesn't scale.

### B. Centralised YAML file (one file, all tools)
- **Pro:** Single source of truth; no new DB.
- **Con:** 58 entries × N fields = 500-line YAML that needs a custom loader.  No transactional safety on edits.  No query interface beyond grep.  Doesn't compose with the existing manifesto.

### C. Per-tool YAML enriched, frontend reads via API
- **Pro:** Incremental on the existing manifesto.
- **Con:** Still duplicates between manifesto YAML + tool config YAML + frontend module + Pydantic.  The "where does this fact live?" question doesn't resolve.

### D. DB table (this ADR)
- **Pro:** Single source of truth.  Transactional.  Queryable.  Each consumer reads from one place.  Migration model is standard (`database/migrations/*.sql`).  Fits the existing tsdb infrastructure.
- **Con:** New table to maintain.  Initial population required.  Requires running the migration against any live DB.

**Chose D** because the cost is small (single table, ~50 rows initially) and the consistency win is large (no more drift between 4+ sources).

## Migration + population strategy

Two-file migration matching the existing convention (`database/migrations/2026-05-20_a3_b1_schema_sync.sql`):

1. **`database/migrations/2026-05-26_phase0_tool_metadata_table.sql`** — creates the table on any running DB.  Idempotent (`CREATE TABLE IF NOT EXISTS`).  Apply via:
   ```
   docker exec -i macro-tsdb psql -U quantuser -d macrodata \
       < database/migrations/2026-05-26_phase0_tool_metadata_table.sql
   ```

2. **`database/populate_tool_metadata.py`** — Python script that:
   - Reads `_PRIMITIVE_SPECS` from `rates_agent.workflows`.
   - Reads `manifesto/03_tool_manifest/rates_agent/*.yml` for `category`.
   - Generates `INSERT ... ON CONFLICT (tool_name) DO UPDATE` statements for each of the 58 tools.
   - Populates ONLY mechanically-derivable fields: `tool_name`, `domain`, `category`, `output_field_units`.
   - The human-curated fields (`theoretical_reference`, `known_limitations`, `desk_narrative`, `source_material_verified`) stay NULL — they are filled per tool in Phase 1.

The script is re-runnable: on subsequent runs, `ON CONFLICT DO UPDATE` updates the mechanical fields if they've drifted, and leaves the human-curated NULLs / values untouched.

**Schema.sql also gets the table definition** so a fresh DB initialisation (empty volume) produces a DB that already has the table.  This matches the existing schema-vs-migration pattern documented in `database/migrations/2026-05-20_a3_b1_schema_sync.sql`'s header.

## Consumers (built in later phases, not Phase 0)

This ADR establishes the table.  Consumer wiring is deferred:

- **Phase 1 (pilot tools)** — first consumer.  The pilot tool's frontend reads units from `tool_metadata.output_field_units` to label its chart correctly; the Library page reads `desk_narrative` to render user-facing copy.
- **Phase 2 (all new tools)** — every new-tool PR populates its row in the same PR that ships the code.
- **Phase 3 (retroactive)** — when a tool gets touched, its row's human-curated fields get filled.

Backend / API / frontend reading code is out of scope for Phase 0.  Phase 0 lands the table + the mechanical population only.  The pilot tools in Phase 1 will be the first consumers and will drive any API endpoint design at that point.

## Implications + open questions

### Implications
1. **The lifecycle standard's "Source of truth"  column** ([`tool_lifecycle.md`](../03_standards/tool_lifecycle.md) §3) refers to this table for the listed fields.  YAML mirrors stay for human readability.
2. **The surface contract** ([`../02_components/surface_contract.md`](../02_components/surface_contract.md)) gets new columns reflecting lifecycle-axis status — including `source_material_verified` (read from DB) and `methodology_doc` (config.yaml presence).
3. **Frontend Library page becomes DB-backed** for the user-facing fields.  In Phase 2 a `/api/v1/tools/{name}/metadata` endpoint surfaces the rows.
4. **Operator chains can validate unit compatibility** at runtime by reading `output_field_units` of upstream tools.  Today this is implicit; with the table it becomes a real check.

### Open questions (track + revisit)
- **Should methodology conventions also move to DB?**  Currently NO — they're user-overridable, fundamentally configurable.  Revisit if drift between config.yaml and the actual Python execution path becomes a problem.
- **Should frontend `displayName` / `oneLineSummary` move to DB?**  Currently NO — frontend owns its own load path.  Revisit if multi-frontend or multi-locale requirements appear.
- **Should there be a `tool_metadata_history` table for audit?**  Currently NO — `updated_at` is sufficient for V1; full history (who changed `desk_narrative` when) is V2 hardening.

## Acceptance

Phase 0 acceptance for this ADR:

- [x] Table definition added to `database/schema.sql`.
- [x] Migration file under `database/migrations/` follows the existing `YYYY-MM-DD_<ticket>_<description>.sql` naming + header conventions.
- [x] Migration is idempotent (re-runnable).
- [x] Population script generates correct UPSERT statements; re-runnable.
- [x] `tool_lifecycle.md` references this ADR as the source for §3's DB-truth column.
- [x] `surface_contract.md` extension references this ADR for the new lifecycle status columns.
- [x] No consumer wiring (backend API endpoint / frontend hook) — those land in Phase 1+ when pilot tools need them.

Future phases (NOT this ADR's acceptance):

- Phase 1 — pilot tools fill their human-curated fields; first frontend / Library consumer ships.
- Phase 2 — every new tool PR populates its row in the same PR as the code.
- Phase 3 — opportunistic retroactive population as tools get touched.
