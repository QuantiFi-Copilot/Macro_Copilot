// ============================================================================
// Curve / tenor universe — the canonical option set
// ----------------------------------------------------------------------------
// A small static catalogue of (curve_family, tenor) values the workspace
// surfaces in dropdowns.  These are the values the desk's primitives expect;
// they're maintained client-side because a wire endpoint listing them does
// not exist yet (and they almost never change).  The user can still type a
// custom value in any free-text picker — these are hint chips, not gates.
// ============================================================================

export const SOVEREIGN_CURVES: { value: string; label: string }[] = [
  { value: 'UST', label: 'UST · US Treasury' },
  { value: 'DE_BUND', label: 'DE_BUND · Germany' },
  { value: 'FR_OAT', label: 'FR_OAT · France' },
  { value: 'IT_BTP', label: 'IT_BTP · Italy' },
  { value: 'ES_BONO', label: 'ES_BONO · Spain' },
  { value: 'UK_GILT', label: 'UK_GILT · United Kingdom' },
  { value: 'JGB', label: 'JGB · Japan' },
];

export const OIS_CURVES: { value: string; label: string }[] = [
  { value: 'USD_SOFR_OIS', label: 'USD SOFR OIS' },
  { value: 'EUR_ESTR_OIS', label: 'EUR ESTR OIS' },
  { value: 'GBP_SONIA_OIS', label: 'GBP SONIA OIS' },
  { value: 'JPY_OIS', label: 'JPY OIS' },
];

export const ALL_CURVES = [...SOVEREIGN_CURVES, ...OIS_CURVES];

export const CANONICAL_TENORS: string[] = [
  '3M',
  '6M',
  '1Y',
  '2Y',
  '3Y',
  '5Y',
  '7Y',
  '10Y',
  '20Y',
  '30Y',
];

export const CANONICAL_FIELDS: { value: string; label: string }[] = [
  { value: '', label: 'default (config.yaml)' },
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID — sovereign yield' },
  { value: 'PX_LAST', label: 'PX_LAST — OIS / swap rate' },
];
