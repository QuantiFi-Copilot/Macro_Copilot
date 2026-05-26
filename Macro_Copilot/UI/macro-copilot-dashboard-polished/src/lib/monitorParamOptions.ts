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
