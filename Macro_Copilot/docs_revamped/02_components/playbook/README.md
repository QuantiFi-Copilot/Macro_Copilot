# Playbook

> The declarative YAML spec that owns an agent's data universe — what instruments exist, which vendor fields to pull for them, how often, and how the ingestion pipeline must handle the result. **Agent-agnostic by design**: the contract on this page holds whether the agent is rates, FX, credit, equity, commodities, options, or any future addition.

**Version:** v1
**Last reviewed:** 2026-05-17
**Status:** load-bearing component contract. Changes require an ADR in [`../../07_decisions/`](../../07_decisions/).
**Operationalises principles:** P1 (future-proofed), P3 (consistency by contract), P4 (determinism), P5 (honest disclosure), P6 (no silent failure), P7 (vendor SDK isolation), P10 (single source of truth), P11 (domain isolation by agent).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new playbook.

---

## What a playbook is

A playbook is a **single YAML file** that declares everything the data substrate needs to know about one cohesive group of instruments belonging to one agent:

- **Which instruments** exist in the universe (`universe`).
- **Which vendor fields** to pull for each instrument (`target_metrics`, `reference_metrics`).
- **From when** to start pulling history, and **how wide** an incremental window to refresh (`extraction`).
- **Static identity** of the playbook itself (`playbook_name`, `playbook_version`, `asset_class`, `dataset_name`).

A playbook is **declarative**. It contains no Python, no inline code, no computed fields, and no methodology choices. It is consumed by:

1. An **extractor** that turns the playbook into Parquet files downloaded from the source-of-record vendor.
2. An **ingester** that turns those Parquet files into rows in `macro_data.instrument_master` + `macro_data.market_data_daily`, with provenance in `macro_data.load_audit`.

Both the extractor and the ingester are vendor-aware (they live below L1 — see P7); the playbook itself is **vendor-shape-aware** only in the sense that it names vendor fields by their canonical mnemonics. Swapping the source-of-record (Bloomberg today, an internal data lake or another vendor tomorrow) requires changes only in the extractor and the L1 adapter, never in the playbook structure.

## What every playbook commits to

Four guarantees. These are the contract the rest of the platform relies on; violating any of them is a substrate bug, not a "config choice."

| # | Guarantee | What it means | Where enforced |
|---|---|---|---|
| 1 | **Declarative.** | The playbook is pure data. No conditional logic, no inline computation, no field that requires interpretation beyond a vendor mnemonic. | The YAML loader rejects unexpected node types. |
| 2 | **Atomic ingestion.** | A successful run inserts every row of the load *or* zero rows. There is no partial state. Delete-then-upsert-then-audit-flip happens inside one Postgres transaction. | The ingester's `engine.begin()` block wraps delete + upsert + audit status update as one unit. |
| 3 | **Idempotent.** | Re-running the same playbook against the same vendor snapshot produces the same database state — no duplicate rows, no spurious new `load_audit` records. Determined by SHA-256 over a canonicalised content view of the Parquet (excluding lineage stamps like `extracted_at`, `git_commit_hash`). | The dedup gate skips ingestion when the incoming `source_file_hash` matches the previous successful load's hash. Drives P4 at the data layer. |
| 4 | **Coverage-gated.** | If the incoming extraction returns fewer than **80% of the previous successful load's instrument count**, the load aborts, the audit row goes to `FAILED`, and the prior data is left untouched. A partial Bloomberg pull cannot wipe out good history. | The ingester's coverage check runs *before* the delete branch of the atomic transaction. |

These four guarantees are agent-agnostic. They apply to every playbook the same way, regardless of asset class.

## The universal contract

Every playbook MUST have these top-level keys. Names are case-sensitive and stable across asset classes:

