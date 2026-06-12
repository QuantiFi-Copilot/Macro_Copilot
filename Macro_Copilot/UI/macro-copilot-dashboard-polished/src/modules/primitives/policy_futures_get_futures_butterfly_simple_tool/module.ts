// ============================================================================
// src/modules/primitives/policy_futures_get_futures_butterfly_simple_tool/module.ts
// ----------------------------------------------------------------------------
// Dispatch 1 — initial dual-view build per the catalog's tool 19 entry
// (build_order 19; policy_futures domain).  Under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-butterfly + own
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Reference twin: calculate_ois_butterfly_tool (the OIS-side single-curve
// 3-leg butterfly cousin — same butterfly KPI shape, bps headline +
// sparkline with z-score bands + belly cheap/rich sign convention).
// Secondary twin: policy_futures_get_futures_price_level_tool (the STIR
// price-level sibling — establishes the policy-futures naming divergence
// where the frontend folder + workflow registry use the
// ``policy_futures_`` prefix while the MCP function inside
// policy_futures/mcp_server.py uses the unprefixed name, plus the
// 100-minus-rate quote convention treatment).
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
import { PolicyFuturesButterflyWidget } from './surfaces/monitor/PolicyFuturesButterflyWidget';
import {
  BUTTERFLY_TRIPLETS_BY_CURVE,
  POLICY_FUTURES_CURVE_OPTIONS,
} from './surfaces/futuresButterflySimpleShared';

// Triplet options for the Monitor widget — registered (wing_short, body,
// wing_long) presets surfaced by label so the catalog form can dispatch
// only valid orderings.  The static catalog form layer does not (yet)
// support cross-field dynamic options, so we surface the UNION of valid
// triplet labels across all three V1 curve families; triplets that are
// not valid for the user's selected curve fall back gracefully — the
// widget's fetchParams memo looks up the chosen label in
// BUTTERFLY_TRIPLETS_BY_CURVE[curve] and snaps to triplets[0] when the
// label is absent.  Default value '1-2-3' is in every family's set so
// the default curve renders cleanly.
const BUTTERFLY_TRIPLET_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const triplets of Object.values(BUTTERFLY_TRIPLETS_BY_CURVE)) {
    for (const t of triplets) {
      if (seen.has(t.label)) continue;
      seen.add(t.label);
      out.push({ value: t.label, label: t.label });
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
  // ``get_futures_butterfly_simple_tool`` name and is bridged via the
  // KNOWN_TOOL_ALIASES entry in src/lib/toolNames.ts.
  toolName: 'policy_futures_get_futures_butterfly_simple_tool',

  // FM3 — tier claims (catalog required_tiers):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — STIR strip butterflies are a desk-canonical
  //     front-end policy-curvature read; surfacing them as a glanceable
  //     tile fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Policy Futures Butterfly',
  category: 'curvature',
  oneLineSummary:
    'Same-curve simple butterfly (3-point implied-rate curvature) on the policy-futures strip (e.g. SFR 1-2-3, ER 1-2-4). FIXED 50-50 weighting: body − 0.5 × (wing_short + wing_long), per-leg implied rates derived from 100-minus-rate raw prices for SFR / ER / SFI. Positive = belly cheap; negative = belly rich. Carries the butterfly value in PERCENT POINTS on the wire (rendered in bps in the display) plus 1d change, rolling 252d z-score, trailing 252d range + percentile, and per-leg disclosure (strip-slot master stems + current-front underlying contracts).',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per rendering_density.md §8 inherently
  // compact — renders at small/medium widget sizes in the bento grid.
  // Parameterised on (curve_family, triplet, lookback_days); fetches the
  // SAME typed-detail endpoint the Build views use per the standalone-
  // bridge contract.
  monitorWidgets: [
    {
      id: 'policy_futures_butterfly',
      label: 'Policy Futures Butterfly',
      description:
        'STIR strip butterfly (3-point implied-rate curvature) on one policy-futures curve family (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT) with 1d change, 252d z-score, and range strip.',
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
          name: 'triplet',
          label: 'Triplet',
          defaultValue: '1-2-3',
          options: BUTTERFLY_TRIPLET_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: PolicyFuturesButterflyWidget,
    },
  ],

  // FM5 — defaults mirror the structural Pydantic Input fields the
  // backend requires.  Mockup default: SOFR_FUT SFR 1-2-3 (front-pack
  // curvature).  ``field_name`` defaults to 'PX_LAST' — the
  // policy-futures domain's ``default_price_field`` convention sentinel.
  // Strip positions are STRINGS here because
  // PrimitiveModuleSpec.defaultParams is Record<string, string>; coerce
  // at the Build surface boundary.
  defaultParams: {
    curve_family: 'SOFR_FUT',
    strip_position_wing_short: '1',
    strip_position_body: '2',
    strip_position_wing_long: '3',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
