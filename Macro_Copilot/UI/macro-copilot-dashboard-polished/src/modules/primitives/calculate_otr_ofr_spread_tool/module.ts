// ============================================================================
// src/modules/primitives/calculate_otr_ofr_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Sovereign cash-bond OTR/OFR yield spread brought to parity with the
// Phase-1 pilot calculate_breakeven_inflation_simple_tool under the
// dual-view + standalone-bridge contracts:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/otr-ofr-spread + own surfaces;
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { OtrOfrSpreadWidget } from './surfaces/monitor/OtrOfrSpreadWidget';
import {
  COUNTRY_OPTIONS,
  TENOR_OPTIONS,
} from './surfaces/otrOfrSpreadShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_otr_ofr_spread_tool',

  // FM3 — tier claims (parity with the breakeven Phase-1 pilot):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — OTR/OFR is a canonical morning-briefing /
  //     desk-board read (cash-bond rich-cheap / liquidity-premium proxy)
  //     per surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'OTR-OFR Spread',
  category: 'curve_shape',
  oneLineSummary:
    "On-the-run vs first-off-the-run sovereign bond yield spread for one (country, tenor) slot — the desk-standard liquidity-premium PROXY: spread_bps = (OTR yield − OFR yield) × 100, with today's move and how stretched it is vs the trailing year.  Sign POSITIVE = OTR cheap to OFR (inverted-liquidity signature); NEGATIVE = OTR rich (typical signature).  Liquidity-premium proxy, not a clean liquidity read — deviations can also reflect bond-specific scarcity / squeeze / repo-rate differences.",


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Single-slot primitive — exposes (country, tenor) plus the
  // lookback selector.  Fetches the SAME /api/v1/rates/detail/otr-ofr-spread
  // endpoint the Build views use (standalone bridge §5.4).
  monitorWidgets: [
    {
      id: 'otr_ofr_spread',
      label: 'OTR-OFR Spread',
      description:
        'On-the-run vs first-off-the-run sovereign bond yield spread for one (country, tenor) slot.  Liquidity-premium PROXY; sign POSITIVE = OTR cheap to OFR.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'country',
          label: 'Country',
          defaultValue: 'US',
          options: COUNTRY_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: TENOR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: OtrOfrSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the mockup (US 10Y at 365d).  ``field_name``
  // defaults to the YAML's default_field_name (YLD_YTM_MID) so the wire
  // sentinel pattern is honoured.
  defaultParams: {
    country: 'US',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