```yaml
playbook_name: <string>           # unique slug; matches the filename stem
playbook_version: <semver>        # bumped on any structural or universe change
asset_class: <string>             # e.g., "rates"; future: "fx", "credit", "equities", "commodities"
dataset_name: <string>            # the canonical name surfaced in load_audit and lineage cards
description: <string>             # one-paragraph human narrative

extraction:
  start_date: <ISO-8601 date>     # historical start; the extractor's earliest pull date
  incremental_window_days: <int>  # how wide a re-pull window for the incremental refresh path

target_metrics:                   # the time-series fields to ingest, per instrument, per day
  - metric_id: <string>           # internal canonical name (e.g., "yield_mid", "last_price")
    bloomberg_field: <string>     # vendor mnemonic; renamed per-vendor when a second adapter ships

reference_metrics:                # static / one-shot fields stamped onto the instrument row
  - column_name: <string>         # internal column name
    bloomberg_field: <string>     # vendor mnemonic

universe:                         # list of instrument declarations — see "the universe block" below
  - ticker: <string>
    instrument_type: <string>
    # ... asset-class-appropriate metadata fields ...
```

### The top-level fields, one by one

- **`playbook_name`** — unique identifier across all agents. Lowercase snake_case. Matches the filename stem (so `sovereign_bonds.yml` has `playbook_name: sovereign_bonds`). A new playbook chooses a new name; renaming an existing playbook breaks `load_audit` continuity and is therefore a closed-family-style decision (file an ADR).
- **`playbook_version`** — semver. Bumped whenever the YAML changes in any way that affects what gets ingested: universe rows added/removed, fields added/removed, `extraction` window changed. Cosmetic edits (whitespace, key reorder, comment changes) do not bump the version because the playbook hash canonicalises them out.
- **`asset_class`** — the discriminator. Today: `rates`. Future: `fx`, `credit`, `equities`, `commodities`, `options`, `crypto`, etc. The value is stamped into `instrument_master.asset_class` and is queryable. Adding a new asset-class value is a closed-family-style decision (P8) — file an ADR.
- **`dataset_name`** — the human-friendly grouping that appears in lineage cards, replay UIs, and `load_audit`. Often equal to `playbook_name`, but it can differ when one playbook covers multiple economically-related groups (e.g., a `vol_surfaces` dataset might span equity, FX, and rates vol playbooks at the dataset name level).
- **`description`** — one paragraph. Describes what the playbook covers and why. Read by humans browsing the catalogue; not parsed by any downstream tool.

### The `extraction` block

Controls the two ingestion modes:

- **`start_date`** — the earliest date the extractor pulls when running in historical mode. Lower bound on the dataset's coverage. Changing this requires a full re-extraction (the existing rows are not back-filled automatically).
- **`incremental_window_days`** — when running in incremental mode, the extractor pulls the last *N* days. The ingester's atomic transaction deletes only the rows within that window and re-inserts them. History outside the window is preserved.

The choice between historical and incremental is **operational** (per-run, set by the operator or the scheduler), not declared in the playbook. The playbook only declares the window *size* for incremental runs.

### The `target_metrics` and `reference_metrics` blocks

These are two distinct kinds of vendor field:

- **`target_metrics`** are **time-series** fields — one value per instrument, per day. They land as rows in `macro_data.market_data_daily` keyed by `(trade_date, instrument_id, field_name)`. Each `metric_id` corresponds to one `field_name` in that table. Examples: a yield, a price, an open-interest count.
- **`reference_metrics`** are **static** fields — one value per instrument, stamped once on `instrument_master` (either into a typed column or into the `attributes` JSONB). They do not change daily, and the ingester does not re-row them per day. Examples: maturity date, coupon, settlement convention, contract size.

The split exists because the two have very different storage costs (time-series scales linearly with days; reference fields do not) and very different query patterns. Both lists can be empty in a degenerate case; in practice every playbook has at least one target metric.

**Vendor mnemonic naming.** Today the field is named `bloomberg_field` because Bloomberg is the source of record. When a second adapter ships, the per-vendor name will be added alongside (`internal_lake_field`, `xyz_vendor_field`, etc.) without renaming the existing one — the existing tag stays accurate for whichever rows were ingested through that adapter. P7 isolation means the rest of the platform never reads these mnemonic strings directly; primitives read by `metric_id` or by `field_name` against the enriched view.

### The `universe` block — where asset classes differ

`universe` is a list. Each row declares one instrument. Every row MUST carry these fields:

- **`ticker`** — the vendor identifier (Bloomberg ticker today). The natural key.
- **`instrument_type`** — a coarse category the substrate uses for filtering. Asset-class-specific values; examples: `sovereign_benchmark`, `ois_swap`, `bond_future`, `inflation_indexed_bond`, `policy_future` for rates today. For FX it would be `spot`, `forward`, `vol_surface`, etc.

