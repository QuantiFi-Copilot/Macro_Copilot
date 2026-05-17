# Playbook

> The declarative YAML spec that owns an agent's data universe — what instruments exist, which vendor fields to pull for them, how often, and how the ingestion pipeline must handle the result. **Agent-agnostic by design**: the contract on this page holds whether the agent is rates, FX, credit, equity, commodities, options, or any future addition.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
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

### Implementation reality today (versus the contract target)

The contract described in this document is agent-agnostic and vendor-agnostic *by design* — it is the target the platform commits to as new agents and adapters come online. **The current implementation is rates-first and Bloomberg-first, in three specific places**, and a new agent will not be drop-in until each is addressed:

- **`utils/push_playbooks.py` is hardcoded** to sync from `rates_agent/playbooks/` to GCS. Adding an FX, credit, or equity agent's playbooks requires either generalising this script or extending it to walk every agent's `playbooks/` folder. (Tracked as a known gap.)
- **The extractors (`utils/historical_extractor.py`, `utils/incremental_extractor.py`) depend on `blpapi`** and treat Bloomberg as the only source-of-record. A second L1 adapter ships as a sibling extractor following the same contract, not as a vendor-branch inside the existing one.
- **`instrument_master` has typed columns (`curve_family`, `tenor`, `country`, `currency`, `underlying_index`, `contract_code`, …) shaped for rates today**. Asset-class-specific typed columns will be added via schema migrations (P8-flavoured decisions); domain-specific fields land in the `attributes` JSONB until they are query-hot enough to promote.

Treat the rest of this document as the *contract surface* — the discipline a playbook must satisfy regardless of agent. Operational integration with the current path (the rates / Bloomberg one) is documented in [`runbook.md`](runbook.md) and flagged where it diverges.

## What every playbook commits to

Four guarantees. These are the contract the rest of the platform relies on; violating any of them is a substrate bug, not a "config choice."

| # | Guarantee | What it means | Where enforced |
|---|---|---|---|
| 1 | **Declarative.** | The playbook is pure data. No conditional logic, no inline computation, no field that requires interpretation beyond a vendor mnemonic. | *Today:* the loader is `yaml.safe_load` with defensive filtering at read time — a row missing `ticker` is dropped, a metric missing `bloomberg_field` is dropped. There is no strict schema validator that rejects unexpected top-level keys or unknown per-row fields. Adding a strict Pydantic-backed playbook validator is a known follow-up. |
| 2 | **Atomic ingestion of the destructive section.** | The destructive section of a load — *delete prior `market_data_daily` rows (full scope or window) + upsert the new `market_data_daily` rows + flip `load_audit.status` to `SUCCESS`* — runs inside one Postgres transaction. If any step fails, the entire destructive section rolls back, leaving prior market data intact. | The ingester's `with engine.begin() as conn:` block wraps these three operations as one unit. **Outside the critical transaction** (so they pre-exist any retry): the audit row is inserted with `status = RUNNING`, and `instrument_master` is upserted. Both are idempotent on retry (audit gets a fresh `load_id`; `instrument_master` is keyed on `(vendor, vendor_ticker)`); including them in the critical transaction would extend lock windows without correctness benefit. |
| 3 | **Idempotent on market data.** | Re-running the same playbook against the same vendor snapshot produces no duplicate rows in `market_data_daily`. Determined by SHA-256 over a canonicalised content view of the *extracted Parquet* (excluding lineage stamps like `extracted_at`, `git_commit_hash`, `playbook_hash`). | The dedup gate skips the destructive section when the incoming `source_file_hash` matches the previous successful load's hash for the same `playbook_name`. **A `SKIPPED_DUPLICATE` row is still inserted into `load_audit`** — by design, so the audit trail records every ingestion attempt (including dedup hits) and a missing audit row never means "nothing happened, we just don't know." Drives P4 at the data layer. |
| 4 | **Coverage-gated, in two places.** | Two independent gates protect against partial data: (a) the **extractor's 90% upload gate** — if fewer than 90% of the playbook's universe tickers returned data from the vendor, the extractor refuses to upload the Parquet at all; (b) the **ingester's 80% destructive gate** — if the incoming Parquet has fewer than 80% of the previous successful load's instrument count, the ingester aborts the destructive section, marks the audit `FAILED`, and leaves prior data intact. The two thresholds are deliberately different — see the *Coverage gates* subsection below. | Extractor gate in `utils/historical_extractor.py` and `utils/incremental_extractor.py`; ingester gate in `ingestion/ingest_parquet.py`, running before the destructive transaction. |

