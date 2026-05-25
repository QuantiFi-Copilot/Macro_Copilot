// ============================================================================
// src/modules/primitives/calculate_wirp_meeting_pricing_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_wirp_meeting_pricing_tool',
  tiers: ['workflow_incompatible'],
  displayName: 'calculate_wirp_meeting_pricing',
  category: 'meeting_pricing',
  oneLineSummary: 'Per-meeting WIRP pricing snapshot for one central bank (FOMC / ECB / BOE / BOJ).  Surfaces Bloomberg\'s WIRP-screen fields verbatim per ADR 0009 §1 (INGEST primitive, P12 boundary — NOT recomputed from STIR futures or OIS pricing): implied policy rate, CUMULATIVE move probability, number of 25bp moves priced, implied rate change.  WIRP_MOVE_PROB is cumulative across multiple 25bp moves (range -360.1..548.0 per wirp.yml Stage-B) — emitted as ``cumulative_move_prob_pct`` with explicit methodology disclosure.  No hike/cut/hold decomposition: any single-event identity (max(p,0)/max(-p,0)/100-|p|) is empirically wrong for a cumulative quantity — BOE 2025-05-08 ships -104.9% verbatim, which the identity would render as hold=-4.9% (impossible).  Step-by-step probability derivation is a documented separate primitive (see config.yaml planned_ extensions).  Two selection modes (next_n_meetings vs specific_meeting_date) via a cohesive PR8 central surface; n_meetings defaults from YAML (currently 6).',
  unsupportedReason: {
    label: 'calculate_wirp_meeting_pricing',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: List of per-meeting WIRP snapshots — each meeting carries a mix of identifier columns (vendor_ticker + 4 Bloomberg tickers per ADR 0009 §1 provenance) and Bloomberg-ingested numeric fields (implied_policy_rate_pct, cumulative_move_prob_pct, num_25bp_',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