Beyond these two, **the per-row schema is asset-class-specific by design.** This is the universal contract's only domain-shaped concession. The platform expects every asset class to declare its own consistent per-row taxonomy, and that taxonomy lands in `instrument_master` either as typed columns (when frequently queried) or in the `attributes` JSONB (when domain-specific).

**Strongly conventional fields** — present in most playbooks, encouraged across asset classes when they apply:

| Field | Meaning | Typical values |
|---|---|---|
| `country` | Country of the issuer / underlying / pair anchor | `"US"`, `"Germany"`, `"Japan"`, … |
| `currency` | Settlement currency | `"USD"`, `"EUR"`, `"JPY"`, … |
| `tenor` | A canonical tenor label when applicable | `"2Y"`, `"5Y"`, `"10Y"`, `"3M"`, … |
| A **family** field | The platform's primary taxonomy axis within an asset class | rates uses `curve_family` (`UST`, `EUR_OIS`, `JPY_TIPS`, …); FX would likely use `pair_family`; equities might use `index_membership` or `sector_taxonomy` |

The family field is the most important per-asset-class design decision. It is what primitives filter by when they ask *"give me everything in the X family."* A new asset class designing its universe must pick a family axis up front and apply it consistently across every row.

**Domain-specific fields** are anything else the asset class needs:

- **Rates examples** (existing in code):
  - OIS playbook: swap leg conventions (`fixed_leg_day_count`, `fixed_leg_pay_frequency`, `float_leg_*`, etc.).
  - Bond futures playbook: `contract_code`, `bucket_label`, `is_rolling_contract`.
  - Policy / STIR futures playbook: `inverse_pricing`, `strip_position`.
  - Inflation playbooks: `pricing_type`, `inflation_index_family`, `index_lag`, `interpolation`.

- **Future-asset-class examples** (illustrative, not yet in code):
  - Equities: `exchange`, `sector`, `industry`, `index_membership`, `market_cap_band`, `adv_30d_band`.
  - FX: `pair`, `bid_ask_convention`, `settlement_days`, `is_em`.
  - Credit: `cusip` / `isin`, `issuer`, `coupon`, `maturity_date`, `sector`, `seniority`, `rating_band`.
  - Options: `underlying`, `strike`, `expiry`, `option_type`, `multiplier`, `exchange`.
  - Individual bonds (OTR-aware): `cusip`, `coupon`, `maturity_date`, `issue_date`, `outstanding`, `is_on_the_run`.

There is **no enumerated whitelist** of allowed per-row fields. The instrument_master schema has a `JSONB attributes` column that absorbs any field not mapped to a typed column. Domain-specific fields that are not query-hot should land there. Fields that *are* query-hot for an asset class should be promoted to typed columns via a schema migration (P8-flavoured decision; file an ADR).

## How playbook data lands in the database

Brief operational picture; the full schema reference lives in `01_architecture/01_l1_data_substrate.md` (forthcoming).

```
playbook.yml
    │
    ▼  (extractor — vendor-aware, lives in utils/)
Parquet file on object storage  ─── playbook_hash + source_file_hash + git_commit_hash + extracted_at
    │
    ▼  (ingester — atomic transaction)
┌─────────────────────────────────────────────────────────────────────┐
│  load_audit  ←  status = RUNNING                                    │
│                                                                     │
│  Coverage gate: count(incoming instruments) ≥ 0.8 × count(prior)?  │
│       │                                                             │
│       ├─ no  →  status = FAILED, no data changes, exit              │
│       │                                                             │
│       └─ yes →  begin transaction                                   │
│                  ├── upsert instrument_master rows                  │
│                  ├── delete prior market_data_daily rows             │
│                  │   (full scope, or window only)                    │
│                  ├── upsert market_data_daily rows                  │
│                  └── status = SUCCESS                                │
│                  commit                                              │
└─────────────────────────────────────────────────────────────────────┘
    │
    ▼
Enriched view:  v_market_data_daily_enriched  (instrument_master ⋈ market_data_daily)
    │
    ▼
Primitives query the enriched view, filtering by the family field
```

### The three tables (brief)

