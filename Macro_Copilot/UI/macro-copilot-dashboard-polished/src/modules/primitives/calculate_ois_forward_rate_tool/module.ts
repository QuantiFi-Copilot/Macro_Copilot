// ============================================================================
// src/modules/primitives/calculate_ois_forward_rate_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view + monitor module under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at
//     /api/v1/rates/detail/ois-forward-rate + its own frontend surfaces;
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
// FM1 identity note: the folder name `calculate_ois_forward_rate_tool`
// matches the MCP function + workflow registry name exactly — no alias
// bridge needed (mirrors the sibling calculate_ois_curve_spread_tool /
// calculate_ois_butterfly_tool, distinct from the get-verb get_ois_rate_level_tool).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import {
  OIS_CURVE_OPTIONS,
  OIS_FORWARD_PAIR_OPTIONS,
} from './surfaces/oisForwardRateShared';
import { OisForwardRateWidget } from './surfaces/monitor/OisForwardRateWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_ois_forward_rate_tool',

  // FM3 — tier claims.
  //   * runtime status: generic_runnable (backend ships in _PRIMITIVE_SPECS)
  //   * custom_build_surface — the dual-view Build contract
  //     (rendering_density.md §1: both buildExtended + buildCompact)
  //   * monitor_surface — desk-glanceable per surface_contract.md §3.4
  //     (OIS forwards are the canonical risk-neutral term-structure read
  //     for policy-path nowcasting — 5Y5Y SOFR is the long-run policy
  //     anchor the desk parks on morning-briefing boards)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'OIS Forward Rate',
  category: 'forward_rate',
  oneLineSummary:
    'Implied forward rate spanning two tenors on an OIS curve (1Y1Y, 5Y5Y, 2Y1Y, etc) via the dual-compounding bootstrap (simple for T ≤ 1Y, annual for T > 1Y).  Carries forward_rate_pct + 1-day change in bps + rolling 252d z-score + trailing 252d high/low/percentile + interpolated start/end spot rates.  Risk-neutral implied policy-path read; OIS forwards price the EXPECTED policy path, not realised central-bank decisions.',

  // own full Build surfaces.

  // FM8 — surface refs (rendering_density.md §5):
  //   * buildExtended — full canvas, mounted for single-tool queries
  //   * buildCompact  — grid card, mounted as a node in multi-tool DAGs
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per
  // docs_revamped/03_standards/rendering_density.md §8 the Monitor
  // surface is INHERENTLY COMPACT (no separate compact/extended split);
  // the widget renders at small/medium sizes inside the bento grid.
  // Parameterised on (curve_family, forward_pair, lookback_days); fetches
  // the SAME typed-detail endpoint
  // (/api/v1/rates/detail/ois-forward-rate) per the standalone-bridge
  // contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'ois_forward_rate',
      label: 'OIS Forward Rate',
      description:
        'OIS implied forward rate (level + z-score + 252d range) for one (curve_family, forward_pair) — e.g. SOFR 5Y5Y. Risk-neutral implied policy-path read.',
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
          name: 'forward_pair',
          label: 'Forward',
          defaultValue: '5Y5Y',
          options: OIS_FORWARD_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: OisForwardRateWidget,
    },
  ],

  // FM5 — defaults.  Mockup default: USD_SOFR_OIS 5Y5Y (the long-run
  // policy-anchor forward — SOFR 5Y5Y is the desk-canonical secular
  // implied-policy read; ESTR 5Y5Y / SONIA 5Y5Y are its EUR / GBP
  // analogues).  start_tenor / end_tenor are also pre-set so the
  // backend's tenor-mode input accepts the params dict directly (the
  // Build surfaces re-derive both from forward_pair on change, keeping
  // them in sync).
  defaultParams: {
    curve_family: 'USD_SOFR_OIS',
    forward_pair: '5Y5Y',
    start_tenor: '5Y',
    end_tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
