# ADR 0001 — Sibling SCD2 `instrument_metadata_history` for rolling-contract tickers

**Status:** Accepted
**Date:** 2026-05-19
**Closes:** [`technical_debt.md`](../../docs/technical_debt.md) item **#2**.
**Operationalises principles:** P1 (built right), P3 (one shape for metadata), P4 (point-in-time replayability), P5 (no silent stale-data answers), P10 (one canonical history table).
**Scope:** Step 0.1 of the data-first roadmap in `tmp/primitive_expansion/`.

---

## Context

`macro_data.instrument_master` has a unique key on `(vendor, vendor_ticker)`. For **rolling-generic tickers** — TY1, FV1, TU1, UXY1, US1, WN1, RX1, OE1, …, SFR1-8, ER1-8, SFI1-8 (≈44 today) — the same `vendor_ticker` represents a *different underlying contract* at different points in time:

- `TY1 Comdty` today is the 10Y Treasury Note future for the next-to-deliver listed cycle. Six months ago it was a different cycle with a different expiry, accrual window, security name, and (where ingested) contract size.
- The current ingester (`utils/historical_extractor.py` + `ingestion/ingest_parquet.py`) writes Bloomberg `bdp()` reference fields onto every daily row in the parquet. `_normalize_instrument_record` routes the typed cols (`expiry_date`, `maturity_date`, `contract_code`) into `instrument_master`; everything else lands in `attributes` JSONB. The `upsert_instrument_master` call uses `ON CONFLICT (vendor, vendor_ticker) DO UPDATE`, which **overwrites those reference fields** with the latest seen values on every run.
- Consequence: a `JOIN market_data_daily d ⋈ instrument_master i` for a 2023-06-15 row on `TY1` returns *today's* contract metadata, not the metadata that was true on 2023-06-15.

Today's primitives (sovereign + OIS + linkers + inflation swaps) do not depend on rolling-contract metadata for correctness — only the price/yield/OI time series themselves matter. **Every Phase 2+ futures primitive does**: `futures_strip_snapshot`, `futures_pack_average_simple`, `ctd_identifier`, basis / DV01 stack, all need the right per-day expiry, accrual, conversion factor.

This ADR is the substrate decision that unblocks all of that.

## Decision

**Add a sibling SCD2 table `macro_data.instrument_metadata_history`** that captures effective-dated metadata windows for rolling-contract tickers. `instrument_master` keeps its current shape and continues to hold the **current** state per ticker (this is what `bdp()` returns and what `upsert_instrument_master` writes today). The history table holds the *prior* effective windows and the *current* window with `effective_to = NULL`.

Reads route through `v_market_data_daily_enriched`, which is updated to overlay history on master via `COALESCE`. **Today, before the Step-1.1 backfill runs, the history table is empty and the view returns byte-identical output** — pure backwards-compat by construction.

### What this ADR explicitly does NOT do

- **Does not modify `instrument_master`'s schema.** No new columns, no SCD2 columns on the existing table.
- **Does not modify `upsert_instrument_master` or `ingest_parquet.py`.** The live ingestion path is unchanged. History rows are written by explicit callers — the Step 1.1 backfill script and any later live-ingester wiring (separate PR, separate scope).
- **Does not change the column shape of `v_market_data_daily_enriched`.** Same 21 columns, same types, same order; only `contract_code`, `expiry_date`, `maturity_date` now overlay history where present.
- **Does not touch `panel_assembly.fetch_instrument_panel` or `financing.fetch_overnight_index_series`.** Both already read the view; both inherit the overlay automatically. Their leg-spec contract stays 3-tuple — the bond-futures-panel extension (4-tuple with `contract_code`) is deferred to Phase 2 when the consuming primitive exists.

## Alternatives considered

