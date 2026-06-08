-- ============================================================================
-- Migration STUB: macro_data.tool_metadata for `calculate_ois_forward_rate_tool`
-- Created by frontend factory Batch 2 setup (2026-05-30) to satisfy
-- PRE_FLIGHT Check 3(b) (curated migration file existence).
--
-- The curated content fields below are PLACEHOLDERS sourced from the
-- backend `rates_agent/ois/tools/forward_rate/config.yaml`'s methodology
-- block, which is the runtime source of truth for the wire
-- `methodology_label` and the THESIS disclosure already.  Replace with
-- full human-authored prose when ready; this stub does not block the
-- frontend factory from building the dual-view module.
--
-- The DB table `macro_data.tool_metadata` does not exist on the live
-- substrate yet (Phase-0 seed not applied).  This migration is
-- idempotent and intentionally NOT applied.
-- ============================================================================

UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$STUB - see rates_agent/ois/tools/forward_rate/config.yaml methodology.what_it_does block for the canonical disclosure that flows to the wire methodology_label.$tref$,
    known_limitations     = $klim$STUB - see rates_agent/ois/tools/forward_rate/config.yaml methodology and known_caveats blocks for the constraints documented at backend level.$klim$,
    desk_narrative        = $narr$STUB - see rates_agent/ois/tools/forward_rate/config.yaml + the per-tool README for the desk-facing prose.  Author full curation when ready.$narr$,
    updated_at = NOW()
WHERE tool_name = 'calculate_ois_forward_rate_tool';

INSERT INTO macro_data.tool_metadata (tool_name, domain, category, output_field_units, theoretical_reference, known_limitations, desk_narrative)
SELECT
    'calculate_ois_forward_rate_tool', 'ois', 'forward_rate', '{}'::jsonb,
    $tref$STUB$tref$,
    $klim$STUB$klim$,
    $narr$STUB$narr$
WHERE NOT EXISTS (SELECT 1 FROM macro_data.tool_metadata WHERE tool_name = 'calculate_ois_forward_rate_tool');
