// ============================================================================
// src/modules/primitives/policy_futures_get_futures_price_level_tool/module.ts
// ----------------------------------------------------------------------------
// Dispatch 1 — initial dual-view build per the catalog's tool 9 entry
// (build_order 9; policy_futures domain).  Under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-price + own
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Reference twin: get_real_yield_level_tool (snapshot shape — level +
// daily change + 252d z-score + range strip).  Mockup-faithful design
// captured in surfaces/BuildExtended.tsx + surfaces/BuildCompact.tsx;
// compact view uses the SHELL-STANDARD density per the Option-(c)
// precedent recorded in catalog last_resolution_applied.  See THESIS.md
// → Mockup conformance for the deferral rationale.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { PolicyFuturesPriceLevelWidget } from './surfaces/monitor/PolicyFuturesPriceLevelWidget';
import {
  POLICY_FUTURES_CURVE_OPTIONS,
  STRIP_POSITION_OPTIONS,
} from './surfaces/policyFuturesPriceShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'policy_futures_get_futures_price_level_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — STIR strip-position price is a desk-canonical
  //     morning read; surfacing as a glanceable tile fits the
  //     surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Policy Futures Price Level',
  category: 'snapshots',
  oneLineSummary:
    'Single policy-futures strip-position price + desk-recognised IMPLIED RATE (PERCENT) for one (curve_family, strip_position) pair (e.g. SFR1, ER2, SFI1). Carries 1-day raw-price + implied-rate changes (bps), rolling 252d z-score of the implied rate, trailing 252d high/low/mid/percentile, and the as_of-bounded SCD2 per-strip disclosure (underlying_contract_code, security_name, expiry_date, tick size/value, inverse_priced flag, short_rate_regime label).',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per rendering_density.md §8 inherently
  // compact — renders at small/medium widget sizes in the bento grid.
  // Parameterised on (curve_family, strip_position, lookback_days); fetches
  // the SAME typed-detail endpoint the Build views use per the standalone-
  // bridge contract.
  monitorWidgets: [
    {
      id: 'policy_futures_price_level',
      label: 'Policy Futures Price Level',
      description:
        'STIR strip-position price + implied rate (percent) for one (curve_family, strip_position) pair (SFR1 / SFR2 / ER1 / SFI1 / ...) with 1d change, 252d z-score, and range strip.',
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
          name: 'strip_position',
          label: 'Strip position',
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
      component: PolicyFuturesPriceLevelWidget,
    },
  ],

  // FM5 — defaults mirror the structural Pydantic Input fields the
  // backend requires (curve_family, strip_position) plus the YAML-locked
  // ``default_price_field`` convention sentinel.  Strip position is a
  // STRING here because PrimitiveModuleSpec.defaultParams is
  // Record<string, string>; coerce at the Build surface boundary.
  defaultParams: {
    curve_family: 'SOFR_FUT',
    strip_position: '1',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
