# ADR 0003 — Cash-bond substrate: CUSIP/ISIN identity + on-the-run history

**Status:** Accepted
**Date:** 2026-05-20
**Closes:** none directly. Unblocks [`docs/technical_debt.md`](../../docs/technical_debt.md) item **#24** (`carry_and_roll` — deferred for lack of per-bond cash-bond data). Builds on ADR 0001 (the SCD2 + `EXCLUDE USING GIST` pattern this ADR reuses for `otr_history`).
**Operationalises principles:** P1 (built right — full substrate, no placeholder), P3 (consistency by contract — `otr_history` reuses the Step-0 SCD2 + EXCLUDE pattern; the new helpers mirror the metadata-history helper trio), P8 (closed-family discipline — `asset_class` is **not** extended; this ADR documents that decision explicitly), P10 (single source of truth — `database/schema.sql` owns `macro_data`; the playbook contract README is updated to match).
**Scope:** Step 2 of the data-first roadmap, Track A — **infrastructure only.** This ADR is PR A3. It does NOT verify or commit to any Bloomberg mnemonic, write any playbook, or ingest any data. The corresponding data PR (A4 — `sovereign_cash_bonds.yml` + extraction + ingestion) is explicitly out of scope and follows separately.

---

## Context

The data-first roadmap's Track A delivers tradable-instrument data in four steps: A1 (futures-metadata readiness), A2 (futures-metadata data run), **A3 (cash-bond substrate — this ADR)**, A4 (cash-bond data). Step 2 of the roadmap calls for ingesting **individual cash sovereign bonds** at CUSIP level — coupon, issue date, maturity, amount outstanding, plus daily yield / dirty price / modified duration / DV01 / accrued interest / bid-ask — and tracking which bond is **on-the-run (OTR)** for each `(country, tenor)` slot over time.

Two facts shape the substrate question:

1. **The daily cash-bond fields need no schema change.** `macro_data.market_data_daily` is long-format keyed by `(trade_date, instrument_id, field_name)`, and `field_name` is a free `VARCHAR(64)` — the vendor mnemonic itself. A cash bond's daily `YAS_BOND_YLD`, `MOD_DUR_MID`, `PX_DIRTY`, etc. are ordinary `market_data_daily` rows. No new columns, no new table.

2. **What is genuinely missing is two things:**
   - **Cash-bond identity.** `instrument_master` today carries no CUSIP or ISIN. Its natural key is `(vendor, vendor_ticker)`. The existing sovereign data (`sovereign_bonds.yml`) is benchmark-*curve-point* data only — `GT10 Govt`-style generic tickers — never an individual bond with a CUSIP. Cash-bond primitives (RV screens, basis, CTD work) look bonds up by CUSIP; the substrate must give CUSIP a first-class home.
   - **On-the-run history.** A freshly-auctioned 10Y UST is on-the-run for roughly three months until the next 10Y is auctioned, then becomes off-the-run permanently. OTR status is therefore a **state of a `(country, tenor)` slot over time**, not a static instrument attribute. The desk wants `otr_ofr_spread` (OTR-vs-off-the-run yield spread) and a point-in-time lookup — *which CUSIP was the OTR 10Y on 2023-06-15?* Neither is answerable today.

