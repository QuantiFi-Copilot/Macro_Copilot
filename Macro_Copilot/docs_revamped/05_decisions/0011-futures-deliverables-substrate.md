# ADR 0011 — `macro_data.futures_deliverables`: per-contract deliverable basket substrate

**Status:** Proposed
**Date:** 2026-05-23
**Builds on:** ADR 0001 (sibling SCD2 `instrument_metadata_history` — the per-contract identity `(instrument_id, contract_code)` C3 borrows for keying), ADR 0002 (the `metadata_history:` playbook section pattern — C3 mirrors it as a `deliverables:` section, §15.6), ADR 0004 (`event_calendar` substrate — the "new table + new extractor mode + new ingester route" template C3 mirrors structurally), ADR 0003 (cash-bond substrate — the typed `cusip` / `isin` identity columns on `instrument_master` C3's deliverable bonds optionally reference).
**Operationalises principles:** P1 (built right — C3 ships the full substrate so C4 is a pure data PR), P2 (accuracy or refuse — the extractor mode is structural; every Bloomberg mnemonic is CANDIDATE until C4's operator probe; if a field will not verify it is deferred and documented), P3 (one shape — C3 mirrors the established metadata-history pattern rather than inventing), P4 (idempotent re-ingestion — natural-key UPSERT), P5 (honest disclosure — what the substrate does NOT do, recorded below), P12 (Bloomberg Accuracy Boundary — deliverable baskets, conversion factors, delivery/notice dates are ingested verbatim, never recomputed).
**Scope:** Track C, work order **C3** — the substrate for bond-future deliverable baskets. C3 is substrate-only: ADR + schema + DB helper + shared validation module + ingester route + extractor mode + tests. **No Bloomberg, no playbook, no data** — those are C4.

---

## Context

A bond-future contract (e.g. the Dec-2024 10Y Treasury future, `TYZ24`) carries three pieces of reference data the Phase-4 RV stack needs:

- a **deliverable basket** — the cash bonds eligible for delivery into the contract;
- a **conversion factor** per (contract, deliverable bond) — the price adjustment used in the futures invoice;
- per-contract **first/last delivery dates** and **first/last notice dates**.

This is **reference data**, not time series. It does not fit `market_data_daily`. It needs its own table, exactly as `event_calendar` did in ADR 0004 — and it is the prerequisite for every Phase-4 bond-futures RV primitive (`ctd_identifier`, `gross_basis`, `implied_repo_rate`, `net_basis`, `inter_commodity_spread_dv01_weighted`, `cross_country_bond_future_spread_dv01_fx_adjusted`).

The bond-future *generics* (`TY1`, `RX1`, `JB1`, …) are already `instrument_master` rows; the per-cycle specific contracts (`TYZ24`, `TYH25`, …) already live as effective-dated windows in `instrument_metadata_history` (keyed `(instrument_id, contract_code)` — ADR 0001). C3 reuses that keying to identify the contract a basket belongs to.

The deliverable bonds — UST CUSIPs for US contracts, ISINs for non-US — span seasoned issues *wider* than A4's OTR/OFR-bounded `sovereign_cash_bonds.yml` universe. C3 therefore does not require deliverable bonds to be pre-registered in `instrument_master`; the CUSIP is the canonical identity, and a registered FK is optional convenience.

C3 is a *substrate-only* work order — it ships the table, helpers, ingester route, extractor mode, and tests. The actual deliverables playbook (`deliverables:` section on `bond_futures.yml`) + the Bloomberg verification + the ingested data are **C4**.

## Decision

### 1. The contract is keyed by `(instrument_id, contract_code)` (§15.5a)

A `futures_deliverables` row references the specific contract cycle by two columns: `instrument_id BIGINT NOT NULL REFERENCES instrument_master(instrument_id)` (the generic — TY1's row) **plus** `contract_code VARCHAR(32) NOT NULL` (the specific cycle — `TYZ24`).

This mirrors `instrument_metadata_history` exactly. Joins to the metadata table — to read a contract's expiry / accrual / tick info — are `ON (instrument_id, contract_code)`. The keying is desk-facing (operators query "TYZ24's basket", not a synthetic id) and backfill-friendly: a basket can land before `instrument_metadata_history` has extracted that contract cycle (no chicken-and-egg).

A `metadata_history_id` FK was rejected — it creates a hard ordering dependency, and it would tie deliverables to one SCD2 window when the basket is really about the contract, not about a metadata window. A plain string ticker (`"TYZ24 Comdty"`) was rejected — no FK to the generic.

### 2. The deliverable bond is a CUSIP string + an optional `instrument_master` FK (§15.5b)

A `futures_deliverables` row carries `deliverable_cusip VARCHAR(16) NOT NULL` — the canonical Bloomberg identity — and `deliverable_instrument_id BIGINT NULL REFERENCES instrument_master(instrument_id)` — populated only when the deliverable bond happens to be in `instrument_master`. An optional `deliverable_isin VARCHAR(16) NULL` is also stored for cross-identifier joins.

Most deliverable bonds are seasoned issues NOT currently in `instrument_master` (A4's universe is bounded to OTR + recently-off-the-run; a basket spans far wider). The CUSIP string is therefore the universal identity, and the FK is convenience: a downstream primitive joins on the FK when set, else on `deliverable_cusip = instrument_master.cusip` (the ADR-0003 partial unique index `WHERE cusip IS NOT NULL` makes that fast).

Auto-registering every deliverable bond into `instrument_master` was rejected for C3 — that materially expands the cash-bond universe (≈30 bonds × ~20 cycles × 19 futures generics → ~11k new instrument-master rows) and is a separate workstream decision. Storing only the CUSIP with no FK was rejected — it loses the convenience join when the bond *is* registered.

> **Universality note.** This v1 schema is UST-shaped (`deliverable_cusip` is `NOT NULL`). Non-US deliverable baskets (Bunds, Gilts, JGBs) typically expose ISINs as the canonical identifier from Bloomberg — C4's verification script tells us empirically which `FUT_DLVRBL_BNDS_*` variant returns what. The `deliverable_isin` column accommodates that; if Bloomberg's non-US deliverable identifiers do not also resolve to CUSIPs, C4's scope is restricted to UST contracts in v1 (a documented deferral, P2/P5) and a follow-up ADR refines the substrate for non-US.

### 3. C4's playbook lives as a `deliverables:` section on `bond_futures.yml` (§15.6)

Mirrors the existing `metadata_history:` section on the same playbook. Same 19-generic universe — no duplication, one playbook describes both flows. The new `--mode deliverables` extractor reads `bond_futures.yml`'s `deliverables:` section exactly as `--mode metadata-history` reads its `metadata_history:` section (ADR 0002). A standalone playbook was rejected — duplicate universe, two files to keep in sync.

### 4. The `deliverables/` data flow (substrate-level)

- **`--mode deliverables`** is added to **both** `utils/historical_extractor.py` and `utils/incremental_extractor.py`, mirroring `--mode metadata-history`. Historical for the initial deep backfill (chain enumeration with `INCLUDE_EXPIRED_CONTRACTS="Y"`); incremental for keeping active and near-future contract cycles current. Both extractors stay self-contained — no project-internal imports.
- A wide parquet (one row per `(generic, contract_code, deliverable_cusip)`) is uploaded to `gs://<bucket>/deliverables/<dataset>/`.
- The ingester's `_process_deliverables_blob` (new route) downloads, validates via the new `ingestion/deliverables.py` shared module, resolves vendor tickers → instrument_id, and writes via `upsert_futures_deliverables` under the existing atomic-critical-section + RUNNING-audit + content-hash-dedup discipline.

## What this ADR explicitly does NOT do

- **No Bloomberg work**, no playbook update, no data — C4 owns those. Every Bloomberg mnemonic (`FUT_DLVRBL_BNDS_*`, `FUT_CNVS_FACTOR`, `FUT_DLV_DT_FIRST`, `FUT_DLV_DT_LAST`, `FUT_NOTICE_FIRST`, `FUT_NOTICE_LAST`, etc.) is **CANDIDATE** until C4's operator probe verifies it.
- **No auto-registration of deliverable bonds** in `instrument_master`. The optional FK is set only for bonds already registered.
- **No Phase-4 primitives** (`ctd_identifier`, `futures_dv01`, `gross_basis`, `implied_repo_rate`, `net_basis`, `inter_commodity_spread_dv01_weighted`, `cross_country_bond_future_spread_dv01_fx_adjusted`). Track C is data only.
- **No new SCD2 machinery.** Deliverable baskets are not effective-dated in the SCD2 sense — they are reference-data per contract, with per-contract delivery / notice dates stored as plain columns. A `(generic, contract_code, deliverable_cusip)` row IS or IS-NOT in the basket; full-row UPSERT on the natural key handles updates.
- **No `instrument_master` schema change.** `instrument_type` values are not enumerated; the bond-future generics already carry `instrument_type: bond_future` from `bond_futures.yml`.
- **No Alembic.** Track C never touches Alembic (project convention).

## Alternatives considered

- **Storing the deliverable basket as JSONB on `instrument_metadata_history.attributes`.** Rejected: the basket is a list of bond identifiers + per-bond conversion factors — querying "which contracts is CUSIP X deliverable into?" forces a JSONB scan across all metadata-history rows. A relational table with an index on `deliverable_cusip` answers that in one lookup. Also: each metadata-history row is one SCD2 *window* — but a basket is one *contract*, not one window.
- **Two tables — `futures_contracts` (per-contract dates) + `futures_deliverables` (per contract-bond pair).** Rejected for v1: per-contract dates (first/last delivery, first/last notice) duplicated across ~30 rows per basket is mildly denormalised, but the table is small (~11k rows max) and the join cost of a two-table split outweighs the duplication. A future ADR can split if scale demands.
- **`metadata_history_id` FK as the contract key (instead of `(instrument_id, contract_code)`).** Rejected — Decision 1.
- **Auto-registering every deliverable CUSIP** in `instrument_master`. Rejected — Decision 2.
- **Standalone deliverables playbook.** Rejected — Decision 3.

## Design — schema (`database/schema.sql` Section 8)

```sql
-- ================================================================================================
-- 8) macro_data.futures_deliverables  (ADR 0011)
--    Per-bond-future-contract deliverable basket + conversion factors + delivery/notice dates.
--    The contract is identified by (instrument_id, contract_code) — same keying as
--    instrument_metadata_history (ADR 0001). The deliverable bond is identified by CUSIP
--    string + optional FK to instrument_master (ADR 0003 cash-bond identity).
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.futures_deliverables (
    deliverable_id BIGSERIAL PRIMARY KEY,

    -- The futures contract — generic + cycle (ADR 0001 keying).
    instrument_id BIGINT NOT NULL REFERENCES macro_data.instrument_master(instrument_id),
    contract_code VARCHAR(32) NOT NULL,

    -- The deliverable bond — CUSIP canonical, ISIN secondary, optional FK to instrument_master.
    deliverable_cusip VARCHAR(16) NOT NULL,
    deliverable_isin  VARCHAR(16),
    deliverable_instrument_id BIGINT REFERENCES macro_data.instrument_master(instrument_id),

    -- Per (contract, deliverable) — the futures invoice adjustment.
    conversion_factor NUMERIC(20, 10),

    -- Per contract — duplicated across deliverables in the basket (denormalised by design — §Alternatives).
    first_delivery_date DATE,
    last_delivery_date  DATE,
    first_notice_date   DATE,
    last_notice_date    DATE,

    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Natural key — idempotent re-ingestion. One row per (generic, contract cycle, deliverable bond).
    CONSTRAINT uq_futures_deliverables_natural_key
        UNIQUE (instrument_id, contract_code, deliverable_cusip)
);

-- Per-contract reads ("give me TYZ24's basket"): the natural-key UNIQUE already supports the
-- (instrument_id, contract_code) prefix, but a dedicated index makes the intent explicit and
-- supports counts.
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_contract
    ON macro_data.futures_deliverables (instrument_id, contract_code);

-- Per-deliverable-bond reads ("which contracts is this CUSIP deliverable into?").
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_cusip
    ON macro_data.futures_deliverables (deliverable_cusip);

-- Optional FK lookups.
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_deliverable_instrument
    ON macro_data.futures_deliverables (deliverable_instrument_id)
    WHERE deliverable_instrument_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_futures_deliverables_attributes
    ON macro_data.futures_deliverables USING GIN (attributes);
```

## Design — DB helper (`database/database.py`)

One new helper, additive:

- **`upsert_futures_deliverables(connectable, records)`** — `_txn`-Connectable contract (Engine OR Connection); `INSERT ... ON CONFLICT (instrument_id, contract_code, deliverable_cusip) DO UPDATE` for idempotent re-ingestion. Models on `upsert_event_calendar` (full-row upsert on a natural key) + `upsert_instrument_metadata_history` (the per-record normaliser pattern). Includes `_normalize_deliverables_record` for uniform-key shaping so SQLAlchemy bulk insert derives one coherent column list regardless of which optional fields each record carries.

Full-row UPSERT semantics: re-ingesting the same parquet over the same natural key safely overwrites every non-key column from the incoming record. There is **no surgical-COALESCE exception** like ADR 0009 §5 — `deliverable_instrument_id` (the optional FK) is populated by the same ingestion pipeline (the route function does the lookup), not by a separate cross-pipeline writer, so a plain overwrite is correct here.

## Design — shared validation module (`ingestion/deliverables.py`)

Mirrors `ingestion/metadata_history.py` + `ingestion/event_calendar.py`. Pure-Python helpers — no Bloomberg, no GCS, no DB — so they are unit-testable in isolation:

- **`DELIVERABLES_TYPED_COLUMNS`** — the single-source-of-truth tuple of typed columns on `futures_deliverables` (mirrors `HISTORY_TYPED_COLUMNS` in `metadata_history.py`).
- **`parquet_to_deliverables_records(df, instrument_id_map, load_id)`** — convert a wide deliverables parquet into per-record dicts ready for `upsert_futures_deliverables`. Resolves `vendor_ticker` → `instrument_id` via `instrument_id_map` (the generic's `instrument_master` row); the deliverable bond's optional FK is left to the ingester route to resolve from `deliverable_cusip` against `instrument_master.cusip`. Dates → ISO strings; non-typed columns → `attributes` JSONB.
- **`is_ingestable_deliverable(rec)`** — gate: the four required fields are present (`instrument_id`, `contract_code`, `deliverable_cusip`, plus a valid generic instrument_id).

The two extractors carry **inlined byte-identical copies** of any logic they need pre-upload (per the CANONICAL/SYNC discipline used by `metadata_history.py`); this module is the canonical source.

## Design — ingester route (`ingestion/ingest_parquet.py`)

A new route function `_process_deliverables_blob(blob, engine, ...)` mirroring `_process_event_blob` / `_process_metadata_history_blob`:

1. Download the parquet, normalised content hash → dedup check.
2. Insert a `load_audit` row at `RUNNING`.
3. Parse via `parquet_to_deliverables_records`; gate via `is_ingestable_deliverable`.
4. Resolve deliverable bonds — for every row whose `deliverable_cusip` matches an `instrument_master.cusip`, populate `deliverable_instrument_id` (the optional FK). Else leave NULL.
5. Inside an atomic `engine.begin()` block:
   - `upsert_futures_deliverables(conn, records)`.
   - Flip the audit row to `SUCCESS`.
6. On any exception inside the critical section, the whole transaction rolls back; the audit row is flipped to `FAILED` in a separate transaction.
7. Archive the parquet under `gs://<bucket>/archive/deliverables/<dataset>/` on success (mirrors the established `archive/<phase>/` convention used by the other ingester routes); leave under the live `deliverables/` prefix on failure (operator can investigate). Path corrected from the v1 draft (which said `_archive/`) to match the code.

The blob-routing dispatcher in `run_ingestion()` gains a `deliverables/` branch alongside the existing `data/`, `metadata_history/`, `event_calendar/`, `otr_resolution/` routes.

## Design — extractor `--mode deliverables`

Added to BOTH extractors (mirroring `--mode metadata-history`). Reads the `deliverables:` section on a playbook (added in C4 — not C3). Section shape (defined here as a CONTRACT for C4 to honour; field-name VALUES are CANDIDATE / VERIFIED by C4). Schema below is the **v4 contract** the extractor's `_resolve_deliverables_section` validator enforces — anything in violation raises and aborts the load:

```yaml
deliverables:
  enabled: true                             # required; absent / falsy => skip
  chain_field: <bloomberg field>            # required; bds — enumerate contract cycles per generic
  chain_overrides:                          # optional; e.g. INCLUDE_EXPIRED_CONTRACTS: "Y" for the
    <key>: <value>                          # historical extractor's deep backfill
  chain_column_name: <string>               # optional; bds-result column fallback when chain field
                                            # is keyed under an unusual name in the returned frame
  basket_field: <bloomberg field>           # required; bds on each contract — returns the
                                            # deliverable basket (CUSIP + factor in one call)
  basket_cusip_column: <string>             # required; basket-frame column carrying the CUSIP
  basket_factor_column: <string>            # required (v4); basket-frame column carrying the
                                            # conversion factor. Conversion factor is core source
                                            # data, never silently NULL.
  basket_isin_column: <string>              # optional; when Bloomberg returns ISINs alongside
                                            # CUSIPs, populate deliverable_isin from this column
  static_fields:                            # required; non-empty list. bdp on each contract.
                                            # Each entry MUST be a {column_name, bloomberg_field}
                                            # pair; column_name MUST be one of the four typed
                                            # date columns on macro_data.futures_deliverables;
                                            # no duplicate column_names (v4).
    - column_name: first_delivery_date      # one of: first_delivery_date | last_delivery_date
      bloomberg_field: <bloomberg field>    #         | first_notice_date | last_notice_date
    - column_name: last_delivery_date
      bloomberg_field: <bloomberg field>
    - column_name: first_notice_date
      bloomberg_field: <bloomberg field>
    - column_name: last_notice_date
      bloomberg_field: <bloomberg field>
  include_tickers:                          # optional whitelist (ADR 0011 v3): scope-reduce to a
    - <ticker exactly matching one of                 # subset of the playbook's rolling-contract
      the playbook's rolling-contract                 # universe. EVERY entry must exactly match a
      universe entries>                               # universe ticker (typos / out-of-scope
                                                      # references abort the load, never silently
                                                      # dropped). Omit the key for "all rolling
                                                      # tickers participate".
```

Validator behaviour summary (v4):
- Absent / non-dict / `enabled: false` ⇒ silent skip (legitimate non-participation).
- Enabled + any required field missing or any optional field malformed ⇒ raise `ValueError`, caller aborts the playbook.
- Strict all-or-nothing coverage at runtime: both **generic-level** (every configured generic must produce ≥ 1 basket) AND **contract-level** (every probed contract on every chain must produce a complete basket — including non-NULL conversion factor and all configured per-contract dates).

The extractor mode:
1. Reads the section per universe row whose `is_rolling_contract: true`.
2. `bds(generic_ticker, chain_field, chain_overrides)` → contract list.
3. For each contract: `bds(contract, basket_field)` → list of (cusip, conversion_factor) pairs; `bdp(contract, static_fields)` → per-contract dates.
4. Assembles a wide parquet — one row per `(generic, contract_code, deliverable_cusip)` — and uploads to `gs://<bucket>/deliverables/<dataset>/<filename>.parquet`.

Both extractors stay self-contained (no project-internal imports) per the established discipline. The shared validation module `ingestion/deliverables.py` is the canonical source for the typed-column set; the extractors inline a byte-identical copy with the same CANONICAL/SYNC note as `metadata_history.py`.

## Consequences

**Positive.**
- C4 becomes a pure data PR: write the `deliverables:` section on `bond_futures.yml` + a `scripts/deliverables_bloomberg_check.py` verification script + the operator cycle. No further schema/code work.
- Every Phase-4 bond-futures RV primitive can read deliverable baskets, conversion factors, and delivery/notice dates via standard SQL joins (`futures_deliverables` ⋈ `instrument_master` on the optional FK or on `deliverable_cusip = im.cusip`).
- The substrate mirrors established project patterns (ADR 0001 keying, ADR 0002 section, ADR 0004 new-table-+-route, ADR 0003 cash-bond identity) — no surprises for the existing reader.

**Negative / accepted.**
- v1's `deliverable_cusip NOT NULL` is UST-shaped; non-US baskets may need a follow-up ADR if Bloomberg does not also return CUSIPs for Bunds / Gilts / JGBs. C4's verification will tell us empirically. Disclosed (Decision 2 universality note).
- Per-contract dates are duplicated across the ~30 rows of a basket. Accepted for the simplicity of a single table; total row count is small.
- Deliverable bonds outside A4's OTR-bounded `sovereign_cash_bonds.yml` universe are not auto-registered in `instrument_master`. Joins to those bonds' market data depend on a future cash-bond-universe expansion or accept the unregistered state.

## Rollout

1. **This PR (C3):**
   - Land Section 8 (`futures_deliverables`) in `database/schema.sql` + a separate idempotent migration SQL block (below).
   - Land `upsert_futures_deliverables` in `database/database.py`.
   - Land `ingestion/deliverables.py` with the typed-column set + parquet parser + ingestability gate.
   - Land the `deliverables/` route in `ingestion/ingest_parquet.py`.
   - Land `--mode deliverables` in `utils/incremental_extractor.py` AND `utils/historical_extractor.py` (both self-contained, both with inlined CANONICAL/SYNC copies of the typed-column set).
   - `MagicMock`-driven tests under `tests/state/` (`test_deliverables_substrate.py`, `test_deliverables_ingestion.py`).
2. **C4 (separate PR):** write `bond_futures.yml`'s `deliverables:` section with CANDIDATE Bloomberg mnemonics; build `scripts/deliverables_bloomberg_check.py`; operator verifies; finalise to VERIFIED; operator push → extract (`--mode deliverables`) → ingest → sanity queries → PR with report.

### Migration SQL (separate idempotent block — operator runs against existing DBs)

```sql
-- ============================================================================
-- ADR 0011 — Section 8: futures_deliverables. Idempotent, data-safe.
-- Verification + rollback below.
-- ============================================================================

CREATE TABLE IF NOT EXISTS macro_data.futures_deliverables (
    deliverable_id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES macro_data.instrument_master(instrument_id),
    contract_code VARCHAR(32) NOT NULL,
    deliverable_cusip VARCHAR(16) NOT NULL,
    deliverable_isin  VARCHAR(16),
    deliverable_instrument_id BIGINT REFERENCES macro_data.instrument_master(instrument_id),
    conversion_factor NUMERIC(20, 10),
    first_delivery_date DATE,
    last_delivery_date  DATE,
    first_notice_date   DATE,
    last_notice_date    DATE,
    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_futures_deliverables_natural_key
        UNIQUE (instrument_id, contract_code, deliverable_cusip)
);

CREATE INDEX IF NOT EXISTS idx_futures_deliverables_contract
    ON macro_data.futures_deliverables (instrument_id, contract_code);
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_cusip
    ON macro_data.futures_deliverables (deliverable_cusip);
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_deliverable_instrument
    ON macro_data.futures_deliverables (deliverable_instrument_id)
    WHERE deliverable_instrument_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_attributes
    ON macro_data.futures_deliverables USING GIN (attributes);

-- Verification:
-- SELECT count(*) FROM macro_data.futures_deliverables;                     -- 0 until C4 lands
-- SELECT conname FROM pg_constraint WHERE conrelid='macro_data.futures_deliverables'::regclass;
-- SELECT indexname FROM pg_indexes WHERE schemaname='macro_data' AND tablename='futures_deliverables';

-- Rollback (only if NOT yet populated — destructive otherwise):
-- DROP TABLE IF EXISTS macro_data.futures_deliverables;
```

## Verification

After this PR is merged and the migration is applied:

1. `SELECT count(*) FROM macro_data.futures_deliverables;` → **0** (empty until C4's ingest).
2. The four indexes + the natural-key UNIQUE exist (query `pg_indexes` and `pg_constraint`).
3. `python -m py_compile utils/historical_extractor.py utils/incremental_extractor.py ingestion/ingest_parquet.py database/database.py ingestion/deliverables.py` → clean.
4. The `MagicMock` tests under `tests/state/test_deliverables_*` pass.
5. A `INSERT INTO macro_data.futures_deliverables (...) VALUES (...) ON CONFLICT (instrument_id, contract_code, deliverable_cusip) DO UPDATE ...` against a known generic-instrument_id is idempotent on re-run (the natural key behaves).

## v2 amendments (post-Codex review, 2026-05-23)

Codex returned six findings on the v1 implementation. All were validated against the substrate; the five that are real-bug or correctness-gap concerns are fixed in this revision (the sixth was a formatting hygiene fix). The amendments below are **substrate-layer** — they harden C3 *before* C4 lands any data, so the first deliverables load already runs through the v2 checks.

### Amendment 1 — Optional `include_tickers` whitelist on the `deliverables:` section (Codex finding 1)

**Symptom v1:** the extractor processed every rolling-contract row in the playbook universe (≈19 generics) regardless of whether that market's basket is CUSIP-shaped. Non-US futures (Bunds, Gilts, JGBs) typically expose ISINs, not CUSIPs; the v1 substrate would either silently emit blank-CUSIP rows or fail per-contract while the load reported success.

**Fix:** `_resolve_deliverables_section` accepts an optional `include_tickers: [...]` whitelist. Present → only the listed generics are probed. Absent → every rolling row participates (v1 behaviour). The validator rejects the whole section on a malformed whitelist (non-list, empty list, non-string entry, blank-string entry) — never silently treat it as "open universe". A C4 playbook can scope deliverables to UST contracts in v1 (`include_tickers: [TY1 Comdty, FV1 Comdty, …]`) and document the non-US deferral (P2/P5).

### Amendment 2 — Strict all-or-nothing coverage gate (Codex finding 2)

**Symptom v1:** `run_deliverables_extraction` accepted any load with `generics_with_data >= 0.5 * expected_generics`. Deliverable baskets are curated reference data — a half-load is never a legitimate state. The 50% threshold is the vanilla broad-discovery rule for time-series; it leaks here because the extractor copied the time-series harness.

**Fix:** the threshold is now `generics_with_data < expected_generics` → abort. Within the configured scope (`include_tickers`, if present, applies *before* this check), every generic MUST produce a basket. If a generic is genuinely out of scope, exclude it via `include_tickers` and document the deferral. Same rule WIRP uses (ADR 0009 §4).

### Amendment 3 — Audit-key isolation suffix on `playbook_name` (Codex finding 3)

**Symptom v1:** `bond_futures.yml` carries three independent flows on one playbook — `--mode time-series` (the daily quotes), `--mode metadata-history` (the per-contract identity SCD2), and `--mode deliverables` (the basket). They all stamp `playbook_name = "bond_futures"` on the parquet, so the ingester's playbook-keyed dedup (a per-playbook `latest successful hash` check) was shared across modes. A deliverables load would dedup against a time-series load's hash; an audit-trail query for "the latest bond_futures load" would conflate three semantically distinct artifacts.

**Fix:** the deliverables extractor stamps `playbook_name = "<base>__deliverables"` on the lineage record (the universe / source data is unchanged — only the audit-key identity is suffixed). Mirrors the OTR resolver pattern (`<base>__otr_resolution`, already shipped in B3). The ingester therefore scopes dedup and load-audit lookups to the deliverables artifacts of this playbook, never confuses them with the sibling time-series / metadata-history flows on the same `bond_futures.yml`.

### Amendment 4 — `MagicMock` test coverage for the route + extractor (Codex finding 4)

**Symptom v1:** the route function `_process_deliverables_blob` and the extractor entry point `run_deliverables_extraction` shipped without unit tests. The shared validation module (`ingestion/deliverables.py`) was covered, but the wiring around it was not — any regression on the route would only surface during C4's live ingest.

**Fix:** added test classes `TestProcessDeliverablesBlob` (route — covers invalid extraction mode, missing lineage, hash-dedup skip, unknown vendor_ticker, per-contract date conflict, well-formed happy path, FK-link happy path) and `TestResolveDeliverablesSection` (extractor section validator + `include_tickers` whitelist) plus `TestResolveDeliverablesSectionHistoricalParity` (sync invariant — historical and incremental copies must agree).

### Amendment 5 — Per-contract date denormalisation invariant check (Codex finding 5)

**Symptom v1:** the `first_delivery_date`, `last_delivery_date`, `first_notice_date`, `last_notice_date` columns are per-contract (one value per `(instrument_id, contract_code)`) but are stored on every basket row (denormalised — see §Alternatives). The v1 extractor fetched those dates once per contract via `bdp(...)` and broadcast them across the basket rows, so a healthy load was always self-consistent. But the substrate-layer ingester did NOT verify the invariant — a malformed artifact (extractor bug, manual file-edit, format-drift) whose rows disagreed on those dates would be silently folded in. A downstream primitive joining a deliverable bond to its basket's delivery dates would then get different answers per row.

**Fix:** the new helper `validate_per_contract_dates(records: list[dict]) -> list[str]` (in `ingestion/deliverables.py`) groups parsed records by `(instrument_id, contract_code)` and verifies all four date columns are identical across the basket. Returns a list of human-readable conflict descriptions; empty means clean. The route calls it between the ingestability gate and the optional FK CUSIP-lookup; ANY conflict aborts the load, marks the audit row FAILED, and never reaches the upsert.

The old `group_rows_by_contract` helper's docstring (which v1 claimed performed this check but did not) was corrected to truthfully describe its actual role (a small grouping utility).

### Amendment 6 — Trailing-whitespace cleanup in `utils/historical_extractor.py` (Codex finding 6)

**Symptom v1:** the historical extractor file had 359 lines with trailing whitespace (introduced by a CRLF/LF interaction during the deliverables append). `git diff --check` flagged these.

**Fix:** stripped via `re.sub(rb'[ \t\r]+\n', b'\n', data)`. `git diff --check` clean after the cleanup.

### Surfaces actually changed in v2

- `ingestion/deliverables.py` — added `validate_per_contract_dates` + updated `group_rows_by_contract` docstring.
- `ingestion/ingest_parquet.py` — `_process_deliverables_blob` now calls `validate_per_contract_dates` after the ingestability gate and aborts on any conflict.
- `utils/incremental_extractor.py` and `utils/historical_extractor.py` (SYNC INVARIANT, both byte-identical) — `_resolve_deliverables_section` validates the optional `include_tickers` whitelist; `run_deliverables_extraction` filters the universe by the whitelist, applies the strict all-or-nothing coverage gate, and stamps the `__deliverables` audit-key suffix on `playbook_name`.
- `tests/state/test_deliverables_ingestion.py` — added `TestValidatePerContractDates` (9 cases) and `TestProcessDeliverablesBlob` (7 route cases, MagicMock-based — no DB, no GCS, no Bloomberg).
- `tests/state/test_deliverables_extraction.py` — NEW. `TestResolveDeliverablesSection` (14 cases including the whitelist validator) + `TestResolveDeliverablesSectionHistoricalParity` (2 cases pinning the historical / incremental sync invariant).

### What v2 explicitly does NOT change

- **Schema is unchanged** — no migration, no column addition. The amendments are all behavioural (extractor scope, ingester validation, audit-key isolation, test coverage).
- **The natural key is unchanged** — `(instrument_id, contract_code, deliverable_cusip)`.
- **The full-row UPSERT contract is unchanged** — ON CONFLICT DO UPDATE on the natural key still overwrites every non-key column.

## v3 amendments (second-round Codex review, 2026-05-23)

The v2 amendments hardened several gaps but Codex's second review identified four more correctness issues, each of which is implemented in v3. Schema and natural key remain unchanged — every change is at the extractor / test layer.

### Amendment 7 — Contract-level all-or-nothing coverage gate (Codex finding 1, second round)

**Symptom v2:** the strict gate required every *generic* to produce at least one basket but did not require every *contract* on each generic's chain to produce a basket. The historical extractor enumerates the full chain (`INCLUDE_EXPIRED_CONTRACTS="Y"`) — for TY1 that's ~179 quarterly cycles. A v2 load could pass with `generics_with_data = 1` even if only 1 of 179 TY contracts emitted rows. That is silent reference-data loss of the exact kind the gate was supposed to prevent.

**Fix:** the new helper `_evaluate_deliverables_coverage(generics_with_data, expected_generics, total_contracts_probed, total_contracts_with_basket, missed_contracts)` enforces **both** gates in sequence:
1. **Generic-level** (unchanged from v2): every configured generic must produce at least one basket.
2. **Contract-level** (new): every probed contract must produce a basket. The error message names the first 5 missed contracts and the count of additional misses for diagnosability.

`missed_contracts: list[str]` is now tracked through the contract loop — a contract is "missed" when `basket_df is None`, when the configured CUSIP column is absent from the returned frame, or when iterating the basket emits zero valid rows. The empty-basket case is the subtle one: a basket frame with all-blank CUSIPs is data-shaped but semantically empty, and v2 would silently swallow it. v3 counts it as a miss.

**If a contract is genuinely out of scope** (e.g. Bloomberg has dropped the basket for very old expired contracts), restrict the chain at the playbook layer via `chain_overrides` and document the deferral (P2/P5). The substrate never silently accepts a partial load.

### Amendment 8 — `include_tickers` must EXACTLY match the rolling universe (Codex finding 2, second round)

**Symptom v2:** the whitelist filter (`universe_items = [it for it in universe_items if it["ticker"] in allow]`) silently dropped any whitelist entry that did not match a universe ticker. `include_tickers: ["TY1 Comdty", "TYPO1 Comdty"]` reduced to `[TY1]` with no signal that the typo'd entry was discarded; `include_tickers: ["TYPO1 Comdty"]` reduced to `[]` and hit the "no rolling-contract tickers" path which skipped without failing.

**Fix:** new helper `_filter_universe_by_include_tickers(universe_items, include_tickers) -> (filtered, unmatched)`. The caller (`run_deliverables_extraction`) **aborts** when `unmatched` is non-empty, naming the unmatched entries. A whitelist must reference real, currently-rolling tickers — never silently typo'd.

Note: structural validation of `include_tickers` (non-empty list of non-blank strings) remains in `_resolve_deliverables_section`; the universe-cross-check moves to the filter helper because the validator has no view of the playbook universe.

### Amendment 9 — Enabled-but-malformed `deliverables:` sections must ABORT, not skip (Codex finding 3, second round)

**Symptom v2:** `_resolve_deliverables_section` returned `None` for three semantically distinct cases — absent section, disabled section, AND malformed-when-enabled section — and the caller treated all three as "skip this playbook, continue the run". Once an operator opted in via `enabled: true`, any typo (`chain_field` misspelled, `static_fields: ~`, malformed `include_tickers`) would silently skip the entire deliverables flow on that playbook.

**Fix:** `_resolve_deliverables_section` now **raises `ValueError`** with a specific message when the section is enabled but any required field is missing or any optional field is malformed. The caller wraps the call in `try / except ValueError`, prints `[ABORT] {playbook}: malformed deliverables: section — {err}`, marks the run failed, and continues to the next playbook (so a single bad playbook surfaces every misconfiguration in one pass rather than re-running once per typo).

The legitimate skip paths — absent `deliverables:` key, non-dict value, `enabled: false` — still return `None` and skip silently. Disabled means full opt-out; the body need not be well-formed.

### Amendment 10 — Pure helpers extracted from `run_deliverables_extraction` for direct unit coverage (Codex finding 4, second round)

**Symptom v2:** the v2 test file (`test_deliverables_extraction.py`) explicitly disclaimed coverage of `run_deliverables_extraction`'s orchestration body — including the audit-key suffix logic and the coverage gate. Codex (correctly) flagged that those were exactly the behaviours the substrate-level tests should pin.

**Fix:** the three pieces of logic that were embedded in the orchestration are now standalone module-level helpers, each individually unit-tested:
- `_filter_universe_by_include_tickers` — 6 tests covering the absent / matched / unmatched / all-unmatched / deterministic-ordering cases.
- `_evaluate_deliverables_coverage` — 7 tests covering clean coverage, generic gap, contract gap, the v2-regression case (1/179 contracts), truncation of long miss lists, the gate priority (generic before contract), and the degenerate empty-universe case.
- `_stamp_deliverables_audit_suffix` — 5 tests covering suffix application, lineage preservation, immutability of the input, and the missing-`playbook_name` raise.

Plus 5 SYNC-INVARIANT tests pinning that the inlined copies in `utils/historical_extractor.py` behave byte-equivalently to the canonical incremental copies. Total new tests: 22.

### Surfaces actually changed in v3

- `utils/incremental_extractor.py` and `utils/historical_extractor.py` (SYNC INVARIANT, both byte-equivalent):
  - `_resolve_deliverables_section` raises on malformed-when-enabled (mirrors event_calendar discipline).
  - Three new pure helpers: `_filter_universe_by_include_tickers`, `_evaluate_deliverables_coverage`, `_stamp_deliverables_audit_suffix`.
  - `run_deliverables_extraction` uses all three helpers; tracks `missed_contracts: List[str]`; aborts on unmatched whitelist entries; uses the unified coverage evaluator.
- `tests/state/test_deliverables_extraction.py` — rewritten to pin the v3 contracts (88 cases total across all three deliverables test files).
- `docs_revamped/05_decisions/0011-futures-deliverables-substrate.md` — this v3 amendment block.

### What v3 explicitly does NOT change

- **Schema unchanged** — `macro_data.futures_deliverables` and its natural-key UNIQUE are the v1 schema. No migration.
- **Ingester route unchanged from v2** — `_process_deliverables_blob` still calls `validate_per_contract_dates` between the ingestability gate and the FK lookup.
- **`ingestion/deliverables.py` unchanged from v2** — the per-contract date invariant check ships as v2 wrote it.

## v4 amendments (third-round Codex review, 2026-05-23)

Codex's third review identified two remaining extractor-side correctness gaps (conversion-factor and static-date NULL-leakage) and one ADR / doc gap (the YAML contract example was incomplete and the archive path was wrong). All three are real and are fixed in v4. Schema, natural key, ingester route, and `ingestion/deliverables.py` remain unchanged.

### Amendment 11 — `basket_factor_column` required + per-row factor presence check (Codex finding 1, third round)

**Symptom v3:** `basket_factor_column` was optional in the playbook section (read via `.get(...)`), and within the contract loop any row with a present CUSIP but no factor silently emitted `conversion_factor = None`. Two failure modes both reached production with NULLs:
- Operator omits / typos `basket_factor_column` → `factor_col_actual is None` → every row gets NULL.
- Operator names the column correctly but a specific row has no factor → that row gets NULL while the contract reports success.

Conversion factor is core source data — the futures invoice price uses it. Silently writing NULL corrupts every downstream RV primitive (`gross_basis`, `implied_repo_rate`, `net_basis`).

**Fix:**
- `_resolve_deliverables_section` now **requires `basket_factor_column`** when the section is enabled; raises `ValueError` otherwise.
- The new pure helper `_stage_contract_rows` (extracted from the contract loop) returns a miss reason `missing_basket_column:<col>` when the configured factor column is absent from the returned basket frame, and `missing_factor_for_cusip:<cusip>` when a row has a CUSIP but no factor. Either case marks the contract as missed and the v3 contract-level coverage gate then aborts the load.

### Amendment 12 — `static_fields` entries validated; missing static value marks contract missed (Codex finding 2, third round)

**Symptom v3:** the validator only checked `static_fields` was a non-empty list. Malformed entries (missing `column_name`, missing `bloomberg_field`, `column_name` outside the four allowed values, duplicates) were silently tolerated. At runtime, a configured static field whose Bloomberg fetch returned None would silently write `<date_column> = NULL` while reporting the contract as a success.

**Fix:**
- `_resolve_deliverables_section` validates each `static_fields` entry: dict shape, non-blank `column_name` in the allowed set `{first_delivery_date, last_delivery_date, first_notice_date, last_notice_date}` (the only typed per-contract date columns on `macro_data.futures_deliverables`), no duplicate `column_name`s, non-blank `bloomberg_field`. The allowed set is exposed as the module-level constant `_ALLOWED_DELIVERABLES_STATIC_COLUMNS` for downstream tooling.
- `_stage_contract_rows` returns a miss reason `missing_static_field:<column_name>` when any configured static field's value for the contract is None / NaN / blank-whitespace string. The v3 coverage gate aborts the load. The check is per-configured-field, so a playbook that legitimately scopes to a subset (e.g. only delivery dates, no notice dates) is unaffected — only declared fields are required.

### Amendment 13 — ADR YAML contract example completed; archive path corrected (Codex finding 3, third round)

**Symptom v1 (carried through v3):** the YAML example in §Design — extractor `--mode deliverables` listed `chain_field`, `chain_overrides`, `basket_field`, and `static_fields` but omitted `basket_cusip_column`, `basket_factor_column`, `basket_isin_column`, and `include_tickers` — all of which the code requires or supports. A reader building C4 from the ADR would have an incomplete spec. Separately, §Design — ingester route step 7 said archives land under `_archive/`, but the route actually moves blobs to `archive/deliverables/<dataset>/`.

**Fix:**
- The YAML example is rewritten to show every required and optional key the v4 validator enforces, with inline annotations for which keys are required-when-enabled vs. optional. The allowed values for `column_name` are listed explicitly.
- A "Validator behaviour summary (v4)" callout summarises the three legitimate validator outcomes (silent skip / raise / proceed) so the C4 author cannot misread the contract.
- The archive path is corrected to `archive/deliverables/<dataset>/`, matching the code and the established `archive/<phase>/` convention used by the other ingester routes.

### `_stage_contract_rows` — the new pure helper

The v3 contract loop body is now extracted as a single pure helper `_stage_contract_rows(basket_df, dates_raw, generic_ticker, contract_code, basket_cusip_column, basket_factor_column, basket_isin_column, date_field_to_column) -> (rows, miss_reason)`. It is the only place where contract-level rows are materialised, the only place where the per-row factor / per-contract static checks live, and the only place that owns the "miss reason" vocabulary. SYNC INVARIANT across both extractors. Direct unit tests (14 cases) pin every miss reason; a parity test pins byte-equivalent behaviour between `utils/incremental_extractor.py` and `utils/historical_extractor.py`.

### Surfaces actually changed in v4

- `utils/incremental_extractor.py` and `utils/historical_extractor.py` (SYNC INVARIANT):
  - `_ALLOWED_DELIVERABLES_STATIC_COLUMNS` module constant.
  - `_resolve_deliverables_section` now requires `basket_factor_column` and validates each `static_fields` entry shape / allowed-name / duplicate / blank-field.
  - `_stage_contract_rows` pure helper extracted with the full miss-reason vocabulary.
  - `run_deliverables_extraction`'s contract loop reduced to a single call to `_stage_contract_rows` + miss-tracking + extend.
- `tests/state/test_deliverables_extraction.py` — `TestResolveDeliverablesSectionV4` (12 cases) + `TestStageContractRowsHappyPath` (5 cases) + `TestStageContractRowsMissReasons` (9 cases) + `TestStageContractRowsHistoricalParity` (2 cases). Total C3 tests: **116 pass**.
- `docs_revamped/05_decisions/0011-futures-deliverables-substrate.md` — YAML example completed, validator summary added, archive path corrected, v4 amendment block.

## v5 amendment (fourth-round Codex review, 2026-05-23) — last functional blocker

### Amendment 14 — Parse-validate conversion factor and per-contract dates (Codex finding, fourth round)

**Symptom v4:** `_stage_contract_rows` caught **structural absence** (None / NaN / blank-whitespace) for both factor and static-field values, but **passed through present-but-unparseable** values:

- `Conversion Factor = "N/A"` → `_clean_scalar` passes `"N/A"` through → `factor_val is None` is False → row emitted with `conversion_factor = "N/A"` → ingester's `_num_or_none("N/A")` → `float("N/A")` raises → silently returns **None** → `futures_deliverables.conversion_factor = NULL`.
- `first_delivery_date = "not-a-date"` → similar pathway through `_to_iso_date` → `pd.to_datetime(..., errors="coerce")` → NaT → returns **None** → `futures_deliverables.first_delivery_date = NULL`.

A successful deliverables load could therefore land NULL conversion factors or NULL configured per-contract dates — exactly the corruption v4 was supposed to prevent. The verifier (a quick smoke script in this session) confirmed both pathways before the fix.

**Fix:** `_stage_contract_rows` now **parse-validates** both layers and emits canonical typed values:

- **Conversion factor (per row):** after the existing None / blank-whitespace check, attempt `float(factor_val)`. Failure → `invalid_factor_for_cusip:<cusip>`. A successfully-parsed NaN (`float("NaN")` succeeds) is rejected via the `factor_num != factor_num` self-inequality check → `invalid_factor_for_cusip:<cusip>`. The row carries the **parsed Python float** (`0.8234`), not the raw string `"0.8234"` — the ingester writes that to the `NUMERIC` column unambiguously.
- **Per-contract static dates (once per contract, pre-computed):** after the existing None / blank-whitespace check, attempt `pd.to_datetime(cleaned, errors="raise")`. Failure (e.g. `"not-a-date"`, `"TBD"`) → `invalid_static_field:<column_name>`. A parsed `NaT` is also rejected → `invalid_static_field:<column_name>`. The validated dates are stored as canonical `YYYY-MM-DD` ISO strings (via `ts.date().isoformat()`) in a `parsed_static_values: dict` BEFORE the row loop, and every emitted row of the basket reads from that dict — guaranteeing every denormalised copy is identical and ISO-formatted.

Either failure marks the contract missed via the existing v3 strict gate (`missed_contracts.append(contract_ticker)`); the load aborts with a precise diagnostic.

The miss-reason vocabulary in v5 is:

| Reason | When |
|---|---|
| `missing_basket_column:<col>` | configured CUSIP or factor column not in returned basket frame |
| `missing_factor_for_cusip:<cusip>` | factor value None / NaN / blank string for a CUSIP-present row |
| `invalid_factor_for_cusip:<cusip>` | factor value present but not parseable as a finite float |
| `missing_static_field:<col_name>` | configured static date value None / NaN / blank string |
| `invalid_static_field:<col_name>` | configured static date value present but not parseable as a date |
| `empty_basket` | basket frame had rows but every row had a blank CUSIP |

### Surfaces actually changed in v5

- `utils/incremental_extractor.py` and `utils/historical_extractor.py` (SYNC INVARIANT): `_stage_contract_rows` parse-validates factor + static dates; emits parsed Python floats and canonical ISO date strings.
- `tests/state/test_deliverables_extraction.py` — `TestStageContractRowsParseValidationV5` (11 cases) + 2 new historical-parity cases. Total C3 tests: **129 pass**.
- `docs_revamped/05_decisions/0011-futures-deliverables-substrate.md` — v5 amendment block + version log entry.

### What v5 explicitly does NOT change

- **Schema** unchanged — `macro_data.futures_deliverables` and its natural-key UNIQUE remain v1.
- **Ingester route** (`_process_deliverables_blob`, `validate_per_contract_dates`, the parser's `_to_iso_date` / `_num_or_none`) unchanged from v2 — the ingester still does defence-in-depth normalisation, but the extractor now guarantees the parquet never carries garbage.
- **v3 coverage gate** (generic + contract levels) unchanged.
- **v4 validator** (`basket_factor_column` required, `static_fields` shape + allowed-name + non-blank `bloomberg_field`) unchanged.
- **YAML contract example** unchanged.

### What v4 explicitly does NOT change

- **Schema** unchanged — `macro_data.futures_deliverables` and its natural-key UNIQUE remain v1.
- **Ingester route** (`_process_deliverables_blob` and the per-contract date validator in `ingestion/deliverables.py`) unchanged from v2 — every v4 improvement lives in the extractor layer.
- **C2a, C1** unaffected.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-23 | Initial decision. Proposed. |
| v2 | 2026-05-23 | Post-Codex amendments. Six findings reviewed; five real-bug / coverage-gap concerns fixed at the substrate layer (optional `include_tickers` whitelist on the playbook section, strict all-or-nothing coverage gate replacing the 50% threshold, `__deliverables` audit-key isolation suffix, MagicMock tests for the route + extractor section validator, per-contract date denormalisation invariant check in the ingester). One formatting hygiene finding fixed (trailing whitespace in `utils/historical_extractor.py`). Schema and natural key unchanged. |
| v3 | 2026-05-23 | Second-round Codex review amendments. Four extractor-side correctness issues fixed: contract-level all-or-nothing coverage gate (closes the "1/179 TY contracts" gap), strict exact-match for `include_tickers` against the rolling universe (closes the silent-typo gap), enabled-but-malformed `deliverables:` sections RAISE instead of skipping (closes the silent-misconfiguration gap), and orchestration logic extracted to three pure helpers (`_filter_universe_by_include_tickers`, `_evaluate_deliverables_coverage`, `_stamp_deliverables_audit_suffix`) with 22 new direct unit tests plus SYNC-INVARIANT parity tests. Schema, natural key, ingester route, and `ingestion/deliverables.py` unchanged. |
| v4 | 2026-05-23 | Third-round Codex review amendments. Two extractor-side correctness gaps closed (`basket_factor_column` required + per-row factor presence enforced; each `static_fields` entry strictly validated against the allowed `column_name` set + missing static value marks the contract missed) and one ADR / doc gap closed (YAML contract example rewritten complete, archive path corrected to `archive/deliverables/<dataset>/`). Contract-loop logic extracted to a single pure helper `_stage_contract_rows` with a documented miss-reason vocabulary; 28 new direct unit tests + SYNC-INVARIANT parity. Schema, natural key, ingester route, `ingestion/deliverables.py`, and the v3 coverage gate / `include_tickers` validator unchanged. |
| v5 | 2026-05-23 | Fourth-round Codex review amendment. **Closes the last functional blocker**: v4 caught structural absence of factor / static-date values (None / NaN / blank-whitespace) but PASSED-THROUGH present-but-unparseable values (e.g. `Conversion Factor = "N/A"`, `first_delivery_date = "not-a-date"`) on which the ingester's `_num_or_none` / `_to_iso_date` then silently produced NULL. v5 makes `_stage_contract_rows` parse-validate both: conversion factor must `float()` to a non-NaN value (else `invalid_factor_for_cusip:<cusip>`); each configured static field must `pd.to_datetime(..., errors="raise")` to a valid timestamp (else `invalid_static_field:<col_name>`). Factor is emitted as a Python `float` (not the raw value); per-contract dates are emitted as canonical `YYYY-MM-DD` ISO strings (pre-computed once per contract from the cleaned Bloomberg return). 13 new direct unit tests pin the parse-validation paths + the canonical-emission contract; 2 new SYNC-INVARIANT parity tests. Total C3 tests: **129 pass**. Schema, natural key, ingester route, `ingestion/deliverables.py`, the v3 coverage gate, the v4 column-and-static-shape validator, and the YAML contract example unchanged. |
| v5.1 | 2026-05-23 | C4-side documentation addendum from the operator's FLDS pre-flight on TYM6 / USM6P. **Documents three Bloomberg fields the operator surfaced + the v1 deferral rationale for each**: (a) `FUT_DLVRBLE_BNDS_ISINS` (FO135) — the ISIN-keyed bulk-data companion to `FUT_DLVRBLE_BNDS_CUSIPS`; v1 declines to probe it because (i) populating `deliverable_isin` from a parallel bulk-data frame requires row-order pairing across two `bds()` calls, which is unverified and could silently mis-key every ISIN if Bloomberg's two frames ever disagree on ordering, (ii) UST ISINs are deterministically derivable from CUSIPs (`US` + 9-char CUSIP + ISO 6166 check digit — confirmed by the FUT_CTD_ISIN value `US91282CQC81` matching the CTD CUSIP `91282CQC8`), and (iii) the substrate column `deliverable_isin` is nullable so v1 lands NULL with zero data loss. v2 (non-US scope: Bunds/Gilts/JGBs where ISIN is the native identifier) will add `basket_isin_column` and a row-order verification invariant in `_stage_contract_rows`. (b) `FUT_CTD_ISIN` (FO069) — the cheapest-to-deliver ISIN at the FLDS query moment; it is a time-series (changes every trading day as yields rotate) and therefore belongs in a future primitive-output table keyed `(contract, date)`, not in the static `futures_deliverables` reference table. Out of scope for C4; will be sourced by the Phase-4 `ctd_identifier` primitive when that lands. (c) `FUT_NOTICE_LAST` — does not exist on UST bond-future contracts (the FLDS "Notice" filter on TYM6 returns only `FUT_NOTICE_FIRST`, `FIRST_NOTICE_RT`, and `PS_FIRST_NOTICE_DATE`; no symmetric "last" variant). `last_notice_date` dropped from `bond_futures.yml`'s `deliverables.static_fields`; the substrate column stays nullable; v1 every row lands NULL. **Documentation-only** — no schema, code, or test change. |
| v6 | 2026-05-23 | C4 Phase A.3 amendment. **Adds the deliverable-CUSIP canonicalisation contract** based on the operator script-probe (2026-05-23 timestamp `20260523_230531`). Bloomberg's `FUT_DLVRBLE_BNDS_CUSIPS` bulk-data field returns deliverable bonds as `"{8-char CUSIP stem} Govt"` (e.g. `"9128273H Govt"`) with the 9th character (CUSIP check digit) truncated for display — confirmed empirically across all 6 UST generics and 48 sampled contracts. Storing the raw 8-char + yellow-key string in `macro_data.futures_deliverables.deliverable_cusip` would break the optional FK lookup `deliverable_cusip = instrument_master.cusip` (per ADR 0003, that column stores canonical 9-char CUSIPs). v6 closes this with: (a) a new pure helper `_canonicalize_deliverable_cusip(raw_value) -> canonical_9_char_cusip` in both extractors (SYNC INVARIANT), which strips a recognised Bloomberg yellow-key suffix (`Govt`/`Corp`/`Equity`/`Comdty`/`Mtge`/`M-Mkt`/`Index`), uppercases the stem, verifies it is exactly 8 alphanumeric (`0-9 A-Z * @ #`) characters, computes the standard CUSIP Global Services Modulus-10-Double-Add-Double check digit, and returns `stem + str(check_digit)`. Input without a yellow-key passes through (already-canonical caller input — preserves backward compatibility with the C3 test suite). Input that cannot be canonicalised raises `ValueError`, which `_stage_contract_rows` converts to the new miss reason `invalid_cusip_for_basket:<raw>` — the v3 strict gate then aborts the load. (b) `_stage_contract_rows` updated to call the canonicaliser per basket row and preserve the raw Bloomberg value in the new per-row column `raw_deliverable_bond_cusip_and_yellow_key`. The ingester parser auto-routes that column into `attributes` JSONB (no schema change needed — `_EXCLUDE_FOR_ATTRIBUTES` does not list it, and it is not a typed column), so the raw form is queryable at `attributes ->> 'raw_deliverable_bond_cusip_and_yellow_key'` for audit. (c) Also fixes the verification script's stale "all-4-dates" log text (introduced before v1 dropped FUT_NOTICE_LAST) — now dynamically reflects the count of `STATIC_FIELDS`. **P12 justification:** this is identifier canonicalisation, not market-data recomputation. Bloomberg internally stores canonical 9-char CUSIPs; the bulk-data viewer truncates the 9th character purely as a display convention because the (8-char stem, yellow-key) tuple resolves unambiguously to one Bloomberg security. The check-digit algorithm is the published CGS standard, deterministic, and reconstructs the identifier Bloomberg already knows about — analogous to normalising a returned date string to ISO YYYY-MM-DD form, not to deriving a price or yield. **22 new tests** (`TestCanonicalizeDeliverableCusip` + `TestStageContractRowsCusipCanonicalisationV6` + `TestCanonicalizeDeliverableCusipHistoricalParity`), including yellow-key stripping for all 7 documented BBG yellow keys, the actual six basket-row samples seen in the operator probe (TU/FV/TY/UXY/US/WN — each canonical 9-char form independently verifiable against real Treasury CUSIPs), the passthrough branch, fail-closed paths (None / blank / wrong-length stem / invalid character), and end-to-end staging-helper integration verifying the natural-key position carries the canonical form and the raw value lands in attributes. Total C3+C4 deliverables tests: **152 pass**. Schema, natural key, ingester route, ingester parser (`ingestion/deliverables.py`), the v3 coverage gate, v4 validator, v5 parse-validation, and YAML contract example unchanged. **Still pending Phase B (separate commit):** the chain-cutoff mitigation (Bloomberg `DT_OVERRIDE_START` chain-side override if honored — zero code; else a `chain_max_length` operator-controlled cap as a small v6.1 amendment). The script-probe revealed Bloomberg drops basket reference data for the oldest contracts of each generic (~7-10 year gap behind each chain start). Without a cap, the production extractor's strict contract-level gate would abort on ~136 missed contracts across the four older generics (TU/FV/TY/US); UXY/WN are clean. |
