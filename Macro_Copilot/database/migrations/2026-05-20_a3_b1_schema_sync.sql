-- ============================================================================
-- Migration: sync the live macro_data schema with A3 (cash-bond substrate,
--            ADR 0003) and B1 (event-calendar substrate, ADR 0004).
--
-- WHY THIS EXISTS
-- ---------------
-- database/schema.sql is mounted into the Postgres container ONLY as a Docker
-- init script (/docker-entrypoint-initdb.d/10_schema.sql). Docker runs init
-- scripts exclusively on first cluster initialisation — an empty data
-- directory. The `macrodata` volume was created before A3/B1 merged, so their
-- additions never reached the running database:
--
--   * macro_data.instrument_master is missing the cusip / isin columns (A3)
--   * the macro_data.otr_history    table does not exist                (A3)
--   * the macro_data.event_calendar table does not exist                (B1)
--
-- schema.sql cannot self-heal this on an existing volume: `CREATE TABLE
-- IF NOT EXISTS instrument_master` is a no-op against the existing table and
-- never adds the new columns. The symptom: a time-series ingestion fails with
-- `AttributeError: cusip` inside upsert_instrument_master, because that helper
-- reflects the live instrument_master table and then references the (absent)
-- excluded.cusip column when building its ON CONFLICT update.
--
-- This migration applies exactly the A3 + B1 delta. Every statement is
-- idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS), so it is safe to run
-- more than once and safe to run on a DB that already has some of these
-- objects. All changes are additive — nullable columns, new indexes, new
-- tables — no existing data is rewritten.
--
-- APPLY
-- -----
--   docker exec -i macro-tsdb psql -U quantuser -d macrodata \
--       < database/migrations/2026-05-20_a3_b1_schema_sync.sql
-- ============================================================================

-- btree_gist supplies the `=` operator class GIST needs for the EXCLUDE
-- constraint on otr_history below. Idempotent; almost certainly already
-- installed (instrument_metadata_history's EXCLUDE constraint needs it too).
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ----------------------------------------------------------------------------
-- A3 — instrument_master cash-bond identity columns (ADR 0003)
-- ----------------------------------------------------------------------------
ALTER TABLE macro_data.instrument_master
    ADD COLUMN IF NOT EXISTS cusip VARCHAR(16);   -- NULL for non-cash-bond instruments
ALTER TABLE macro_data.instrument_master
    ADD COLUMN IF NOT EXISTS isin  VARCHAR(16);   -- NULL for non-cash-bond instruments

-- A CUSIP uniquely identifies one cash bond; the index is partial
-- (WHERE cusip IS NOT NULL) so the majority of instruments that carry no
-- CUSIP are not collapsed onto a single NULL value by the uniqueness rule.
CREATE UNIQUE INDEX IF NOT EXISTS uq_instrument_master_cusip
    ON macro_data.instrument_master (cusip) WHERE cusip IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_instrument_master_isin
    ON macro_data.instrument_master (isin) WHERE isin IS NOT NULL;

-- ----------------------------------------------------------------------------
-- A3 — on-the-run (OTR) history table (ADR 0003)
-- SCD2 for the per-(country, tenor) on-the-run slot. A3 lands this empty;
-- the A4 data PR populates it. DDL copied verbatim from database/schema.sql.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_data.otr_history (
    otr_history_id BIGSERIAL PRIMARY KEY,
    country VARCHAR(16) NOT NULL,
    tenor   VARCHAR(16) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to   DATE,                              -- NULL = currently on-the-run
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

-- ----------------------------------------------------------------------------
-- B1 — event_calendar table (ADR 0004)
-- One row per macro event (economic release / central-bank meeting / auction).
-- B1 lands this empty; the B2 data PR populates it. DDL copied verbatim from
-- database/schema.sql.
-- ----------------------------------------------------------------------------
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
    actual           NUMERIC(20, 8),
    consensus_median NUMERIC(20, 8),
    consensus_high   NUMERIC(20, 8),
    consensus_low    NUMERIC(20, 8),
    prior            NUMERIC(20, 8),
    revised_prior    NUMERIC(20, 8),
    surprise         NUMERIC(20, 8),
    surprise_std_dev NUMERIC(20, 8),
    high_yield   NUMERIC(20, 8),
    bid_to_cover NUMERIC(20, 8),
    tail_bps     NUMERIC(20, 8),
    indirect_pct NUMERIC(20, 8),
    related_instrument_id BIGINT REFERENCES macro_data.instrument_master(instrument_id),
    attributes JSONB,
    load_id BIGINT REFERENCES macro_data.load_audit(load_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_calendar_natural_key
        UNIQUE (event_type, country, release_date)
);

CREATE INDEX IF NOT EXISTS idx_event_calendar_type_date
    ON macro_data.event_calendar (event_type, release_date);

CREATE INDEX IF NOT EXISTS idx_event_calendar_country_date
    ON macro_data.event_calendar (country, release_date);

CREATE INDEX IF NOT EXISTS idx_event_calendar_category_date
    ON macro_data.event_calendar (event_category, release_date);

CREATE INDEX IF NOT EXISTS idx_event_calendar_related_instrument
    ON macro_data.event_calendar (related_instrument_id);

CREATE INDEX IF NOT EXISTS idx_event_calendar_attributes
    ON macro_data.event_calendar USING GIN (attributes);
