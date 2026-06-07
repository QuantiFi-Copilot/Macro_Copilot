-- ============================================================================
-- Migration STUB: macro_data.tool_metadata for `calculate_swap_spread_tool`
-- Created by frontend factory Batch 3 follow-up (2026-06-07) to satisfy
-- PRE_FLIGHT Check 3(b) after the mockup duplicate-file bug was fixed
-- (Extended.png re-authored by human).
--
-- Curated content fields below are PLACEHOLDERS sourced from the backend
-- `rates_agent/ois/tools/swap_spread/config.yaml`'s methodology block,
-- which is the runtime source of truth for the wire `methodology_label`
-- and THESIS disclosure already.  Replace with full prose when ready.
--
-- The DB table `macro_data.tool_metadata` does not exist on the live
-- substrate yet; this migration is idempotent and intentionally NOT applied.
-- ============================================================================

UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$STUB - see rates_agent/ois/tools/swap_spread/config.yaml methodology block.$tref$,
    known_limitations     = $klim$STUB - par-leg OIS approximation, NOT per-bond ASW (does not reflect specials/scarcity).$klim$,
    desk_narrative        = $narr$STUB - Treasury yield minus OIS rate at common tenor.  Canonical RV read for fixed-income vs swap-market dislocations.$narr$,
    updated_at = NOW()
WHERE tool_name = 'calculate_swap_spread_tool';

INSERT INTO macro_data.tool_metadata (tool_name, domain, category, output_field_units, theoretical_reference, known_limitations, desk_narrative)
SELECT
    'calculate_swap_spread_tool', 'ois', 'cross_market_rv', '{}'::jsonb,
    $tref$STUB$tref$,
    $klim$STUB$klim$,
    $narr$STUB$narr$
WHERE NOT EXISTS (SELECT 1 FROM macro_data.tool_metadata WHERE tool_name = 'calculate_swap_spread_tool');
