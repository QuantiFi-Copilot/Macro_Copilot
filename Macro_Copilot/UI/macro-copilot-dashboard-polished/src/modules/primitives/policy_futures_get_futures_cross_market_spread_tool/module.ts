// ============================================================================
// src/modules/primitives/policy_futures_get_futures_cross_market_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Dispatch 1 — initial dual-view build per the catalog's tool 21 entry
// (build_order 21; policy_futures domain).  Under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-cross-market + own
//     surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Reference twin: calculate_cross_market_inflation_swap_spread_tool (the
// cross-market inflation-swap cousin — same A − B differential KPI shape,
// different curve family).  Secondary twin:
// policy_futures_get_futures_calendar_spread_tool (the just-shipped tool
// 20 — establishes the policy-futures naming-divergence alias pattern
// where the frontend folder + workflow registry use the ``policy_futures_``
// prefix while the MCP function inside policy_futures/mcp_server.py uses
// the unprefixed name, plus the methodology_disclosure threading pattern,
// the per-curve CURVE_REGISTRY, and the inverse-pricing-flag treatment).
//
// Mockup-faithful design captured in surfaces/BuildExtended.tsx +
// surfaces/BuildCompact.tsx; compact view uses the SHELL-STANDARD density
// per the Option-(c) precedent (catalog design_guardrail #4).  See
// THESIS.md → Mockup conformance for the deferral rationale.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { PolicyFuturesCrossMarketSpreadWidget } from './surfaces/monitor/PolicyFuturesCrossMarketSpreadWidget';
import {
  POLICY_FUTURES_CURVE_OPTIONS,
  STRIP_POSITION_OPTIONS,
} from './surfaces/futuresCrossMarketSpreadShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName).  Per the catalog design
  // guardrail #3: the FRONTEND module identity is the
  // ``policy_futures_`` prefixed name (matches the workflow registry +
  // tool_metadata DB row); the MCP function inside
  // policy_futures/mcp_server.py uses the unprefixed
  // ``get_futures_cross_market_spread_tool`` name and is bridged via
  // the KNOWN_TOOL_ALIASES entry in src/lib/toolNames.ts.
  toolName: 'policy_futures_get_futures_cross_market_spread_tool',

  // FM3 — tier claims (catalog required_tiers):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — STIR cross-market spreads are the desk-canonical
  //     cross-CB policy-divergence read on the front-end strip; surfacing
  //     them as a glanceable tile fits surface_contract.md §3.4
  //     eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Policy Futures Cross-Market Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Matched-strip cross-market implied-rate differential between two policy-futures curve families at one strip position (e.g. SOFR_FUT vs SONIA_FUT strip 1 = SFR1 − SFI1, SOFR_FUT vs EUR_SHORT_RATE_FUT strip 4 = SFR4 − ER4). Wire-frozen A − B sign convention in PERCENT POINTS; display in bps (positive = leg A pricing above leg B at this strip slot — cross-CB divergence direction). Carries 1d change, rolling 252d z-score, trailing 252d range + percentile, per-leg disclosure block (strip-slot master stems + current-front underlying contracts) AND per-leg short-rate regime labels (RFR vs IBOR — surfaced INDEPENDENTLY for mixed-regime pairs; NO pack-average collapse). RAW differential — NOT basis-adjusted, NOT beta-adjusted.',

  // FM9 — STANDALONE pattern per methodology_exposure.md §5: no shared
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

  // FM5c — Monitor catalog widget.  Per rendering_density.md §8
  // inherently compact — renders at small/medium widget sizes in the
  // bento grid.  Parameterised on (curve_family_a, curve_family_b,
  // strip_position, lookback_days); fetches the SAME typed-detail
  // endpoint the Build views use per the standalone-bridge contract.
  monitorWidgets: [
    {
      id: 'policy_futures_cross_market_spread',
      label: 'Policy Futures Cross-Market Spread',
      description:
        'Matched-strip cross-CB implied-rate differential between two policy-futures curve families (e.g. SOFR_FUT vs EUR_SHORT_RATE_FUT strip 1). Wire A − B convention; 1d change, 252d z-score, range strip. Surfaces mixed-regime (RFR vs IBOR) call-out inline.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family_a',
          label: 'Leg A',
          defaultValue: 'SOFR_FUT',
          options: POLICY_FUTURES_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'curve_family_b',
          label: 'Leg B',
          defaultValue: 'EUR_SHORT_RATE_FUT',
          options: POLICY_FUTURES_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'strip_position',
          label: 'Strip Position',
          defaultValue: '1',
          options: STRIP_POSITION_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: PolicyFuturesCrossMarketSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural Pydantic Input fields the
  // backend requires.  Mockup default: SOFR_FUT vs EUR_SHORT_RATE_FUT
  // strip 1 (SFR1 − ER1 front-quarter cross-CB divergence — the
  // mockup's SFR-ER Pos1 Cross-CB Spread).  ``field_name`` defaults to
  // 'PX_LAST' — the policy-futures domain's ``default_price_field``
  // convention sentinel.  Strip position is a STRING here because
  // PrimitiveModuleSpec.defaultParams is Record<string, string>; coerce
  // at the Build surface boundary.
  defaultParams: {
    curve_family_a: 'SOFR_FUT',
    curve_family_b: 'EUR_SHORT_RATE_FUT',
    strip_position: '1',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