**Option A (chosen) — Sibling history table; view-level COALESCE overlay; `instrument_master` unchanged.**
- ✅ Zero behavior change for existing fetchers (they already read the view).
- ✅ Empty history = byte-identical output → safe to land before backfill.
- ✅ Migration is additive only: `CREATE TABLE IF NOT EXISTS` + `CREATE OR REPLACE VIEW`. No `ALTER TABLE` on data tables. No risk of data loss.
- ✅ Per-rolling-ticker scope; non-rolling tickers cost nothing extra (the LATERAL short-circuits on `is_rolling_contract = false`).
- ⚠️ Reads on rolling rows pay an extra `O(log N)` index lookup per row. Acceptable today; if it becomes a hotspot, the view can be materialized (covers [`technical_debt.md`](../../docs/technical_debt.md) item #17).

**Option B — SCD2 in-place on `instrument_master` (add `effective_from / effective_to` columns; expand the unique key).**
- ❌ Changes the primary identity of `instrument_master` rows; every JOIN in the codebase touching `instrument_master` would need rethinking.
- ❌ `upsert_instrument_master`'s `ON CONFLICT (vendor, vendor_ticker)` would break — every ingestion would insert a new row instead of upserting, requiring a major rewrite of the ingester.
- ❌ Non-rolling tickers would still gain SCD2 columns they don't need.
- ❌ The `attributes` GIN index and the `idx_instrument_master_lookup` index would need rebuilds.

**Option C — Stamp metadata onto `market_data_daily` directly (one row per (date, instrument, field, …with metadata cols)).**
- ❌ Massively duplicates static metadata across 5000+ days per instrument.
- ❌ Conflicts with the long-format design of `market_data_daily`.
- ❌ Hot path for time-series queries gets wider rows for zero query benefit.

Option A wins on every axis except the marginal read-cost concern, which has a known follow-up path.

## Design — schema

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- needed by EXCLUDE below

CREATE TABLE IF NOT EXISTS macro_data.instrument_metadata_history (
    metadata_history_id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES macro_data.instrument_master(instrument_id),
    effective_from DATE NOT NULL,
    effective_to   DATE,                             -- NULL = currently in effect
    -- Mirrors of instrument_master's rolling-sensitive typed cols:
    contract_code  VARCHAR(32),
    expiry_date    DATE,
    maturity_date  DATE,
    -- Reference fields that today live in attributes JSONB but are needed
    -- per-effective-window for Phase 2-4 futures primitives:
    security_name      VARCHAR(256),
    settlement_date    DATE,
    accrual_start_date DATE,
    accrual_end_date   DATE,
    tick_size      NUMERIC(20, 10),
    tick_value     NUMERIC(20, 10),
    contract_size  NUMERIC(20, 4),
    exchange_code  VARCHAR(32),
    underlying_ticker VARCHAR(128),
    attributes JSONB,                                -- per-window escape hatch
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instrument_metadata_history_window
        UNIQUE (instrument_id, effective_from),
    CONSTRAINT ck_instrument_metadata_history_window
        CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- Hard reject overlapping windows per instrument at write time. The view's
    -- LATERAL ORDER BY effective_from DESC LIMIT 1 silently picks the latest
    -- matching row when windows overlap — this EXCLUDE stops bad writes from
    -- ever producing that ambiguity. Adjacent windows that share a boundary
    -- day are also rejected (bounds = '[]' inclusive), matching the close
    -- semantics of close_open_metadata_window() (new effective_from − 1).
    CONSTRAINT ex_instrument_metadata_history_no_overlap
        EXCLUDE USING GIST (
            instrument_id WITH =,
            daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]') WITH &&
        )
);
```

Indices:
- `idx_instrument_metadata_history_pit` on `(instrument_id, effective_from DESC)` — supports the LATERAL point-in-time lookup with one index hit.
- `idx_instrument_metadata_history_attributes` on `(attributes)` USING GIN — same shape as the master table's index.

The EXCLUDE constraint additionally creates an implicit GIST index that supports both the equality and range-overlap predicates; we do NOT add a separate `idx_..._range` because the EXCLUDE-backing index already serves that role.

## Design — view overlay

`v_market_data_daily_enriched` is recreated with a `LEFT JOIN LATERAL` against history, guarded by `i.is_rolling_contract = true` so non-rolling rows skip the lookup:

```sql
CREATE OR REPLACE VIEW macro_data.v_market_data_daily_enriched AS
SELECT
    d.trade_date,
    d.instrument_id,
    i.vendor, i.vendor_ticker, i.asset_class, i.instrument_type,
    i.curve_family, i.country, i.currency, i.tenor, i.underlying_index,
    COALESCE(hist.contract_code,  i.contract_code)  AS contract_code,
    COALESCE(hist.expiry_date,    i.expiry_date)    AS expiry_date,
    COALESCE(hist.maturity_date,  i.maturity_date)  AS maturity_date,
    i.is_rolling_contract, i.is_active,
    d.field_name, d.field_value, d.load_id, d.created_at, i.attributes
FROM macro_data.market_data_daily d
JOIN macro_data.instrument_master i
  ON d.instrument_id = i.instrument_id
