-- ============================================================================
-- Migration: add the ``tool_metadata_domain_check`` CHECK constraint to an
--            EXISTING macro_data.tool_metadata table (Phase 0 fix-up
--            remediation — see commit d814683 + ADR
--            docs_revamped/05_decisions/0015-tool-metadata-db-table.md).
--
-- WHY THIS EXISTS
-- ---------------
-- The Phase 0 fix-up added a closed-family CHECK constraint on the
-- ``domain`` column at the CREATE TABLE step (in both
-- database/schema.sql and database/migrations/2026-05-26_phase0_tool_metadata_table.sql).
--
-- BUT the CREATE TABLE statement uses ``IF NOT EXISTS``, which is a no-op
-- on any cluster where the table was already created from the PRIOR
-- version of the migration file (the version without the CHECK
-- constraint).  That means a DB initialised against the prior version
-- never picks up the constraint, even after re-applying the now-fixed
-- migration file.
--
-- This separate migration explicitly ALTERs the existing table to add
-- the missing constraint.  Idempotent — checks ``pg_constraint`` for
-- the constraint name before adding, so re-runs are safe.
--
-- APPLY
-- -----
--   docker exec -i macro-tsdb psql -v ON_ERROR_STOP=1 -U quantuser -d macrodata \
--       < database/migrations/2026-05-27_phase0_tool_metadata_domain_check.sql
--
-- WHEN TO SKIP
-- ------------
-- If your DB was initialised with the fix-up's CREATE TABLE (i.e. the
-- table was created from the NEW migration file or from the updated
-- schema.sql), the constraint already exists.  This migration is a
-- no-op in that case — safe to run anyway.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'tool_metadata_domain_check'
          AND conrelid = 'macro_data.tool_metadata'::regclass
    ) THEN
        ALTER TABLE macro_data.tool_metadata
        ADD CONSTRAINT tool_metadata_domain_check
        CHECK (domain IN (
            'sovereign_bonds',
            'ois',
            'inflation_indexed_bonds',
            'inflation_swaps',
            'policy_futures',
            'bond_futures'
        ));
        RAISE NOTICE 'Added tool_metadata_domain_check constraint to macro_data.tool_metadata.';
    ELSE
        RAISE NOTICE 'tool_metadata_domain_check already present — no change.';
    END IF;
END $$;
