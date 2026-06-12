// ============================================================================
// src/modules/primitives/get_ois_rate_level_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view + monitor module under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at
//     /api/v1/rates/detail/ois-rate-level + its own frontend surfaces;
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder, committed alongside the module per the project's mockup-first
// workflow.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No
// side effects, no global mutation, no register() calls.
//
// FM1 identity note: the folder name `get_ois_rate_level_tool` matches the
// backend-canonical name; the manifest historically published the verb-
// mismatched form `calculate_ois_rate_level_tool` which the TS aliaser at
// src/lib/toolNames.ts (`KNOWN_TOOL_ALIASES`) bridges back to this folder.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import {
  OIS_CURVE_OPTIONS,
  OIS_DEFAULT_TENORS,
} from './surfaces/oisRateLevelShared';
import { OisRateLevelWidget } from './surfaces/monitor/OisRateLevelWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'get_ois_rate_level_tool',

  // FM3 — tier claims.
  //   * runtime status: generic_runnable (backend ships in _PRIMITIVE_SPECS)
  //   * custom_build_surface — the dual-view Build contract
  //     (rendering_density.md §1: both buildExtended + buildCompact)
  //   * monitor_surface — desk-glanceable per surface_contract.md §3.4
  //     (OIS rate levels are a canonical daily PM read — the risk-neutral
  //     implied policy path for the desk-anchor currency)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'OIS Rate Level',
  category: 'snapshots',
  oneLineSummary:
    'Single-tenor OIS par-swap-rate snapshot — current rate, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, plus full chartable time series.  OIS analogue of get_yield_levels.',


  // FM8 — surface refs (rendering_density.md §5):
  //   * buildExtended — full canvas, mounted for single-tool queries
  //   * buildCompact  — grid card, mounted as a node in multi-tool DAGs
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per
  // docs_revamped/03_standards/rendering_density.md §8 the Monitor surface
  // is INHERENTLY COMPACT (no separate compact/extended split); the widget
  // renders at small/medium sizes inside the bento grid.  Parameterised on
  // (curve_family, tenor, lookback_days) — same inputs the Build surfaces
  // use; fetches the SAME typed-detail endpoint
  // (/api/v1/rates/detail/ois-rate-level) per the standalone-bridge
  // contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'ois_rate_level',
      label: 'OIS Rate Level',
      description:
        'OIS par-swap rate (level + z-score + 252d range) for one curve point. Risk-neutral implied policy path read.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'OIS Curve',
          defaultValue: 'USD_SOFR_OIS',
          options: OIS_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '2Y',
          options: OIS_DEFAULT_TENORS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: OisRateLevelWidget,
    },
  ],

  // FM5 — defaults.  Mockup default: USD_SOFR_OIS 2Y (SOFR policy-shape
  // focus — the desk-canonical front-end read on Fed implied policy path).
  // Mirrors the catalog entry's recommended defaults.
  defaultParams: {
    curve_family: 'USD_SOFR_OIS',
    tenor: '2Y',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