These four guarantees are agent-agnostic. They apply to every playbook the same way, regardless of asset class.

## The universal contract

Every playbook MUST have the top-level keys listed below. Names are case-sensitive and stable across asset classes. There are eight required keys; the order is conventional (review-friendly), not enforced.

> **Current behaviour vs. target contract.** The current extractor (`utils/historical_extractor.py`) tolerates additional operational top-level keys it reads as defaults — `vendor`, `instrument_type`, `default_instrument_type`, `curve_family`, `underlying_index`, `is_rolling_contract`, `is_active`, `bdh_kwargs`, `bdp_kwargs`. These are not part of the universal contract: they are extractor-side operational defaults that pre-date the stricter shape this document codifies. The target contract is what is documented below; tightening the extractor to reject unknown top-level keys is a future schema-hardening decision (file an ADR when it is taken). Until then, new playbooks SHOULD restrict themselves to the documented keys, and reviewers SHOULD push back on uses of the legacy operational keys for new playbooks.

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
- **`playbook_version`** — semver. Bumped whenever the YAML changes in a way that affects what gets ingested: universe rows added/removed, target/reference metrics added/removed, `extraction` window changed. Cosmetic edits (whitespace, key reorder, comment changes) do not require a bump because they do not change the *extracted Parquet* and therefore do not change the ingester's dedup hash — but they *do* change `playbook_hash` (which is a raw SHA-256 of the YAML bytes; see the *Versioning* section below for the full two-hash distinction).
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

- **`target_metrics`** are **time-series** fields — one value per instrument, per day. They land as rows in `macro_data.market_data_daily` keyed by `(trade_date, instrument_id, field_name)`. **`field_name` in the table is the vendor mnemonic itself** (e.g., `YLD_YTM_MID`, `PX_LAST`, `OPEN_INT`) — *not* the internal `metric_id` declared in the playbook. The `metric_id` is the human-friendly name surfaced in catalogues and methodology cards; the on-disk `field_name` mirrors what the vendor returned. Examples: a yield, a price, an open-interest count.
- **`reference_metrics`** are **static** fields — one value per instrument, stamped once on `instrument_master` (either into a typed column or into the `attributes` JSONB). They do not change daily, and the ingester does not re-row them per day. Examples: maturity date, coupon, settlement convention, contract size.

The split exists because the two have very different storage costs (time-series scales linearly with days; reference fields do not) and very different query patterns. Both lists can be empty in a degenerate case; in practice every playbook has at least one target metric.

**Vendor mnemonic naming.** Today the field is named `bloomberg_field` because Bloomberg is the source of record, and the vendor mnemonic stored in the playbook is also what lands in `market_data_daily.field_name` — primitives query the enriched view by that mnemonic. When a second adapter ships, the per-vendor name will be added alongside (`internal_lake_field`, `xyz_vendor_field`, etc.) without renaming the existing one — the existing tag stays accurate for whichever rows were ingested through that adapter. P7 isolation means the rest of the platform never *parses* the mnemonic to decide vendor behaviour; primitives use it as a string key against the enriched view.

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

- `playbook_version` bumps on any **content-affecting** change: adding/removing a universe row, adding/removing a metric, changing `extraction.start_date` or `incremental_window_days`. The version is a contributor-driven semver — there is no automatic check that the bump matches the change.
- The version number is stamped into every `load_audit` row, so historical loads remain attributable to the version they were produced under.

