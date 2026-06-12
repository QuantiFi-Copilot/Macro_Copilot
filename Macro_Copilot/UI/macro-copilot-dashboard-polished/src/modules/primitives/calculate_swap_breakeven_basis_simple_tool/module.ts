// ============================================================================
// src/modules/primitives/calculate_swap_breakeven_basis_simple_tool/module.ts
// ----------------------------------------------------------------------------
// Brought to full parity with calculate_breakeven_inflation_simple_tool
// (Phase-1 pilot) + calculate_cross_market_inflation_swap_spread_tool
// (load-bearing index-family caveat pattern) under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/swap-breakeven-basis + own surfaces; no shared
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { SwapBreakevenBasisWidget } from './surfaces/monitor/SwapBreakevenBasisWidget';
import { SWAP_BREAKEVEN_BASIS_PAIR_OPTIONS } from './surfaces/swapBreakevenBasisShared';

// Same-currency tenor pillars shared across the three pairs at the
// Monitor surface (V1 ingested universe — 5Y / 10Y / 30Y intersect; the
// per-pair Build dropdown narrows to the actual intersection at runtime).
const SWAP_BE_BASIS_MONITOR_TENORS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: '30Y', label: '30Y' },
];

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_swap_breakeven_basis_simple_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — swap-vs-bond inflation basis is the
  //     desk-canonical RV read on inflation-market dislocations and a
  //     natural morning-briefing tile (surface_contract.md §3.4).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Swap-Breakeven Basis',
  category: 'cross_market_rv',
  oneLineSummary:
    "Swap-breakeven basis at a single tenor: same-currency ZCIS rate minus bond-implied breakeven inflation (e.g. USD_ZCIS 10Y minus UST/USD_TIPS 10Y breakeven). Surfaces the liquidity / risk-premium proxy between swap-market and bond-market inflation pricing — NOT a clean liquidity-premium read (also reflects index-lag differences, linker on-the-run effects, and structural ZCIS basis). Sign convention POSITIVE = ZCIS rich vs bond breakeven.",


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Parameterised on (pair, tenor, lookback_days) where ``pair`` is
  // the currency key that uniquely picks the same-currency triplet; the
  // widget expands it to all three curve_family params.  Fetches the SAME
  // /api/v1/rates/detail/swap-breakeven-basis endpoint the Build views use
  // (standalone bridge §5.4).
  monitorWidgets: [
    {
      id: 'swap_breakeven_basis_simple',
      label: 'Swap-Breakeven Basis',
      description:
        'Same-currency swap-vs-bond inflation basis at a single tenor (e.g. USD_ZCIS 10Y minus UST/USD_TIPS 10Y breakeven). Liquidity-premium proxy — index-family differences and linker on-the-run effects also drive the basis.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'pair',
          label: 'Country pair',
          defaultValue: 'USD',
          options: SWAP_BREAKEVEN_BASIS_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: SWAP_BE_BASIS_MONITOR_TENORS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: SwapBreakevenBasisWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires.  Same-currency invariant: the USD triplet is the
  // mockup default (USD_ZCIS + UST + USD_TIPS at 10Y).  ``field_name``
  // defaults to PX_MID so the YAML-resolved default is explicit at the
  // wire layer rather than relying on the schema's empty-string coercion.
  defaultParams: {
    zcis_curve_family: 'USD_ZCIS',
    nominal_curve_family: 'UST',
    linker_curve_family: 'USD_TIPS',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_MID',
  },
};
