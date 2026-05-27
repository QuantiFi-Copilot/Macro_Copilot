-- ============================================================================
-- Migration: add macro_data.tool_metadata table (Phase 0 of the `revamp`
--            branch — see docs_revamped/03_standards/tool_lifecycle.md +
--            docs_revamped/05_decisions/0015-tool-metadata-db-table.md).
--
-- WHY THIS EXISTS
-- ---------------
-- database/schema.sql is mounted into the Postgres container ONLY as a
-- Docker init script (/docker-entrypoint-initdb.d/10_schema.sql).  Docker
-- runs init scripts exclusively on first cluster initialisation — an
-- empty data directory.  Existing databases already have a populated
-- `macrodata` volume, so `CREATE TABLE IF NOT EXISTS tool_metadata` in
-- schema.sql will NOT add the new table to a live cluster.
--
-- This migration applies the Phase 0 tool_metadata delta.  Every
-- statement is idempotent (IF NOT EXISTS), so it is safe to run more
-- than once and safe to run on a DB that already has the table.  All
-- changes are additive — no existing data is touched.
--
-- WHAT THIS DOES
-- --------------
-- Creates `macro_data.tool_metadata` — a per-tool static-metadata table
-- that is the DB-backed source of truth for:
--   - output_field_units (units per output field, JSONB)
--   - theoretical_reference (institutional textbook citation)
--   - known_limitations (documented edge cases)
--   - desk_narrative (user-facing macro-narrative copy)
--   - source_material_verified (human-judgment status, JSONB)
-- Plus mechanical metadata: domain, category.
--
-- Frequently-changing status flags (e.g. validation_status) continue to
-- live in manifesto/03_tool_manifest/*.yml.  User-overridable methodology
-- conventions continue to live in rates_agent/<domain>/tools/<tool>/config.yaml.
--
-- This migration creates the table ONLY.  Population is a separate
-- step — see database/populate_tool_metadata.py.
--
-- APPLY
-- -----
--   docker exec -i macro-tsdb psql -U quantuser -d macrodata \
--       < database/migrations/2026-05-26_phase0_tool_metadata_table.sql
--
-- POPULATE (after applying the migration)
-- ---------------------------------------
--   python database/populate_tool_metadata.py
-- ============================================================================

CREATE TABLE IF NOT EXISTS macro_data.tool_metadata (
    tool_name           VARCHAR(255) PRIMARY KEY,

    -- Static facts (mechanically derivable; populated by the seed script).
    domain              VARCHAR(64)  NOT NULL,
    category            VARCHAR(64),
    output_field_units  JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- Human-curated facts (NULL until per-tool Phase 1 work fills them).
    theoretical_reference     TEXT,
    known_limitations         TEXT,
    desk_narrative            TEXT,
    source_material_verified  JSONB,  -- {verifier: str, date: ISO date, source: str}

    -- Audit columns.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Closed-family domain enforcement.
    CONSTRAINT tool_metadata_domain_check CHECK (domain IN (
        'sovereign_bonds',
        'ois',
        'inflation_indexed_bonds',
        'inflation_swaps',
        'policy_futures',
        'bond_futures'
    ))
);

CREATE INDEX IF NOT EXISTS idx_tool_metadata_domain
    ON macro_data.tool_metadata (domain);

CREATE INDEX IF NOT EXISTS idx_tool_metadata_category
    ON macro_data.tool_metadata (category)
    WHERE category IS NOT NULL;
