// ============================================================================
// src/modules/primitives/policy_futures_get_futures_calendar_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Dispatch 1 — initial dual-view build per the catalog's tool 20 entry
// (build_order 20; policy_futures domain).  Under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-calendar + own
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Reference twin: calculate_ois_curve_spread_tool (the OIS-side single-
// curve 2-point tenor-spread cousin — same spread KPI shape, bps headline
// + sparkline with z-score bands + steeper/flatter sign convention).
// Secondary twin: policy_futures_get_futures_butterfly_simple_tool (the
// just-shipped tool 19 — establishes the policy-futures naming divergence
// where the frontend folder + workflow registry use the
// ``policy_futures_`` prefix while the MCP function inside
// policy_futures/mcp_server.py uses the unprefixed name, plus the
// 100-minus-rate quote convention treatment + wire-honest
// ``methodology_disclosure`` threading pattern + the per-curve
// CURVE_REGISTRY + defensive useEffect ordering guard).
//
// Mockup-faithful design captured in surfaces/BuildExtended.tsx +
// surfaces/BuildCompact.tsx; compact view uses the SHELL-STANDARD density
// per the Option-(c) precedent (catalog design_guardrail #5).  See
// THESIS.md → Mockup conformance for the deferral rationale.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { PolicyFuturesCalendarSpreadWidget } from './surfaces/monitor/PolicyFuturesCalendarSpreadWidget';
import {
  CALENDAR_PAIRS_BY_CURVE,
  POLICY_FUTURES_CURVE_OPTIONS,
} from './surfaces/futuresCalendarSpreadShared';

// Pair options for the Monitor widget — registered (short, long) presets
// surfaced by label so the catalog form can dispatch only valid orderings.
// The static catalog form layer does not (yet) support cross-field dynamic
// options, so we surface the UNION of valid pair labels across all three V1
// curve families; pairs that are not valid for the user's selected curve
// fall back gracefully — the widget's fetchParams memo looks up the chosen
// label in CALENDAR_PAIRS_BY_CURVE[curve] and snaps to pairs[0] when the
// label is absent.  Default value '1-3' is in every family's set so the
// default curve renders cleanly.
const CALENDAR_PAIR_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const pairs of Object.values(CALENDAR_PAIRS_BY_CURVE)) {
    for (const p of pairs) {
      if (seen.has(p.label)) continue;
      seen.add(p.label);
      out.push({ value: p.label, label: p.label });
    }
  }
  return out;
})();

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName).  Per the catalog design
  // guardrail #3: the FRONTEND module identity is the
  // ``policy_futures_`` prefixed name (matches the workflow registry +
  // tool_metadata DB row); the MCP function inside
  // policy_futures/mcp_server.py uses the unprefixed
  // ``get_futures_calendar_spread_tool`` name and is bridged via the
  // KNOWN_TOOL_ALIASES entry in src/lib/toolNames.ts.
  toolName: 'policy_futures_get_futures_calendar_spread_tool',

  // FM3 — tier claims (catalog required_tiers):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — STIR calendar spreads are the desk-canonical
  //     front-end policy-path slope read; surfacing them as a glanceable
  //     tile fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Policy Futures Calendar Spread',
  category: 'curve_shape',
  oneLineSummary:
    'Same-curve calendar spread between two strip-position slots on one policy-futures family (e.g. SFR 1-3, ER 1-4). Spread is on the IMPLIED-RATE axis: WIRE convention front-leg − back-leg in PERCENT POINTS; DISPLAY convention back-leg − front-leg in bps (positive = back rate HIGHER than front = steeper policy path). Underlying STIR contracts quote inverse (100-minus-rate) for SFR / ER / SFI. Carries 1d change, rolling 252d z-score, trailing 252d range + percentile, and per-leg disclosure (strip-slot master stems + current-front underlying contracts).',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per rendering_density.md §8 inherently
  // compact — renders at small/medium widget sizes in the bento grid.
  // Parameterised on (curve_family, pair, lookback_days); fetches the
  // SAME typed-detail endpoint the Build views use per the standalone-
  // bridge contract.
  monitorWidgets: [
    {
      id: 'policy_futures_calendar_spread',
      label: 'Policy Futures Calendar Spread',
      description:
        'STIR strip calendar spread (2-point implied-rate slope) on one policy-futures curve family (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT) with 1d change, 252d z-score, and range strip.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Policy futures curve',
          defaultValue: 'SOFR_FUT',
          options: POLICY_FUTURES_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'pair',
          label: 'Pair',
          defaultValue: '1-3',
          options: CALENDAR_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: PolicyFuturesCalendarSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural Pydantic Input fields the
  // backend requires.  Mockup default: SOFR_FUT SFR 1-3 (front-pack
  // slope).  ``field_name`` defaults to 'PX_LAST' — the policy-futures
  // domain's ``default_price_field`` convention sentinel.  Strip
  // positions are STRINGS here because
  // PrimitiveModuleSpec.defaultParams is Record<string, string>; coerce
  // at the Build surface boundary.
  defaultParams: {
    curve_family: 'SOFR_FUT',
    strip_position_short: '1',
    strip_position_long: '3',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