**Two distinct hashes; do not conflate.** This is the most common source of confusion:

- **`playbook_hash`** is a raw SHA-256 of the YAML file's bytes (see `_sha256_file(pb_path)` in `utils/historical_extractor.py`). It changes on *any* edit, including cosmetic ones (whitespace, key reorder, comment changes). It is stamped into `load_audit.playbook_hash` for traceability — *"which exact bytes were on disk when this extraction ran"* — but it is **not** what the dedup gate compares.
- **`source_file_hash` (a.k.a. the *normalized data hash*)** is SHA-256 over a canonicalised content view of the *extracted Parquet*, with lineage stamps (`playbook_hash`, `git_commit_hash`, `extractor_version`, `extracted_at`, etc.) explicitly excluded. This is what the ingester's dedup gate compares against the previous successful load's hash. See `ingestion/hashing.py`.

**Practical consequence.** A cosmetic edit to a playbook YAML changes `playbook_hash` but does **not** change `source_file_hash`, because the extractor reads the same fields the same way and produces the same Parquet content. So a re-extraction after a cosmetic edit still dedups cleanly at the ingester (a `SKIPPED_DUPLICATE` audit row is inserted with the new `playbook_hash` but no destructive section runs). What this means in practice: cosmetic edits do *not* require a version bump if the universe and metrics are unchanged, because the data the playbook produces is unchanged — but the `playbook_hash` provenance trail will still show the bytes were different on that run.

### Coverage gates (the two distinct thresholds)

Two independent gates protect the dataset from partial extractions. They are *not* the same gate at different points; they ask different questions against different baselines:

| Gate | Where it runs | What it compares | Threshold | What happens on failure |
|---|---|---|---|---|
| **Extractor upload gate** | Inside the extractor, before any Parquet is written to GCS. | *Tickers successfully returned by the vendor* ÷ *tickers declared in the playbook's `universe`*. | **≥ 90%** | The extractor refuses to upload; no Parquet lands in `gs://…/data/`; nothing reaches the ingester. The extraction run is logged as failed for that playbook. |
| **Ingester destructive gate** | Inside the ingester, after the incoming Parquet is hash-checked, before the critical (destructive) transaction. | *Unique instruments in the incoming Parquet* ÷ *unique instruments in the previous successful `load_audit` row*. | **≥ 80%** | The ingester aborts; marks the `load_audit` row `FAILED`; leaves prior `market_data_daily` and `instrument_master` data intact. Parquet stays in `gs://…/data/` for investigation. |

The two thresholds are deliberately different. The 90% extractor gate is *pre-upload sanity* — catches transient vendor issues before they propagate. The 80% ingester gate is the *destructive-load safety* — protects historical data from being deleted if some upstream change shrinks the universe.

Both thresholds are global and hardcoded in code today; per-playbook tuning is a known gap.

### First load vs. subsequent loads

