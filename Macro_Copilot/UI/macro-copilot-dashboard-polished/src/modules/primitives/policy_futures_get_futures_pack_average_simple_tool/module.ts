// ============================================================================
// src/modules/primitives/policy_futures_get_futures_pack_average_simple_tool/module.ts
// ----------------------------------------------------------------------------
// Dispatch 1 — initial dual-view build per the catalog's tool 22 entry
// (build_order 22; policy_futures domain).  Under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-pack-average + own
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Reference twin: calculate_ois_forward_rate_tool (the snapshot/forward
// cousin — same single-implied-rate-in-PERCENT KPI shape, different
// curve family).  Secondary twin:
// policy_futures_get_futures_cross_market_spread_tool (the just-shipped
// tool 21 — establishes the policy-futures naming-divergence alias
// pattern where the frontend folder + workflow registry use the
// ``policy_futures_`` prefix while the MCP function inside
// policy_futures/mcp_server.py uses the unprefixed name, plus the
// methodology_disclosure threading pattern, the per-curve CURVE_REGISTRY,
// and the inverse-pricing-flag treatment).
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
import { PolicyFuturesPackAverageWidget } from './surfaces/monitor/PolicyFuturesPackAverageWidget';
import {
  PACK_OPTIONS,
  POLICY_FUTURES_CURVE_OPTIONS,
} from './surfaces/futuresPackAverageSimpleShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName).  Per the catalog design
  // guardrail #3: the FRONTEND module identity is the
  // ``policy_futures_`` prefixed name (matches the workflow registry +
  // tool_metadata DB row); the MCP function inside
  // policy_futures/mcp_server.py uses the unprefixed
  // ``get_futures_pack_average_simple_tool`` name and is bridged via
  // the KNOWN_TOOL_ALIASES entry in src/lib/toolNames.ts.
  toolName: 'policy_futures_get_futures_pack_average_simple_tool',

  // FM3 — tier claims (catalog required_tiers):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — STIR pack averages are the desk-canonical
  //     year-anchored policy-path read; surfacing them as a glanceable
  //     tile fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Policy Futures Pack Average',
  category: 'aggregates',
  oneLineSummary:
    'Pack average across 4 consecutive quarterly STIR contracts (whites = SFR1..SFR4 / SFI1..SFI4 = positions 1-4; reds = SFR5..SFR8 / SFI5..SFI8 = positions 5-8) on ONE policy-futures curve family — the desk-canonical year-anchored implied-policy-path read.  Wire returns the pack-average implied rate in PERCENT (arithmetic mean of the four per-leg rates derived from the per-strip inverse-pricing flag), 1-day change in PERCENT POINTS (display × 100 → bps), rolling 252d z-score, trailing 252d high/low/percentile + per-leg disclosure block.  V1 executes on SOFR_FUT + SONIA_FUT; EUR_SHORT_RATE_FUT returns a clean controlled-error envelope per ADR 0013 V1 scope.  Simple arithmetic mean — NOT duration-weighted, NOT meeting-by-meeting, NOT CTD-of-OIS (PR11 planned-extension territory).',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per rendering_density.md §8
  // inherently compact — renders at small/medium widget sizes in the
  // bento grid.  Parameterised on (curve_family, pack, lookback_days);
  // fetches the SAME typed-detail endpoint the Build views use per the
  // standalone-bridge contract.
  monitorWidgets: [
    {
      id: 'policy_futures_pack_average',
      label: 'Policy Futures Pack Average',
      description:
        'STIR pack-average implied rate (arithmetic mean across 4 consecutive quarterly contracts) on one policy-futures curve_family — e.g. SOFR_FUT whites (SFR1..SFR4) front-year implied-policy-path read.  Carries level, 1d change, 252d z-score + percentile + range strip.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Curve',
          defaultValue: 'SOFR_FUT',
          options: POLICY_FUTURES_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'pack',
          label: 'Pack',
          defaultValue: 'whites',
          options: PACK_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: PolicyFuturesPackAverageWidget,
    },
  ],

  // FM5 — defaults mirror the structural Pydantic Input fields the
  // backend requires.  Mockup default: SOFR_FUT whites (the SFR whites
  // pack — the desk-canonical front-year US policy-path read).
  // ``field_name`` defaults to 'PX_LAST' — the policy-futures domain's
  // ``default_price_field`` convention sentinel.  V1 picks an
  // executable curve_family + pack so the very first render against
  // the typed-detail endpoint resolves without hitting the
  // EUR_SHORT_RATE_FUT compute-layer refusal envelope.
  defaultParams: {
    curve_family: 'SOFR_FUT',
    pack: 'whites',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