- **`macro_data.instrument_master`** — one row per unique `(vendor, vendor_ticker)`. Carries the typed-column metadata (asset_class, instrument_type, family, country, currency, tenor, …) plus the `attributes` JSONB. Primary key: `instrument_id` (BIGSERIAL).
- **`macro_data.market_data_daily`** — one row per `(trade_date, instrument_id, field_name)`. Long-format; `field_name` is the metric (e.g., `YLD_YTM_MID`, `PX_LAST`). TimescaleDB hypertable partitioned on `trade_date`.
- **`macro_data.load_audit`** — one row per ingestion run. Tracks playbook hash, content hash, git commit, extraction window, ingestion status. The audit trail for replay (P4) at the data layer.

Primitives never write to these tables and never delete from them. They read only from `v_market_data_daily_enriched`, which joins the two storage tables.

## Conventions and methodology

These are the practices that keep playbooks consistent across agents.

### Naming

- **File:** `<playbook_name>.yml` (lowercase snake_case). Must match `playbook_name` inside the file.
- **Location:** `<agent>/playbooks/`. Per P11 (domain isolation), each agent owns its playbooks; cross-agent shared playbooks do not exist.
- **`asset_class`:** lowercase, singular noun. Today: `rates`. Adding a new value is a P8 decision.
- **`metric_id`:** lowercase snake_case. The internal name that primitives reference; should be vendor-neutral wherever possible (`yield_mid`, not `bbg_yld_ytm_mid`).
- **`instrument_type`:** snake_case noun phrase. Consistent within an asset class (`sovereign_benchmark`, `ois_swap`, `bond_future`); a new value across asset classes should mirror the style.
- **Per-row family field name:** asset-class-specific (rates uses `curve_family`); choose once for the asset class and use it consistently across every playbook in that class.

### Vendor field references

- Each `target_metric` / `reference_metric` references the vendor field by its canonical mnemonic in the field-name slot (`bloomberg_field` today). When a second adapter ships, additional vendor slots are added alongside without renaming the existing one — the rows already ingested through Bloomberg keep their `bloomberg_field` provenance.
- Do not put computed or derived fields here. The playbook is for raw vendor fields only; derivation belongs in primitives.

### `attributes` JSONB

The `attributes` column on `instrument_master` is the escape hatch for fields the typed schema does not cover. Use it when:

- The field is domain-specific to one or two playbooks (e.g., OIS swap leg conventions today live in `attributes`).
- The field is needed by a small number of primitives but is not query-hot enough to justify a typed column.

Do not use it for:

- Fields that the platform queries across many primitives — promote those to typed columns via a schema migration (with an ADR).
- Fields whose schema will mutate often — the absence of column constraints means schema drift in `attributes` is silent. Document the expected shape in the playbook itself, or in the domain agent's contract.

### Versioning

- `playbook_version` bumps on any **content-affecting** change: adding/removing a universe row, adding/removing a metric, changing `extraction.start_date` or `incremental_window_days`.
- Cosmetic edits (whitespace, key order, comments) do not require a version bump — the playbook's content hash canonicalises them out so a re-extraction does not double-ingest.
- The version number is stamped into every `load_audit` row, so historical loads remain attributable to the version they were produced under.

### First load vs. subsequent loads

- The coverage gate compares incoming instrument count to the previous successful load's count. On the **very first load** there is no prior, and the gate trivially passes — but this is the only time it does so without comparison, so first loads warrant extra care.
- A first load of a new playbook should be done in `historical` mode against the full `extraction.start_date`. After the first successful load, subsequent runs default to `incremental` and re-fetch only `incremental_window_days` worth of recent data.

### Concurrent runs

There is no per-playbook locking today. Concurrent ingestion of the same playbook can race; the atomic transaction protects against state corruption, but the second run may waste work or briefly overwrite rows the first run had just written. Treat playbook ingestion as **operationally serialised** by external orchestration (one run at a time per playbook). Adding an advisory lock per `playbook_name` is a known gap, tracked as a follow-up.

## Anti-patterns

Auto-reject in review:

- **Inline computation in the playbook.** *"Coupon × 100"*, *"if currency=USD then X"*. The playbook is declarative; derivations belong in primitives.
- **A new top-level key.** The five top-level keys (`playbook_name`, `playbook_version`, `asset_class`, `dataset_name`, `description`, `extraction`, `target_metrics`, `reference_metrics`, `universe`) are the contract. Adding a new one is a P8 decision; file an ADR before introducing it.
- **A vendor mnemonic that looks made up.** *"BBG_YIELD_THING"*. Every `bloomberg_field` value must be a real, documented Bloomberg field. The extractor will fail at runtime; the review catches it earlier.
- **Per-row fields that drift across rows in the same playbook.** If some rows have a `coupon` field and others don't, either (a) it is genuinely missing for the second set (then make that explicit with `coupon: null` and document why) or (b) it should be present for all and was forgotten. Drift breaks the JSONB merge in `attributes`.
- **`is_active: false` rows used as a soft-delete mechanism.** If an instrument is no longer in the universe, remove the row and let the next ingestion's coverage gate handle it (or, if it would push you under the 80% gate, do a documented one-time threshold override). Carrying inactive rows pollutes downstream queries.
- **A playbook spanning multiple asset classes.** Each playbook is for one `asset_class`. Cross-class universes are an orchestration concern, not a playbook concern.
- **A playbook missing `description`.** The catalogue is human-browsable; an undescribed playbook is unmaintainable.
- **A "v0.x" or "draft" version.** Any merged playbook is shipped at v1.0 or later. Pre-merge drafts live in PRs, not in the playbook itself.
- **Changing `playbook_name` after the first ingestion.** The audit trail is keyed on this; renaming breaks continuity. If a rename is genuinely needed, that is a P8 decision and the migration plan goes through an ADR.

## Relationship to primitives and other components

A clean boundary:

- **Playbooks declare the universe.** Which instruments exist, what fields are pulled for them, with what cadence.
- **Primitives compute over the universe.** They query `v_market_data_daily_enriched`, filter by the family field, and produce typed artifacts.
- **Operators are universe-blind.** They consume typed artifacts and never reach back to `instrument_master` or `market_data_daily`. This is P9.

Primitives never assume a particular ticker exists — they query the enriched view by family + tenor and operate on whatever the playbook has populated. Adding instruments to a playbook automatically extends the primitive's reach with no primitive-code change.

The playbook is the **boundary between the data substrate (L1) and the agent's analytic substrate (L2+)**. Per P7, nothing above L1 knows the vendor identity; per P11, primitives in one agent only see playbook data from that agent's playbooks.

## Open questions and known limitations

These are documented gaps; they do not affect the contract today but are flagged so a future agent or contributor does not invent workarounds.

1. **Per-playbook advisory lock.** No protection against concurrent ingestion of the same playbook. State stays consistent (atomic transaction) but parallel runs are wasteful and can briefly thrash. Adding a Postgres advisory lock keyed on `playbook_name` is the agreed fix.
2. **Incremental window overlap.** No validation that `requested_start_date` ≥ prior `requested_end_date`. Overlapping incremental windows can create duplicate rows under some failure modes.
3. **Field-level reload.** When a `target_metric` is removed in a new playbook version and the load runs in historical mode, the prior data for that field is deleted alongside the still-active fields. Removing fields is currently a destructive operation — granular field-level versioning is a known gap.
4. **Schema discipline on `attributes`.** No enforcement of shape. If a vendor adds a new label on a reference field, it lands in `attributes` silently. Primitives that assume a specific JSONB shape can break. Mitigation: each agent's playbook contract documents the `attributes` schema it relies on.
5. **No "soft-deprecate" path for universe rows.** Removing a row triggers the coverage gate at 80%; large universe trims need either a one-time threshold override or a planned multi-step shrink.
6. **Coverage threshold is global.** The 80% threshold is hard-coded in the ingester; per-playbook tuning is not yet supported. Some playbooks (e.g., declining-liquidity legacy markets) may warrant a different gate.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-17 | Initial playbook contract. Universal top-level shape (five required keys + the universe block); the four ingestion guarantees (declarative, atomic, idempotent, coverage-gated); per-row field conventions (`ticker`, `instrument_type`, `country`, `currency`, `tenor`, family field); domain-extension policy via typed columns or `attributes` JSONB; vendor-mnemonic isolation (P7); per-agent ownership (P11); anti-patterns list; primitives–playbook boundary; six open questions captured. | (pending) |
