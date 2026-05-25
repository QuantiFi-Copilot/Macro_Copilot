// ============================================================================
// src/modules/primitives/calculate_nfp_surprise_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_nfp_surprise_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_nfp_surprise',
  category: 'economic_release_surprises',
  oneLineSummary: 'Per-release US nonfarm-payrolls (NFP) surprise series (actual − consensus_median, in THOUSANDS of jobs) plus a rolling z-score over a window of N RELEASES (NOT calendar days).  Surprise is computed at the primitive layer per ADR 0008 §2\'s P12 disclosure (event_calendar.surprise is intentionally NULL by ingestion; Bloomberg ECO screen reports the same number).  US-only, event_type=nfp — both country and event_type are YAML-locked, no LLM-facing instrument selector.  Lives under sovereign_bonds because NFP is the macro print most directly read into the 2s/5s/10s front-end Treasury curve.',
};
