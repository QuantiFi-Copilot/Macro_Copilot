-- ================================================================================================
-- MACRO COPILOT - UPDATED SCHEMA DEFINITION
-- Description: Core schema for a multi-asset macro stack with richer instrument identity,
--              daily market observations, and load/extraction lineage.
-- ================================================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;
-- ``btree_gist`` provides ``=`` operator class support inside GIST indexes,
-- which the EXCLUDE constraint on ``instrument_metadata_history`` (section 4)
-- needs to combine equality on ``instrument_id`` with range overlap on the
-- effective window.
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE SCHEMA IF NOT EXISTS macro_data;

-- ================================================================================================
-- 1. INSTRUMENT MASTER
-- Goal: define what each instrument is in a structured, queryable way.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.instrument_master (
    instrument_id BIGSERIAL PRIMARY KEY,
    vendor VARCHAR(32) NOT NULL DEFAULT 'BLOOMBERG',
    vendor_ticker VARCHAR(128) NOT NULL,
    asset_class VARCHAR(64) NOT NULL,
    instrument_type VARCHAR(64) NOT NULL,     -- e.g. sovereign, ois, irs, future, linker, swaption
    curve_family VARCHAR(64),                 -- e.g. UST, SOFR_OIS, EUR_IRS
    country VARCHAR(16),
    currency VARCHAR(16),
    tenor VARCHAR(16),                        -- e.g. 2Y, 5Y, 10Y
    underlying_index VARCHAR(32),             -- e.g. SOFR, SONIA, ESTR
    contract_code VARCHAR(32),                -- useful for futures/options if needed
    cusip VARCHAR(16),                        -- cash-bond identity (ADR 0003); NULL for non-cash-bond instruments
    isin  VARCHAR(16),                        -- cash-bond identity (ADR 0003); NULL for non-cash-bond instruments
    expiry_date DATE,
    maturity_date DATE,
    is_rolling_contract BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    attributes JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instrument_vendor UNIQUE (vendor, vendor_ticker)
);

CREATE INDEX IF NOT EXISTS idx_instrument_master_lookup
    ON macro_data.instrument_master (asset_class, instrument_type, country, currency, tenor);

CREATE INDEX IF NOT EXISTS idx_instrument_master_curve_family
    ON macro_data.instrument_master (curve_family);

CREATE INDEX IF NOT EXISTS idx_instrument_master_attributes
    ON macro_data.instrument_master USING GIN (attributes);

-- Cash-bond identity (ADR 0003). A CUSIP uniquely identifies one cash bond;
-- the index is partial (WHERE cusip IS NOT NULL) so the majority of instruments
-- — futures, OIS, benchmarks, linkers — which carry no CUSIP are not collapsed
-- onto a single NULL value by the uniqueness rule.
CREATE UNIQUE INDEX IF NOT EXISTS uq_instrument_master_cusip
    ON macro_data.instrument_master (cusip) WHERE cusip IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_instrument_master_isin
    ON macro_data.instrument_master (isin) WHERE isin IS NOT NULL;