- The ingester's 80% gate compares incoming instrument count to the previous successful load's count. On the **very first load** there is no prior, and the gate trivially passes — but this is the only time it does so without comparison, so first loads warrant extra care.
- The 90% extractor gate is still active on first loads (it compares against the playbook's own universe size), so it provides some protection. But it is not a substitute for manual verification on first load.
- A first load of a new playbook should be done in `historical` mode against the full `extraction.start_date`. After the first successful load, subsequent runs default to `incremental` and re-fetch only `incremental_window_days` worth of recent data.

### Concurrent runs

There is no per-playbook locking today. Concurrent ingestion of the same playbook can race; the atomic transaction protects against state corruption, but the second run may waste work or briefly overwrite rows the first run had just written. Treat playbook ingestion as **operationally serialised** by external orchestration (one run at a time per playbook). Adding an advisory lock per `playbook_name` is a known gap, tracked as a follow-up.

## Anti-patterns

Auto-reject in review:

- **Inline computation in the playbook.** *"Coupon × 100"*, *"if currency=USD then X"*. The playbook is declarative; derivations belong in primitives.
- **A new top-level key not in the eight-key contract.** The eight required top-level keys (`playbook_name`, `playbook_version`, `asset_class`, `dataset_name`, `description`, `extraction`, `target_metrics`, `reference_metrics`, `universe`) are the target contract. The current extractor tolerates a handful of legacy operational keys (see *Current behaviour vs. target contract* above); new playbooks SHOULD NOT use them. Adding a new contractual top-level key is a P8 decision; file an ADR before introducing it.
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
6. **Coverage thresholds are global.** Both the 90% extractor-upload threshold and the 80% ingester-destructive threshold are hard-coded; per-playbook tuning is not yet supported. Some playbooks (e.g., declining-liquidity legacy markets, or universes with a known number of inactive tickers) may warrant different thresholds, or per-ticker-tier thresholds.

7. **No strict playbook schema validation.** The loader is `yaml.safe_load` with defensive filtering — unknown top-level keys are silently tolerated, unknown per-row fields land in `attributes` JSONB without warning. A Pydantic playbook model (rejecting unexpected keys and validating per-row required fields) is a known follow-up that would let this document's contract be enforced mechanically.

8. **`push_playbooks.py` is rates-hardcoded.** The sync script that uploads local YAML to GCS hard-codes `rates_agent/playbooks/`. Adding a second agent's playbooks requires either generalising this script (walk every `<agent>/playbooks/` folder) or running a per-agent equivalent. Tracked as part of the broader "make the operational path agent-agnostic" work.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Nine factual corrections from pre-canonical review, each re-verified against the cited source files: (a) **Guarantee #1 (Declarative)** — softened the "loader rejects unexpected node types" claim; the loader is `yaml.safe_load` with defensive filtering; strict validator is a known follow-up. (b) **Guarantee #2 (Atomic)** — narrowed scope to *delete + market_data_daily upsert + audit-success flip* (which IS atomic per `with engine.begin() as conn:`); the audit-RUNNING insert and `instrument_master` upsert happen *outside* the critical transaction (deliberately, per the in-code comment, for retry idempotency and lock-window reasons). (c) **Guarantee #3 (Idempotent)** — corrected to *no duplicate `market_data_daily` rows*; a `SKIPPED_DUPLICATE` audit row IS inserted on a dedup hit by design (preserves audit trail). (d) **Guarantee #4 (Coverage-gated)** — restated as TWO distinct gates: 90% extractor-upload gate (in `historical_extractor.py` / `incremental_extractor.py`) and 80% ingester-destructive gate (in `ingest_parquet.py`); added a dedicated *Coverage gates* subsection with a side-by-side table. (e) **`target_metrics` storage** — corrected the claim that `metric_id` becomes `field_name`; the **vendor mnemonic** (`bloomberg_field`) becomes `field_name` in `market_data_daily`. (f) **Versioning** — distinguished `playbook_hash` (raw SHA-256 of YAML bytes) from `source_file_hash` / normalised data hash (canonicalised over Parquet content); cosmetic edits change `playbook_hash` but not the dedup hash. (g) **Universal contract count** — corrected "five top-level keys" (was listed as nine) to "eight required top-level keys"; added a "current behaviour vs. target contract" caveat acknowledging that the current extractor tolerates legacy operational keys (`vendor`, `default_instrument_type`, `bdh_kwargs`, etc.). (h) **Implementation reality** — added an explicit section near the top documenting the three places the current operational path is rates-first / Bloomberg-first (hardcoded `push_playbooks.py`, `blpapi`-dependent extractors, rates-shaped typed columns on `instrument_master`); flagged each as a tracked gap. (i) **Open questions** — added items 7 (no strict schema validator) and 8 (`push_playbooks.py` is rates-hardcoded). | (pending) |
| v1 | 2026-05-17 | Initial playbook contract. Replaced by v1.1 the same day after a factual-review pass. | — |
