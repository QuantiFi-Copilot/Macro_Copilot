// ============================================================================
// src/lib/monitorParamOptions.ts — option arrays for Monitor widget params.
// ----------------------------------------------------------------------------
// Stage 4d — extracted from src/components/monitor/registry.ts so per-
// module ``module.ts`` files can value-import the option arrays for
// their ``monitorWidgets[i].paramFields`` declarations without
// re-entering the monitor page-shell (FP12 + cycle avoidance).
//
// ``src/components/monitor/registry.ts`` re-exports the same arrays
// for back-compat with the few non-module consumers (the config-form
// renderer + existing tests) that import them from there.
// ============================================================================

/** Sovereign curve families exposed in Monitor widget config forms.
 *  Mirrors the substrate's registered curve_family values (shared/schemas
 *  + rates_agent/playbooks).  Keep aligned with the backend; changes
 *  here that drift from the registered families produce 4xx on the
 *  detail-endpoint calls. */
export const CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'UST', label: 'UST · US Treasuries' },
  { value: 'DE_BUND', label: 'Bund · German' },
  { value: 'UK_GILT', label: 'Gilt · UK' },
  { value: 'JGB', label: 'JGB · Japan' },
  { value: 'FR_OAT', label: 'OAT · France' },
  { value: 'IT_BTP', label: 'BTP · Italy' },
  { value: 'ES_BONO', label: 'Bono · Spain' },
  { value: 'AU_GOVT', label: 'AUS · Australia' },
  { value: 'CANADA_GOVT', label: 'CAN · Canada' },
];

export const TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '2Y', label: '2Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: '30Y', label: '30Y' },
];

export const LOOKBACK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '252', label: '1Y · 252 trading days' },
  { value: '504', label: '2Y · 504 days' },
  { value: '1260', label: '5Y · 1260 days' },
];

// ----------------------------------------------------------------------------
// Linker (inflation-indexed bond) curve + tenor options.
// ----------------------------------------------------------------------------
// Separate from the sovereign CURVE_OPTIONS above because the linker
// universe is disjoint — different curve_family codes, different
// tenor sets per family.  Used by Monitor widgets for the
// inflation-indexed-bonds primitives (real_yield_level, breakeven
// primitives, real-yield curve spreads).  Tenor sets per family
// mirror the institutional doc + manifest universe at
// manifesto/01_instruments/rates_agent/04_inflation_indexed_bonds.md.

export const LINKER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'USD_TIPS', label: 'TIPS · US' },
  { value: 'GBP_LINKER', label: 'Linker · UK' },
  { value: 'EUR_FR_LINKER', label: 'OATei · France' },
  { value: 'CAD_RRB', label: 'RRB · Canada' },
];

/** Tenor sets per linker curve_family.  Empty array fallback for an
 *  unrecognised curve_family — caller's responsibility to validate. */
export const LINKER_TENOR_OPTIONS_BY_CURVE: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_TIPS: ['5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  GBP_LINKER: ['1Y', '2Y', '3Y', '5Y', '10Y', '15Y', '20Y', '30Y', '50Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  EUR_FR_LINKER: ['2Y', '5Y', '7Y', '10Y', '15Y'].map((t) => ({
    value: t,
    label: t,
  })),
  CAD_RRB: ['5Y', '10Y', '15Y', '20Y', '25Y', '30Y'].map((t) => ({
    value: t,
    label: t,
  })),
};

/** Default linker tenor set when the curve_family hasn't been selected
 *  yet — used by the Monitor add-widget form's initial render. */
export const LINKER_DEFAULT_TENORS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: '30Y', label: '30Y' },
];
