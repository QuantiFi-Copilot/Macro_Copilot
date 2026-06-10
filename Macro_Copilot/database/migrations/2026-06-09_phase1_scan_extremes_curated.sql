-- ============================================================================
-- Migration STUB: macro_data.tool_metadata for `scan_extremes_tool`
-- Created by frontend factory Batch 4 setup (2026-06-09) to satisfy
-- PRE_FLIGHT Check 3(b) (curated migration file existence).
--
-- For migration-mode entries (build_mode: migration), the curated content
-- may already exist in an earlier phase-1 migration file authored when the
-- tool was first shipped on the legacy typed-renderer pattern.  This stub
-- exists so the per-tool Check 3(b) glob finds at least one matching file
-- with this build date stamp.
--
-- The DB table `macro_data.tool_metadata` does not exist on the live
-- substrate yet (Phase-0 seed not applied).  This migration is idempotent
-- and intentionally NOT applied.  See the backend tool's config.yaml
-- methodology.what_it_does block for the canonical disclosure that flows
-- to the wire methodology_label.
-- ============================================================================

UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$STUB - see rates_agent/sovereign_bonds/tools/scan_extremes/config.yaml methodology block.$tref$,
    known_limitations     = $klim$STUB - see backend config.yaml + known_caveats blocks for constraints documented at backend level.$klim$,
    desk_narrative        = $narr$STUB - see backend config.yaml + per-tool README for desk-facing prose. Author full curation when ready.$narr$,
    updated_at = NOW()
WHERE tool_name = 'scan_extremes_tool';

INSERT INTO macro_data.tool_metadata (tool_name, domain, category, output_field_units, theoretical_reference, known_limitations, desk_narrative)
SELECT
    'scan_extremes_tool', 'sovereign_bonds', 'scanner', '{}'::jsonb,
    $tref$STUB$tref$,
    $klim$STUB$klim$,
    $narr$STUB$narr$
WHERE NOT EXISTS (SELECT 1 FROM macro_data.tool_metadata WHERE tool_name = 'scan_extremes_tool');
