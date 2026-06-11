// ============================================================================
// src/modules/primitives/calculate_wirp_meeting_pricing_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view build under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/wirp-meeting-pricing + own
//     surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// RUNTIME TIER — ``workflow_incompatible`` is KEPT (backend declares the
// tool in WORKFLOW_INCOMPATIBLE_TOOLS: list-shaped per-meeting output, no
// Series / Panel artifact for the workflow bridge).  The runtime tier and
// the capability tiers are ORTHOGONAL axes: workflow-incompatible only
// says the WORKFLOW BRIDGE can't dispatch it — the typed-detail endpoint
// is fully live, so the module also claims ``custom_build_surface`` +
// ``monitor_surface`` on top of the unchanged runtime tier.  Precedent:
// classify_curve_move_tool ships the same combo.  ``unsupportedReason``
// stays VERBATIM (FM6 — the runtime-status copy mirrors the backend
// rationale; it now coexists with live bespoke surfaces, which
// ``whatWorksNow`` already discloses).
//
// FP9 guardrail carried by every surface: cumulative_move_prob_pct is
// Bloomberg's CUMULATIVE signed value (can exceed ±100) — surfaced
// verbatim, never decomposed into hike/cut/hold probabilities.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { WirpMeetingPricingWidget } from './surfaces/monitor/WirpMeetingPricingWidget';
import { WIRP_CENTRAL_BANK_OPTIONS } from './surfaces/wirpMeetingPricingShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_wirp_meeting_pricing_tool',

  // FM3 — tier claims:
  //   * workflow_incompatible — runtime-status tier KEPT verbatim from the
  //     Stage-3 scaffold (backend WORKFLOW_INCOMPATIBLE_TOOLS entry —
  //     list-shaped categorical output, not bridge-composable in V1).
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //     served by the standalone typed-detail bridge, which is live even
  //     though the workflow bridge can't dispatch the tool.
  //   * monitor_surface — the next-meeting implied pricing IS a desk-
  //     glanceable morning read ("when is the next FOMC and what's
  //     priced?"); surface_contract.md §3.4 eligibility.
  tiers: ['workflow_incompatible', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata (kept verbatim from the Stage-3 scaffold)
  displayName: 'WIRP Meeting Pricing',
  category: 'meeting_pricing',
  oneLineSummary: 'Per-meeting WIRP pricing snapshot for one central bank (FOMC / ECB / BOE / BOJ).  Surfaces Bloomberg\'s WIRP-screen fields verbatim per ADR 0009 §1 (INGEST primitive, P12 boundary — NOT recomputed from STIR futures or OIS pricing): implied policy rate, CUMULATIVE move probability, number of 25bp moves priced, implied rate change.  WIRP_MOVE_PROB is cumulative across multiple 25bp moves (range -360.1..548.0 per wirp.yml Stage-B) — emitted as ``cumulative_move_prob_pct`` with explicit methodology disclosure.  No hike/cut/hold decomposition: any single-event identity (max(p,0)/max(-p,0)/100-|p|) is empirically wrong for a cumulative quantity — BOE 2025-05-08 ships -104.9% verbatim, which the identity would render as hold=-4.9% (impossible).  Step-by-step probability derivation is a documented separate primitive (see config.yaml planned_ extensions).  Two selection modes (next_n_meetings vs specific_meeting_date) via a cohesive PR8 central surface; n_meetings defaults from YAML (currently 6).',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5): no shared
  // typedView.  The module owns its own full Build surfaces.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build``
  // is kept === buildExtended for the legacy VirtualPrimitiveCanvas
  // dispatcher that reads ``surfaces.build``.
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact
  // (rendering_density.md §8).  Parameterised on ``central_bank`` only;
  // fetches the SAME /api/v1/rates/detail/wirp-meeting-pricing endpoint
  // with n_meetings=1 (the tile shows the NEXT meeting only).
  monitorWidgets: [
    {
      id: 'wirp_meeting_pricing',
      label: 'WIRP Meeting Pricing',
      description:
        'Next central-bank meeting (FOMC / ECB / BOE / BOJ): meeting date, Bloomberg-implied post-meeting policy rate, and the CUMULATIVE signed 25bp-move probability (verbatim — no hike/cut/hold split).',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'central_bank',
          label: 'Central bank',
          defaultValue: 'FOMC',
          options: WIRP_CENTRAL_BANK_OPTIONS,
        },
      ],
      component: WirpMeetingPricingWidget,
    },
  ],

  // FM5 — defaults mirror the Pydantic Input's structural field
  // (central_bank) plus the cohesive PR8 selection surface's defaults
  // (selection_mode default + YAML default_n_meetings = 6).
  defaultParams: {
    central_bank: 'FOMC',
    selection_mode: 'next_n_meetings',
    n_meetings: '6',
  },

  // FM6 — unsupported reason: KEPT VERBATIM from the Stage-3 scaffold
  // (mirrors the backend WORKFLOW_INCOMPATIBLE_TOOLS rationale).  Note
  // ``whatWorksNow`` already anticipated the bespoke Build typed-view
  // this module now ships.
  unsupportedReason: {
    label: 'calculate_wirp_meeting_pricing',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: List of per-meeting WIRP snapshots — each meeting carries a mix of identifier columns (vendor_ticker + 4 Bloomberg tickers per ADR 0009 §1 provenance) and Bloomberg-ingested numeric fields (implied_policy_rate_pct, cumulative_move_prob_pct, num_25bp_',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