-- ================================================================================================
-- 2. LOAD AUDIT
-- Goal: track what actually ran: playbook/version, extraction window, source file, and status.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.load_audit (
    load_id BIGSERIAL PRIMARY KEY,
    playbook_name VARCHAR(256) NOT NULL,
    playbook_version VARCHAR(64),
    playbook_hash VARCHAR(128),
    git_commit_hash VARCHAR(128),
    extractor_version VARCHAR(128),
    source_file_name VARCHAR(512),
    source_file_hash VARCHAR(128),
    dataset_name VARCHAR(128),
    requested_start_date DATE,
    requested_end_date DATE,
    extracted_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(32) NOT NULL DEFAULT 'SUCCESS',   -- e.g. SUCCESS / FAILED / PARTIAL
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_load_audit_playbook
    ON macro_data.load_audit (playbook_name, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_load_audit_dataset
    ON macro_data.load_audit (dataset_name, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_load_audit_source_file_hash
    ON macro_data.load_audit (source_file_hash);

-- ================================================================================================
-- 3. DAILY MARKET DATA
-- Goal: one row per daily observation for a given instrument + field.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.market_data_daily (
    trade_date DATE NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES macro_data.instrument_master(instrument_id),
    field_name VARCHAR(64) NOT NULL,          -- e.g. YLD_YTM_MID, PX_LAST, OPEN_INT
    field_value NUMERIC(20, 8),
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, instrument_id, field_name)
);

SELECT create_hypertable('macro_data.market_data_daily', 'trade_date', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_market_data_daily_instrument_date
    ON macro_data.market_data_daily (instrument_id, trade_date DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_daily_field
    ON macro_data.market_data_daily (field_name, trade_date DESC);

-- ================================================================================================
-- 4. INSTRUMENT METADATA HISTORY (SCD2 for rolling-contract metadata)
-- Goal: preserve per-day reference metadata (contract_code, expiry_date, maturity_date,
--       security_name, tick_size, accrual windows, …) for rolling-generic tickers
--       (TY1, FV1, SFR1-8, ER1-8, …) whose underlying contract changes as the front rolls.
--
-- Design (ADR docs_revamped/05_decisions/0001-instrument-metadata-history.md):
--   * macro_data.instrument_master keeps its current shape and holds the *current* state per
--     ticker (this is what bdp() returns today and what upsert_instrument_master writes).
--   * macro_data.instrument_metadata_history captures *prior* effective windows plus the
--     *current* window (with effective_to = NULL).
--   * Reads route through v_market_data_daily_enriched (below), which COALESCEs history onto
--     master so reads stay byte-identical until backfill populates the history table.
--
-- Closes docs/technical_debt.md item #2.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.instrument_metadata_history (
    metadata_history_id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES macro_data.instrument_master(instrument_id),
    effective_from DATE NOT NULL,
    effective_to   DATE,                              -- NULL = currently in effect
    -- Mirrors of instrument_master's rolling-sensitive typed columns:
    contract_code  VARCHAR(32),
    expiry_date    DATE,
    maturity_date  DATE,
    -- Reference fields that today live in attributes JSONB on instrument_master but are
    -- needed per-effective-window for Phase 2-4 futures primitives. Promoted to typed
    -- columns here because they are query-hot for the upcoming RV stack (CTD, basis, DV01).
    security_name      VARCHAR(256),
    settlement_date    DATE,
    accrual_start_date DATE,
    accrual_end_date   DATE,
    tick_size      NUMERIC(20, 10),
    tick_value     NUMERIC(20, 10),
    contract_size  NUMERIC(20, 4),
    exchange_code  VARCHAR(32),
    underlying_ticker VARCHAR(128),
    -- Per-window escape hatch for asset-class-specific fields not promoted to typed cols.
    attributes JSONB,
    -- Provenance: which load wrote this row.
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instrument_metadata_history_window
        UNIQUE (instrument_id, effective_from),
    CONSTRAINT ck_instrument_metadata_history_window
        CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- Reject overlapping effective windows per instrument at write time.
    -- The view's LATERAL ORDER BY effective_from DESC LIMIT 1 silently picks
    -- the latest matching row when windows overlap; this constraint stops
    -- bad writes from ever producing that ambiguity. Adjacent windows that
    -- share a boundary day are also rejected (bounds = '[]' inclusive), which
    -- matches close_open_metadata_window()'s "new.effective_from - 1" close
    -- semantics in database.py.
    CONSTRAINT ex_instrument_metadata_history_no_overlap
        EXCLUDE USING GIST (
            instrument_id WITH =,
            daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]') WITH &&
        )
);

-- Point-in-time lookup: "which row was in effect for this instrument on this trade_date?"
-- The LATERAL subquery in the enriched view below uses this index for O(log N) per row.
CREATE INDEX IF NOT EXISTS idx_instrument_metadata_history_pit
    ON macro_data.instrument_metadata_history (instrument_id, effective_from DESC);

CREATE INDEX IF NOT EXISTS idx_instrument_metadata_history_attributes
    ON macro_data.instrument_metadata_history USING GIN (attributes);

-- ================================================================================================
-- 5. ENRICHED DAILY VIEW (with SCD2 history overlay)
-- Goal: make querying easier by joining observations to instrument metadata.
--       For rolling-contract tickers, COALESCE per-day-effective history onto the (current)
--       instrument_master row so historical trade_date queries see the metadata that was
--       actually true on that date, not today's overwrite.
--
-- Column shape is UNCHANGED from the pre-history version of this view: same 21 columns,
-- same types, same order. When the history table is empty (today, before the Step-1.1
-- backfill), every COALESCE falls through to the instrument_master values → byte-identical
-- output. After backfill, historical trade_date rows reflect their effective-window metadata.
--
-- Performance note: the LATERAL is guarded by ``i.is_rolling_contract = true`` so non-rolling
-- tickers (sovereign benchmarks, OIS, ZCIS, linkers — the vast majority of rows today) skip
-- the lookup entirely. Rolling-row reads pay one extra O(log N) index hit per row.
-- ================================================================================================
CREATE OR REPLACE VIEW macro_data.v_market_data_daily_enriched AS
SELECT
    d.trade_date,
    d.instrument_id,
    i.vendor,
    i.vendor_ticker,
    i.asset_class,
    i.instrument_type,
    i.curve_family,
    i.country,
    i.currency,
    i.tenor,
    i.underlying_index,
    COALESCE(hist.contract_code, i.contract_code) AS contract_code,
    COALESCE(hist.expiry_date,   i.expiry_date)   AS expiry_date,
    COALESCE(hist.maturity_date, i.maturity_date) AS maturity_date,
    i.is_rolling_contract,
    i.is_active,
    d.field_name,
    d.field_value,
    d.load_id,
    d.created_at,
    i.attributes
FROM macro_data.market_data_daily d
JOIN macro_data.instrument_master i
  ON d.instrument_id = i.instrument_id
LEFT JOIN LATERAL (
    SELECT
        h.contract_code,
        h.expiry_date,
        h.maturity_date
    FROM macro_data.instrument_metadata_history h
    WHERE i.is_rolling_contract = TRUE
      AND h.instrument_id = d.instrument_id
      AND h.effective_from <= d.trade_date
      AND (h.effective_to IS NULL OR h.effective_to >= d.trade_date)
    ORDER BY h.effective_from DESC
    LIMIT 1
) hist ON TRUE;

-- ================================================================================================
-- 6. ON-THE-RUN (OTR) HISTORY (SCD2 for the per-(country, tenor) on-the-run slot)
-- Goal: track which individual cash sovereign bond was on-the-run for each
--       (country, tenor) slot over time. A freshly-auctioned 10Y is on-the-run
--       for ~3 months until the next 10Y is auctioned, then becomes off-the-run
--       permanently — so OTR status is a STATE of a (country, tenor) slot over
--       time, not a static instrument attribute.
--
-- Design (ADR docs_revamped/05_decisions/0003-cash-bond-substrate.md):
--   * One row per (country, tenor, effective_window). otr_instrument_id points
--     at the macro_data.instrument_master row that was on-the-run during the
--     window; effective_to = NULL means "currently on-the-run".
--   * Same SCD2 + EXCLUDE pattern as instrument_metadata_history (section 4),
--     re-keyed from instrument_id to the (country, tenor) slot. The EXCLUDE
--     constraint DB-enforces NON-OVERLAP — AT MOST ONE bond is recorded as
--     on-the-run per (country, tenor) on any date. It does NOT enforce gapless
--     coverage ("some bond is on-the-run on EVERY date"): a gap between a
--     closed window and the next is structurally permitted. close_open_otr_window()
--     produces gapless adjacency when used correctly, and the A4 OTR loader is
--     responsible for validating no-gaps if that invariant matters downstream.
--   * A3 lands this table empty; the Step-2 data PR (A4) populates it.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.otr_history (
    otr_history_id BIGSERIAL PRIMARY KEY,
    country VARCHAR(16) NOT NULL,
    tenor   VARCHAR(16) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to   DATE,                              -- NULL = currently on-the-run
    otr_instrument_id BIGINT NOT NULL
        REFERENCES macro_data.instrument_master(instrument_id),
    -- Per-window escape hatch for fields not promoted to typed columns.
    attributes JSONB,
    -- Provenance: which load wrote this row.
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_otr_history_window
        UNIQUE (country, tenor, effective_from),
    CONSTRAINT ck_otr_history_window
        CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- Reject overlapping OTR windows per (country, tenor) slot at write time —
    -- i.e. AT MOST ONE bond is recorded as on-the-run per slot per date. (This
    -- does not enforce gapless coverage; see the section header.) Adjacent
    -- windows that share a boundary day are also rejected (bounds = '[]'
    -- inclusive), which matches close_open_otr_window()'s "new.effective_from - 1"
    -- close semantics in database.py.
    CONSTRAINT ex_otr_history_no_overlap
        EXCLUDE USING GIST (
            country WITH =,
            tenor   WITH =,
            daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[]') WITH &&
        )
);

-- Point-in-time slot lookup: "which bond was on-the-run for this (country,
-- tenor) on this date?" — one index hit for get_otr_at().
CREATE INDEX IF NOT EXISTS idx_otr_history_pit
    ON macro_data.otr_history (country, tenor, effective_from DESC);

-- Reverse lookup: "which OTR window(s) did this bond occupy?"
CREATE INDEX IF NOT EXISTS idx_otr_history_instrument
    ON macro_data.otr_history (otr_instrument_id);

CREATE INDEX IF NOT EXISTS idx_otr_history_attributes
    ON macro_data.otr_history USING GIN (attributes);

-- ================================================================================================
-- 7. EVENT CALENDAR (macro releases, central-bank meetings, sovereign auctions)
-- Goal: one row per macro EVENT — an economic release (CPI, NFP, retail, PMI, claims),
--       a central-bank meeting (FOMC, ECB, BoE, BoJ), or a sovereign auction. Events do not
--       fit market_data_daily's (trade_date, instrument_id, field_name, value) long shape:
--       they are wide, structured, one-shot observations carrying actual / consensus / prior /
--       surprise / release_time / period (releases) or high-yield / bid-to-cover / tail /
--       indirect (auctions). They are also not instruments — not tradable, no vendor ticker.
--       So they get their own table.
--
-- Design (ADR docs_revamped/05_decisions/0004-event-calendar-substrate.md):
--   * One row per event. The natural key (event_type, country, release_date) is stable
--     across the announcement -> results lifecycle, so an auction is a SINGLE row whose
--     result columns fill in post-auction via upsert_event_calendar's ON CONFLICT DO UPDATE
--     (the same way an economic release's `actual` is NULL before the print and filled after).
--   * `period` is intentionally NOT in the natural key — it can be NULL at announcement and
--     filled later; a key column that changes between ingests would break idempotency.
--   * event_category is a free VARCHAR (economic_release | central_bank_meeting | auction) —
--     consistent with how asset_class / instrument_type are modelled; the closed set is
--     enforced at the code/contract level (EVENT_CATEGORIES in database.py), not via a CHECK.
--   * related_instrument_id optionally links an event to an instrument_master row (an
--     auctioned CUSIP, or the synthetic per-meeting WIRP instrument B2 will create).
--   * WIRP per-meeting implied-rate pricing is a daily time series and does NOT live here —
--     it goes into market_data_daily against a synthetic per-meeting instrument (ADR 0004).
--   * B1 lands this table empty; the Step-3 data PR (B2) populates it.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.event_calendar (
    event_id BIGSERIAL PRIMARY KEY,
    event_type     VARCHAR(64)  NOT NULL,   -- e.g. cpi_yoy, nfp, fomc_decision, ust_auction_10y
    event_category VARCHAR(32)  NOT NULL,   -- economic_release | central_bank_meeting | auction
    country        VARCHAR(16)  NOT NULL,
    currency       VARCHAR(16),
    central_bank   VARCHAR(32),             -- FOMC / ECB / BOE / BOJ — for central_bank_meeting rows
    release_date   DATE         NOT NULL,
    release_time   TIME,                    -- nullable — not always known
    period         VARCHAR(32),             -- reference period: "Mar 2026", "Q1 2026"
    -- Economic-release numeric fields (the cross-category surprise model):
    actual           NUMERIC(20, 8),
    consensus_median NUMERIC(20, 8),
    consensus_high   NUMERIC(20, 8),
    consensus_low    NUMERIC(20, 8),
    prior            NUMERIC(20, 8),
    revised_prior    NUMERIC(20, 8),
    surprise         NUMERIC(20, 8),
    surprise_std_dev NUMERIC(20, 8),
    -- Auction result fields (typed columns per ADR 0004; NULL for non-auction rows):
    high_yield   NUMERIC(20, 8),
    bid_to_cover NUMERIC(20, 8),
    tail_bps     NUMERIC(20, 8),
    indirect_pct NUMERIC(20, 8),
    -- Optional linkage to an instrument (an auctioned CUSIP, or the synthetic
    -- per-meeting WIRP instrument for a central_bank_meeting row).
    related_instrument_id BIGINT REFERENCES macro_data.instrument_master(instrument_id),
    -- Escape hatch: central-bank decision (hike/hold/cut), auction sized_amount,
    -- statement classification, and any event-type-specific structured field.
    attributes JSONB,
    -- Provenance: which load wrote this row.
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Natural key for idempotent re-ingestion. Deliberately excludes `period`
    -- (see the section header) so the announcement -> results upsert updates a
    -- single row rather than inserting a duplicate.
    CONSTRAINT uq_event_calendar_natural_key
        UNIQUE (event_type, country, release_date)
);

-- "Every CPI release over time" — the event-study series lookup.
CREATE INDEX IF NOT EXISTS idx_event_calendar_type_date
    ON macro_data.event_calendar (event_type, release_date);

-- "Every US event in a window".
CREATE INDEX IF NOT EXISTS idx_event_calendar_country_date
    ON macro_data.event_calendar (country, release_date);

-- "Every auction in a window".
CREATE INDEX IF NOT EXISTS idx_event_calendar_category_date
    ON macro_data.event_calendar (event_category, release_date);

-- FK reverse lookup ("events linked to this instrument"); Postgres does not
-- auto-index foreign-key columns.
CREATE INDEX IF NOT EXISTS idx_event_calendar_related_instrument
    ON macro_data.event_calendar (related_instrument_id);

CREATE INDEX IF NOT EXISTS idx_event_calendar_attributes
    ON macro_data.event_calendar USING GIN (attributes);


-- ================================================================================================
-- 8) macro_data.futures_deliverables  (ADR 0011)
-- The deliverable basket + conversion factor + delivery/notice dates per bond-future contract.
-- One row per (generic, contract cycle, deliverable bond). Reference data — not time-series —
-- so it does not fit market_data_daily, exactly as event_calendar did not (ADR 0004).
--
-- KEYING (ADR 0011 Decisions 1 & 2):
--   * The futures contract is keyed by (instrument_id, contract_code) — the same identity
--     instrument_metadata_history uses (ADR 0001) so joins to that table are clean
--     `ON (instrument_id, contract_code)`. `instrument_id` is the GENERIC's row (TY1's row);
--     `contract_code` is the specific cycle (e.g. 'TYZ24'). A `metadata_history_id` FK was
--     rejected — it creates a chicken-and-egg ordering dependency on metadata-history
--     extraction.
--   * The deliverable bond is a CUSIP string + an OPTIONAL FK to instrument_master. Most
--     deliverable bonds are seasoned issues NOT in instrument_master (A4 bounded the cash-bond
--     universe to OTR + recently-off-the-run); the CUSIP is the universal identity, the FK is
--     convenience. The ingester populates `deliverable_instrument_id` only when the deliverable
--     bond IS already registered.
--
-- DENORMALISATION (ADR 0011 Alternatives):
--   First/last delivery + notice dates are per-CONTRACT, but stored on every basket row
--   (duplicated across the ~30 deliverables of a basket). Accepted for v1 — basket sizes are
--   small (~11k rows max across all generics and cycles); a future ADR can split into two
--   tables if scale demands it.
--
-- UPSERT discipline:
--   Natural key (instrument_id, contract_code, deliverable_cusip) — full-row UPSERT on conflict.
--   Re-ingesting the same parquet over the same key safely overwrites every non-key column.
-- ================================================================================================
CREATE TABLE IF NOT EXISTS macro_data.futures_deliverables (
    deliverable_id BIGSERIAL PRIMARY KEY,

    -- The futures contract — generic instrument + cycle code (ADR 0001 keying).
    instrument_id BIGINT NOT NULL
        REFERENCES macro_data.instrument_master(instrument_id),
    contract_code VARCHAR(32) NOT NULL,

    -- The deliverable bond — CUSIP canonical, ISIN secondary, optional FK (ADR 0003 identity).
    deliverable_cusip VARCHAR(16) NOT NULL,
    deliverable_isin  VARCHAR(16),
    deliverable_instrument_id BIGINT
        REFERENCES macro_data.instrument_master(instrument_id),

    -- Per (contract, deliverable) — the futures invoice adjustment.
    conversion_factor NUMERIC(20, 10),

    -- Per contract — duplicated across deliverables in the basket (see denormalisation note).
    first_delivery_date DATE,
    last_delivery_date  DATE,
    first_notice_date   DATE,
    last_notice_date    DATE,

    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per (generic, contract cycle, deliverable bond) — the natural key for idempotent
    -- re-ingestion. UPSERT on conflict.
    CONSTRAINT uq_futures_deliverables_natural_key
        UNIQUE (instrument_id, contract_code, deliverable_cusip)
);

-- Per-contract reads ("give me TYZ24's basket"). The natural-key UNIQUE already supports the
-- (instrument_id, contract_code) prefix, but a dedicated index makes the intent explicit and
-- supports group-by counts.
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_contract
    ON macro_data.futures_deliverables (instrument_id, contract_code);

-- Per-deliverable-bond reads ("which contracts is this CUSIP deliverable into?").
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_cusip
    ON macro_data.futures_deliverables (deliverable_cusip);

-- Optional-FK lookups (partial — most rows will have NULL deliverable_instrument_id since most
-- deliverable bonds are seasoned issues outside A4's OTR-bounded universe).
CREATE INDEX IF NOT EXISTS idx_futures_deliverables_deliverable_instrument
    ON macro_data.futures_deliverables (deliverable_instrument_id)
    WHERE deliverable_instrument_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_futures_deliverables_attributes
    ON macro_data.futures_deliverables USING GIN (attributes);