LEFT JOIN LATERAL (
    SELECT h.contract_code, h.expiry_date, h.maturity_date
    FROM macro_data.instrument_metadata_history h
    WHERE i.is_rolling_contract = true
      AND h.instrument_id = d.instrument_id
      AND h.effective_from <= d.trade_date
      AND (h.effective_to IS NULL OR h.effective_to >= d.trade_date)
    ORDER BY h.effective_from DESC
    LIMIT 1
) hist ON true;
```

Column shape: **unchanged**. Output today (empty history): **byte-identical to pre-Step-0**.

### Which metadata fields read through which path — explicit policy

The view's COALESCE deliberately overlays only the **three rolling-sensitive typed columns** that already exist on `instrument_master`. The history table holds more fields (`security_name`, `tick_size`, accrual window, contract size, etc.), but those are NOT promoted into the view's column shape. The rationale: keeping the view's 21-column shape and types byte-identical preserves backwards-compat for every existing caller; tools that need the history-only fields opt in via the Python getter.

| Field | Where reads go today | Where reads should go after Step 0 (rolling tickers) | Where reads should go after Step 0 (non-rolling) |
|---|---|---|---|
| `contract_code` | view (`i.contract_code`) | **view (overlay)** | view (`i.contract_code`, fall-through) |
| `expiry_date` | view (`i.expiry_date`) | **view (overlay)** | view (`i.expiry_date`, fall-through) |
| `maturity_date` | view (`i.maturity_date`) | **view (overlay)** | view (`i.maturity_date`, fall-through) |
| `security_name` | `i.attributes->>'security_name'` (stale per-rolling-ticker) | **`get_instrument_metadata_at(...)`** (typed) | `i.attributes->>'security_name'` (unchanged) |
| `tick_size`, `tick_value`, `contract_size` | not ingested today | **`get_instrument_metadata_at(...)`** (typed, post-Step-1.1 backfill) | not applicable |
| `settlement_date`, `accrual_start_date`, `accrual_end_date` | not ingested today | **`get_instrument_metadata_at(...)`** (typed, post-Step-1.1 backfill) | not applicable |
| `exchange_code`, `underlying_ticker` | `i.attributes->>...` (best-effort) | **`get_instrument_metadata_at(...)`** (typed, post-Step-1.1 backfill) | `i.attributes->>...` (unchanged) |
| `delivery_month_type` (Step 1.2 — policy_futures only) | `i.attributes->>'delivery_month_type'` | `i.attributes->>'delivery_month_type'` — structural per-ticker, **NOT** effective-dated; lives on master | n/a |
| `attributes` JSONB (other keys) | `i.attributes` | `i.attributes` (master, unchanged); per-window keys read via `get_instrument_metadata_at().history_attributes` | `i.attributes` (unchanged) |

**Attributes merge policy.** The view exposes only `i.attributes` (master). It does NOT COALESCE or merge `hist.attributes`. Two reasons: (a) the merge semantics depend on the use case — `COALESCE` picks one or the other entirely, while `||` concatenates with right-hand-side override on key collision, and there is no single right answer at the substrate level; (b) keeping the view's `attributes` column identical to pre-Step-0 preserves byte-identical output for every existing caller. **Python callers that need per-window JSONB read `get_instrument_metadata_at(...)` and apply whatever merge policy fits — typically: `{**master_attributes, **history_attributes}` so history-window keys override master.**

This split is the right Step-0 trade-off: tighten the view for the three fields that broke under TD#2 (which is what every existing caller already reads), and keep new history-only metadata behind an opt-in Python getter so the view's contract stays stable. A future ADR can promote history-only fields into the view if a downstream pattern proves common.

## Design — DB helpers (in `database/database.py`)

Three new helpers, all additive:

- `upsert_instrument_metadata_history(connectable, records)` — append-on-change writer; `ON CONFLICT (instrument_id, effective_from) DO UPDATE` for idempotent re-runs.
- `close_open_metadata_window(connectable, instrument_id, new_effective_from)` — closes the prior open row by setting its `effective_to = new.effective_from - 1`. Used by the backfill / live append paths to keep windows non-overlapping.
- `get_instrument_metadata_at(engine, instrument_id, as_of_date)` — point-in-time getter that overlays history on master, mirroring the view's COALESCE semantics for Python callers that need a single typed row (not a long-format view query).

`upsert_instrument_master` is **not** modified. The live ingester (`ingest_parquet.py`) is **not** modified.

## TD#11 — Bond-futures lookup disambiguation (Step 0.2/0.3)

Separate from TD#2, but lands in the same Step 0. `bond_futures.yml` already carries `contract_code` and `bucket_label` per row (`TY1`/`UXY1` are distinct on `bucket_label=10Y_CLASSIC`/`10Y_ULTRA` and `contract_code=TY1`/`UXY1`). The YAML data is fine; the gap is on the **tool fetcher** side — today's helpers look up by `(curve_family, tenor)` which is ambiguous for `(UST_FUT, 10Y)`.

Fix: add an **optional** `contract_code: Optional[str] = None` kwarg to the four single-/group-/pair-style fetchers in `shared/analytics/rates_fetch.py`. Default `None` preserves today's behavior exactly. When the caller passes a `contract_code`, the SQL gains an `AND contract_code = :contract_code` clause that disambiguates among rows sharing the same `(curve_family, tenor)`. Existing sovereign/OIS callers pass nothing → no behavior change. Future bond-future fetchers pass the contract code.

`fetch_scan_universe` additionally gains `contract_code` in its SELECT output — purely additive (existing callers use named-column access).

`fetch_instrument_panel` (4-tuple leg-spec extension) and `fetch_overnight_index_series` (non-rolling, single overnight tenor) are not touched in Step 0.

## Consequences

**Positive:**
- All existing fetchers transparently gain per-day-correct rolling metadata after the Step-1.1 backfill — zero call-site change.
- Step-1.1 backfill becomes a pure data-population task (no schema work, no view rework).
- New futures primitives in Phase 2 onward can read correct expiry/accrual/tick info per `trade_date` from day one.
- `get_instrument_metadata_at` gives Python callers a clean point-in-time API for cases the view shape doesn't fit (e.g., one-row metadata cards on UI).

**Negative / known trade-offs:**
- Rolling-row reads pay one extra index lookup per row. Acceptable; mitigation path is materialised view ([`technical_debt.md`](../../docs/technical_debt.md) #17).
- A second source of metadata truth (master vs history). Mitigated by: master = "current"; history = "effective-dated"; the COALESCE in the view + `get_instrument_metadata_at` make the union policy explicit and centrally defined.
- The live ingester still overwrites `instrument_master` reference cols on each run. That is intentional in Step 0 — `instrument_master` is the *current* state; effective-dated capture is the backfill's job (Step 1.1) and will be wired into the live path in a subsequent PR with explicit append-on-change semantics.

## Rollout plan

1. **This PR (Step 0):**
   - Land schema additions in `database/schema.sql` for future fresh setups: `CREATE EXTENSION IF NOT EXISTS btree_gist` (required by EXCLUDE), `CREATE TABLE IF NOT EXISTS instrument_metadata_history` (with the EXCLUDE non-overlap constraint), the two GIN/pit indices, and `CREATE OR REPLACE VIEW` for `v_market_data_daily_enriched`.
   - Land matching `ALTER`/`CREATE` commands for existing DBs (provided in the PR description).
   - Land DB helpers in `database/database.py`. `_normalize_history_record` writes uniform-key dicts (every history column present, `None` defaults) so SQLAlchemy bulk insert generates a single coherent column list regardless of which optional fields each record carries.
   - Land `contract_code` kwarg in the four `rates_fetch.py` fetchers + `contract_code` in `fetch_scan_universe` SELECT.
2. **Step 1.1 (separate PR):** Backfill script that pulls Bloomberg generic-ticker-day history for all 44 rolling tickers and writes effective-dated rows via `upsert_instrument_metadata_history` + `close_open_metadata_window`. The backfill MUST include a pre-write validation step that detects overlapping or boundary-sharing windows per instrument before attempting the insert — if any input violates the EXCLUDE constraint, the bulk insert aborts the whole batch, so the validator catches and reports them cleanly. Test plan: (a) empty-history → view row-count unchanged; (b) one synthetic history row overlays a historical trade_date; (c) overlapping-window input is rejected (positive integrity test); (d) bond-future contract_code disambiguation returns a single row for `(UST_FUT, 10Y, contract_code='TY1')` and `(UST_FUT, 10Y, contract_code='UXY1')`.
3. **Step 1.2:** `delivery_month_type: quarterly | serial` annotation on `policy_futures.yml`. Note: this is a structural per-ticker attribute, NOT effective-dated — it lives on `instrument_master.attributes` and reads through the view's `i.attributes` column unchanged, NOT through the history table.
4. **Step 1.3:** Verification queries via the updated view + `get_instrument_metadata_at`.
5. **Future:** Wire `ingest_parquet.py` to call `upsert_instrument_metadata_history` automatically on each ingestion for rolling tickers — but only after the backfill is in and verified, so the live path can rely on history being non-empty. The live-path wiring will use `close_open_metadata_window(instrument_id, new_effective_from=trade_date)` before any `upsert_instrument_metadata_history` so the EXCLUDE constraint is satisfied (no overlapping windows possible).

## Verification

After this PR is merged and the migration is applied:

1. `SELECT count(*) FROM macro_data.instrument_metadata_history;` → 0 (empty until Step 1.1).
2. `SELECT trade_date, contract_code, expiry_date FROM macro_data.v_market_data_daily_enriched WHERE vendor_ticker = 'TY1 Comdty' ORDER BY trade_date DESC LIMIT 5;` → returns *current* `instrument_master` values (unchanged from pre-Step-0).
3. Insert a synthetic history row for one rolling ticker; re-run query 2 for a `trade_date` inside its window → returns the history row's metadata (the COALESCE overlay activates).
4. `SELECT count(*) FROM macro_data.v_market_data_daily_enriched WHERE trade_date >= '2025-01-01';` → identical row count to pre-migration (the LATERAL adds no rows; it only overlays columns).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-19 | Initial decision. Accepted. |