`carry_and_roll` ([`docs/technical_debt.md`](../../docs/technical_debt.md) #24) is the canonical consumer waiting on this: it needs per-bond modified duration, coupon, day-count and accrued interest, and was deferred precisely because the sovereign-benchmark playbook ingests only `YLD_YTM_MID` + `maturity_date` + `security_name`. A3 is the substrate that unblocks A4, which unblocks `carry_and_roll`.

## Decision

A3 lands five coordinated, **purely additive** substrate changes:

1. **A new `instrument_type` value: `sovereign_cash_bond`.** `instrument_type` is a free `VARCHAR(64)` on `instrument_master` (not a closed family in code — see P8 note below), so this is a documented convention, not a schema change. `asset_class` stays `rates`.

2. **Promote `cusip` and `isin` to typed columns on `instrument_master`.** Two new nullable `VARCHAR(16)` columns. `coupon`, `issue_date` and `outstanding` stay in the `attributes` JSONB. Rationale: the playbook contract's rule is *query-hot fields get typed columns, the rest stay in `attributes`* — CUSIP and ISIN are the lookup/join keys cash-bond primitives filter on; coupon / issue-date / outstanding are compute *inputs* (read, not filtered), so JSONB is the correct home until a query pattern proves otherwise.

3. **A new SCD2 table `macro_data.otr_history`** keyed on `(country, tenor, effective_from)`, with an `EXCLUDE USING GIST` constraint that rejects overlapping or boundary-sharing windows per `(country, tenor)` — the same pattern ADR 0001 shipped for `instrument_metadata_history`, re-keyed from `instrument_id` to the `(country, tenor)` slot. Each row points (`otr_instrument_id` FK) to the `instrument_master` row that was OTR during that window.

4. **DB helpers** in `database/database.py`: `_normalize_instrument_record` and `upsert_instrument_master` extended to map the new `cusip` / `isin` columns; a new helper trio `upsert_otr_history` / `close_open_otr_window` / `get_otr_at` (plus the pure-Python `_normalize_otr_record`) mirroring the metadata-history trio and the `_txn`-Connectable contract.

5. **Ingester** (`ingestion/ingest_parquet.py`): `cusip` / `isin` added to the per-instrument record dict and to the `_build_instrument_attributes` exclude set, so a future cash-bond parquet routes them to the typed columns rather than double-writing into JSONB.

The playbook contract README is bumped (v1.2 → v1.3) to record the new `instrument_type` value and the two new typed columns.

### What this ADR explicitly does NOT do

- **Does not write `sovereign_cash_bonds.yml` or edit `sovereign_bonds.yml`.** Playbooks are A4.
- **Does not claim any Bloomberg mnemonic is verified, run any extraction, or ingest any data.** A3 is substrate; A4 is data.
- **Does not populate `otr_history`.** A3 lands the OTR *schema and helpers*; A4+ populates the windows.
- **Does not extend the `asset_class` closed family (P8).** Cash bonds are `rates`. A new `asset_class` value would be an ADR-gated closed-family extension; cash bonds do not need one and this ADR deliberately does not propose it.
- **Does not change the column shape of `v_market_data_daily_enriched`.** `cusip` / `isin` are read from `instrument_master` directly (or a future fetcher), exactly as ADR 0001 kept history-only typed columns out of the view to preserve its stable contract. Promoting them into the view is a future decision if a read pattern proves common.
- **Does not modify `market_data_daily` or its hypertable.** Cash-bond daily fields are ordinary long-format rows.
- **Does not ship an OTR pre-write Python validator.** The DB-level `EXCLUDE` constraint is the enforcement. A4 adds a defence-in-depth validator when it builds the OTR ingestion path, mirroring how ADR 0002 added `validate_no_overlaps` alongside the metadata-history extractor/ingester.
- **Does not touch Alembic.** `macro_data` is owned by `database/schema.sql`; Alembic manages only `copilot_state`.

## Alternatives considered

### Cash-bond identity — typed columns vs. JSONB

**Option A (chosen) — promote `cusip` + `isin`; keep `coupon` / `issue_date` / `outstanding` in `attributes`.** CUSIP and ISIN are the natural keys a cash-bond lookup filters or joins on; they earn typed columns under the playbook-contract rule. The other three are read by `carry_and_roll`-style consumers as compute inputs but are not filter/join keys — JSONB is correct and reversible (a later ADR can promote any of them if a query pattern emerges).

**Option B — promote only `cusip`.** Rejected: ISIN is the primary identifier outside the US (European sovereigns quote ISIN, not CUSIP); forcing ISIN lookups through the JSONB GIN index when it is a first-class identifier for a whole region is a false economy.

**Option C — promote all five.** Rejected: `coupon` / `issue_date` / `outstanding` are not query-hot; typed columns for fields that are only ever read (never filtered) add schema surface for no query benefit and pre-empt a decision that costs nothing to defer.

### OTR history — the mechanism

**Option (a) — add `is_on_the_run BOOLEAN` to `instrument_metadata_history`.** Rejected. Three failures: (1) that table's `EXCLUDE` constraint is keyed on `instrument_id`, so it cannot enforce the **at-most-one-OTR-per-slot** invariant — two different CUSIPs could both carry `is_on_the_run = TRUE` for the same `(country, tenor)` on the same day with nothing to stop it. (2) The enriched view's history overlay is guarded by `is_rolling_contract = TRUE`; cash bonds are individual fixed instruments (`is_rolling_contract = FALSE`), so the overlay would never fire for them. (3) Conceptually the table holds *per-instrument metadata that varies per effective window* (a futures generic's underlying contract); a cash bond's own metadata does not vary — only the slot's OTR pointer does. Wrong table.

**Option (b) — model each OTR slot as a synthetic rolling generic.** One `instrument_master` row per `(country, tenor)` slot with `is_rolling_contract = TRUE`, whose `instrument_metadata_history` rows point at the OTR CUSIP. This reuses the Step-0 substrate including the view overlay, and is structurally the rolling-generic pattern (`TY1` → underlying contract). Rejected on cost: it requires *either* inventing synthetic non-Bloomberg tickers in `instrument_master` (a new identity convention, with an awkward `vendor`), *or* flipping `is_rolling_contract = TRUE` on the ~88 existing sovereign-benchmark rows — which silently changes the enriched view's overlay behaviour for every benchmark row and is, in any case, a data/ingestion decision that belongs to A4, not a substrate decision A3 can make cleanly.

**Option (c) (chosen) — a dedicated `macro_data.otr_history` table.** Keyed on `(country, tenor, effective_from)` with an `EXCLUDE USING GIST (country WITH =, tenor WITH =, daterange(...) WITH &&)` constraint. This is the only option that **DB-enforces the real invariant** — at most one OTR window per slot covers any given date. It is purpose-built and honest: `get_otr_at(country, tenor, date)` is a clean indexed single-table lookup; `otr_ofr_spread` resolves its OTR leg through that lookup. It overloads nothing, needs no synthetic tickers, and mutates no existing instrument. The "third SCD2-ish table" cost is real but small: it is the *same* SCD2 + `EXCLUDE` pattern from ADR 0001, re-keyed on the `(country, tenor)` slot — a consistent reuse under P3, not a new shape. The `btree_gist` extension the `EXCLUDE` needs is already installed (Step 0).

**Option (d) — model OTR transitions as rows in B1's `event_calendar`.** Rejected. An OTR transition (a new auction settling) is a discrete event, but *OTR status* is a **state that persists between transitions**. Answering "which CUSIP was OTR on 2023-06-15" from an event log means scanning for the most recent transition before that date and replaying — exactly the work an SCD2 table with effective windows does natively. `event_calendar` is the right home for the auction *event*; it is the wrong home for the *resulting state over time*.

## Design — schema (`database/schema.sql`)

### `instrument_master` — two new typed columns

```sql
cusip VARCHAR(16),   -- cash-bond identity (ADR 0003); NULL for non-cash-bond instruments
isin  VARCHAR(16),   -- cash-bond identity (ADR 0003); NULL for non-cash-bond instruments
```

Both nullable, so every existing row (futures, OIS, benchmarks, linkers — none of which carry a CUSIP) is unaffected.

```sql
-- A CUSIP uniquely identifies one cash bond. Partial (WHERE cusip IS NOT NULL)
-- so the majority of instruments, which have no CUSIP, are not collapsed onto
-- a single NULL value by the uniqueness rule.
CREATE UNIQUE INDEX IF NOT EXISTS uq_instrument_master_cusip
    ON macro_data.instrument_master (cusip) WHERE cusip IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_instrument_master_isin
    ON macro_data.instrument_master (isin) WHERE isin IS NOT NULL;
```

### `macro_data.otr_history` — new SCD2 table

```sql
CREATE TABLE IF NOT EXISTS macro_data.otr_history (
    otr_history_id BIGSERIAL PRIMARY KEY,
    country VARCHAR(16) NOT NULL,
    tenor   VARCHAR(16) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to   DATE,                              -- NULL = currently on-the-run
    otr_instrument_id BIGINT NOT NULL
        REFERENCES macro_data.instrument_master(instrument_id),
    attributes JSONB,                                 -- per-window escape hatch
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_otr_history_window
        UNIQUE (country, tenor, effective_from),
    CONSTRAINT ck_otr_history_window
        CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- Reject overlapping OTR windows per (country, tenor) slot at write time —
    -- AT MOST ONE bond is recorded as on-the-run per slot per date. The
    -- constraint does NOT enforce gapless coverage; see "Gapless coverage"
    -- below. Adjacent windows sharing a boundary day are also rejected
    -- (bounds '[]'), matching close_open_otr_window()'s "new − 1" close.
    CONSTRAINT ex_otr_history_no_overlap
        EXCLUDE USING GIST (
            country WITH =,
            tenor   WITH =,
            daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]') WITH &&
        )
);
```

Indices:
- `idx_otr_history_pit` on `(country, tenor, effective_from DESC)` — the point-in-time slot lookup, one index hit.
- `idx_otr_history_instrument` on `(otr_instrument_id)` — the reverse lookup ("which window(s) was this bond OTR for").
- `idx_otr_history_attributes` GIN on `(attributes)` — same shape as the sibling SCD2 table.

The `EXCLUDE`-backing GIST index additionally serves the slot+range predicate; no separate range index is added.

### Gapless coverage — an A4 responsibility, not a DB constraint

The `EXCLUDE` constraint guarantees **at most one** OTR window covers any date for a slot — it rejects overlapping and boundary-sharing windows. It does **not** guarantee **gapless** coverage — that *some* bond is on-the-run on *every* date. A gap between a closed window's `effective_to` and the next window's `effective_from` is structurally permitted by the table; Postgres `EXCLUDE` can reject overlaps, it cannot require coverage. In correct data there are no gaps (the prior on-the-run bond stays OTR until the next one settles), and `close_open_otr_window` produces gapless adjacency when used before each insert (`effective_to = new_effective_from − 1`). The A4 OTR loader is responsible for producing gapless windows and SHOULD validate no-gaps as a defence-in-depth check — the same way ADR 0002's extractor/ingester run `validate_no_overlaps` alongside the DB constraint. "Exactly one bond is on-the-run per slot per date" is the real-world fact the *data* should reflect; the *constraint* enforces the no-overlap half of it.

## Design — DB helpers (`database/database.py`)

All additive; all follow the `_txn`-Connectable contract (accept an `Engine` for a self-managed transaction or a `Connection` for caller-managed composition).

- **`_normalize_instrument_record`** — gains `cusip` and `isin` in the normalised dict (`rec.get(...)`, default `None`). **`upsert_instrument_master`** — gains `cusip` / `isin` in the `ON CONFLICT DO UPDATE` set.
- **`_normalize_otr_record(record)`** — pure-Python; validates `country` / `tenor` / `effective_from` / `otr_instrument_id` are present and returns a uniform-key dict (every `otr_history` value column present, `None` default) so SQLAlchemy bulk insert generates one coherent column list. Mirrors `_normalize_history_record`.
- **`upsert_otr_history(connectable, records)`** — `INSERT … ON CONFLICT (country, tenor, effective_from) DO UPDATE`; idempotent re-runs. Mirrors `upsert_instrument_metadata_history`.
- **`close_open_otr_window(connectable, country, tenor, new_effective_from)`** — closes the prior open window for a slot by setting `effective_to = new_effective_from − 1`. Mirrors `close_open_metadata_window`.
- **`get_otr_at(engine, country, tenor, as_of_date)`** — point-in-time getter; returns the OTR window in effect on `as_of_date` joined to the OTR bond's `instrument_master` identity (`instrument_id`, `vendor_ticker`, `cusip`, `isin`, `maturity_date`). Returns `None` (typed `Optional`, per P6) when no window covers the date. Mirrors `get_instrument_metadata_at`.

## Design — ingester (`ingestion/ingest_parquet.py`)

The PHASE-2 per-instrument record dict gains `cusip` / `isin` keys (read from the parquet row, `None` when absent), and `_build_instrument_attributes`'s exclude set gains both names so they are not *also* written into `attributes`. Purely additive; the time-series path is unchanged for every existing playbook (none of which carry a `cusip` / `isin` column).

## Consequences

**Positive:**
- A4 becomes a pure data-population task — write `sovereign_cash_bonds.yml`, extract, ingest. No schema work, no helper work.
- `carry_and_roll` and the broader cash-bond RV stack (TD #24) are unblocked at the substrate level.
- The no-overlap invariant is enforced by the database, not by application discipline — an OTR backfill that double-books a slot fails loudly at write time. (Gaplessness is not DB-enforceable; the A4 loader validates it — see "Gapless coverage" above.)
- `otr_history` is the fourth instance of the SCD2 + `EXCLUDE` pattern (`instrument_metadata_history` was the first); the pattern is now demonstrably reusable, which de-risks future effective-dated needs.

**Negative / known trade-offs:**
- A third effective-dated table in `macro_data`. Mitigated: it is the same pattern, and the helper trio is a near-exact structural copy of the metadata-history trio — low marginal cognitive cost.
- `cusip` / `isin` are typed columns that are NULL for the large majority of instruments. This is the deliberate, normal shape of an asset-class-specific typed column (the futures `contract_code` column is NULL for every sovereign row today); the partial indexes keep it cheap.
- `cusip` / `isin` are not exposed in `v_market_data_daily_enriched`. A cash-bond primitive reading the view cannot see them without a follow-up `CREATE OR REPLACE VIEW`. Accepted deliberately, mirroring ADR 0001's treatment of history-only fields; the view's stable contract is worth more than pre-emptive width.

## Rollout plan

1. **This PR (A3):**
   - Land this ADR + the playbook contract README v1.3 bump.
   - Land the `schema.sql` additions (cusip/isin columns + indexes; `otr_history` table) for fresh setups.
   - Land the matching idempotent migration SQL for existing databases (below).
   - Land the DB helpers and the ingester change.
   - Land `MagicMock`-driven tests for the new pure-Python and Connection-composed helpers.
2. **PR A4 (next):** `sovereign_cash_bonds.yml` (start US, expand by country later) — every universe row stamps `instrument_type: sovereign_cash_bond` explicitly per the universal contract (the ingester's coarse `_infer_instrument_type` fallback is a safety net, not the primary path); verify the Bloomberg cash-bond mnemonics; extract; ingest; populate `otr_history`; add the OTR pre-write validator as defence-in-depth (overlaps **and** gaps).
3. **Future:** cash-bond primitives (`carry_and_roll`, `otr_ofr_spread`); a possible `CREATE OR REPLACE VIEW` to surface `cusip` if a view-read pattern proves common.

### Migration SQL — run on an existing database

Idempotent and data-safe (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`; no `DELETE`, no `UPDATE`, no destructive rewrite). Apply by exec-ing into Postgres (`docker exec -it <pg> psql -U <user> -d <db>`).

```sql
BEGIN;

-- 0. btree_gist — required by the otr_history EXCLUDE constraint below (it gives
--    GIST the "=" operator class for the country/tenor varchars). Step 0
--    (ADR 0001) already installs it for instrument_metadata_history; repeated
--    here, idempotently, so this migration block is self-sufficient even if run
--    against a database that somehow lacks it.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1. instrument_master: cash-bond identity columns (nullable → existing rows unaffected)
ALTER TABLE macro_data.instrument_master
    ADD COLUMN IF NOT EXISTS cusip VARCHAR(16);
ALTER TABLE macro_data.instrument_master
    ADD COLUMN IF NOT EXISTS isin  VARCHAR(16);

CREATE UNIQUE INDEX IF NOT EXISTS uq_instrument_master_cusip
    ON macro_data.instrument_master (cusip) WHERE cusip IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_instrument_master_isin
    ON macro_data.instrument_master (isin) WHERE isin IS NOT NULL;

-- 2. otr_history: dedicated SCD2 table for on-the-run history
CREATE TABLE IF NOT EXISTS macro_data.otr_history (
    otr_history_id BIGSERIAL PRIMARY KEY,
    country VARCHAR(16) NOT NULL,
    tenor   VARCHAR(16) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to   DATE,
    otr_instrument_id BIGINT NOT NULL
        REFERENCES macro_data.instrument_master(instrument_id),
    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_otr_history_window
        UNIQUE (country, tenor, effective_from),
    CONSTRAINT ck_otr_history_window
        CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT ex_otr_history_no_overlap
        EXCLUDE USING GIST (
            country WITH =,
            tenor   WITH =,
            daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]') WITH &&
        )
);

CREATE INDEX IF NOT EXISTS idx_otr_history_pit
    ON macro_data.otr_history (country, tenor, effective_from DESC);
CREATE INDEX IF NOT EXISTS idx_otr_history_instrument
    ON macro_data.otr_history (otr_instrument_id);
CREATE INDEX IF NOT EXISTS idx_otr_history_attributes
    ON macro_data.otr_history USING GIN (attributes);

COMMIT;
```

**Post-migration verification:**

```sql
-- Columns exist:
SELECT column_name FROM information_schema.columns
 WHERE table_schema = 'macro_data' AND table_name = 'instrument_master'
   AND column_name IN ('cusip','isin');                       -- expect 2 rows

-- otr_history exists and is empty:
SELECT count(*) FROM macro_data.otr_history;                  -- expect 0

-- Existing data untouched:
SELECT count(*) FROM macro_data.instrument_master;            -- unchanged vs pre-migration
SELECT count(*) FROM macro_data.market_data_daily;            -- unchanged vs pre-migration

-- EXCLUDE constraint is live (this INSERT pair MUST error):
BEGIN;
INSERT INTO macro_data.otr_history (country, tenor, effective_from, effective_to, otr_instrument_id)
SELECT 'US','10Y', d.ef, d.et,
       (SELECT min(instrument_id) FROM macro_data.instrument_master)
FROM (VALUES ('2024-01-01'::date,'2024-06-30'::date),
             ('2024-04-01'::date,'2024-09-30'::date)) d(ef,et);
-- expect ERROR: conflicting key value violates exclusion constraint
ROLLBACK;
```

**Rollback** (safe only before any data has been written to `otr_history` and before any instrument row has a non-NULL `cusip` / `isin`):

```sql
BEGIN;
DROP TABLE IF EXISTS macro_data.otr_history;
DROP INDEX IF EXISTS macro_data.uq_instrument_master_cusip;
DROP INDEX IF EXISTS macro_data.idx_instrument_master_isin;
ALTER TABLE macro_data.instrument_master DROP COLUMN IF EXISTS isin;
ALTER TABLE macro_data.instrument_master DROP COLUMN IF EXISTS cusip;
COMMIT;
```

## Verification

A3's acceptance criteria (no Bloomberg required):

- `database/schema.sql` parses and reflects the design above; the migration block is idempotent (running it twice is a no-op) and data-safe.
- `pytest Macro_Copilot/tests/state/test_cash_bond_substrate.py` passes — `_normalize_instrument_record` maps `cusip` / `isin` onto the typed-column dict; the ingester's `_build_instrument_attributes` excludes them from the JSONB payload (no double-write); `_normalize_otr_record` validates and shapes OTR records; `upsert_otr_history` / `close_open_otr_window` compose under a caller-owned `Connection` without opening a sub-transaction.
- The existing test suite is unchanged — A3 is purely additive (`pytest tests/state/` green).
- No production playbook is altered (`git diff` is empty for `rates_agent/playbooks/`).
- The operator's post-migration verification queries return the expected results, and the `EXCLUDE` smoke test errors as designed.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-20 | Initial decision. Accepted. |
