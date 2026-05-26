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
  // Stage 1 — additional sovereign families present in
  // rates_agent/playbooks/sovereign_bonds.yml on `build` today.
  { value: 'AU_GOVT', label: 'AU_GOVT · Australia' },
  { value: 'CANADA_GOVT', label: 'CANADA_GOVT · Canada' },
];

export const OIS_CURVES: { value: string; label: string }[] = [
  { value: 'USD_SOFR_OIS', label: 'USD SOFR OIS' },
  { value: 'EUR_ESTR_OIS', label: 'EUR ESTR OIS' },
  { value: 'GBP_SONIA_OIS', label: 'GBP SONIA OIS' },
  { value: 'JPY_OIS', label: 'JPY OIS' },
  // Stage 1 — additional OIS families present in
  // rates_agent/playbooks/ois.yml on `build` today.
  { value: 'AUD_OIS', label: 'AUD OIS' },
  { value: 'CAD_OIS', label: 'CAD OIS' },
];

// ----------------------------------------------------------------------------
// Stage 1 — curve-family option sets for the inflation, ZCIS, RFR,
// bond-futures, and policy-futures domains.  Backend playbooks live
// at:
//   - rates_agent/playbooks/inflation_indexed_bonds.yml  (USD_TIPS,
//     GBP_LINKER, EUR_FR_LINKER, CAD_RRB)
//   - rates_agent/playbooks/inflation_swaps.yml          (USD_ZCIS,
//     EUR_ZCIS, GBP_ZCIS)
//   - rates_agent/playbooks/overnight_rfr.yml            (USD_SOFR_RFR,
//     EUR_ESTR_RFR, GBP_SONIA_RFR, JPY_TONA_RFR)
//   - rates_agent/playbooks/bond_futures.yml             (UST_FUT,
//     DE_FUT, UK_FUT, IT_FUT, FR_FUT, ES_FUT, CA_FUT, AU_FUT, JP_FUT)
//   - rates_agent/playbooks/policy_futures.yml           (SOFR_FUT,
//     EUR_SHORT_RATE_FUT, SONIA_FUT)
//
// These appear in ``ALL_CURVES`` below so the rich-model builder
// (PCA, rolling_regression, attribution, half_life, beta_adjusted
// spread) exposes them in the dropdown.  Per backend PR5 the
// rich-model primitives accept any registered ``curve_family``.

export const INFLATION_LINKER_CURVES: { value: string; label: string }[] = [
  { value: 'USD_TIPS', label: 'USD_TIPS · US TIPS' },
  { value: 'GBP_LINKER', label: 'GBP_LINKER · UK Linker' },
  { value: 'EUR_FR_LINKER', label: 'EUR_FR_LINKER · France OATi/OAT€i' },
  { value: 'CAD_RRB', label: 'CAD_RRB · Canada Real Return Bonds' },
];

export const ZCIS_CURVES: { value: string; label: string }[] = [
  { value: 'USD_ZCIS', label: 'USD_ZCIS · USD zero-coupon inflation swap' },
  { value: 'EUR_ZCIS', label: 'EUR_ZCIS · EUR zero-coupon inflation swap' },
  { value: 'GBP_ZCIS', label: 'GBP_ZCIS · GBP zero-coupon inflation swap' },
];

export const RFR_CURVES: { value: string; label: string }[] = [
  { value: 'USD_SOFR_RFR', label: 'USD_SOFR_RFR · USD SOFR overnight' },
  { value: 'EUR_ESTR_RFR', label: 'EUR_ESTR_RFR · EUR ESTR overnight' },
  { value: 'GBP_SONIA_RFR', label: 'GBP_SONIA_RFR · GBP SONIA overnight' },
  { value: 'JPY_TONA_RFR', label: 'JPY_TONA_RFR · JPY TONA overnight' },
];

export const BOND_FUTURES_CURVES: { value: string; label: string }[] = [
  { value: 'UST_FUT', label: 'UST_FUT · US bond futures' },
  { value: 'DE_FUT', label: 'DE_FUT · German bond futures' },
  { value: 'UK_FUT', label: 'UK_FUT · UK bond futures' },
  { value: 'IT_FUT', label: 'IT_FUT · Italian bond futures' },
  { value: 'FR_FUT', label: 'FR_FUT · French bond futures' },
  { value: 'ES_FUT', label: 'ES_FUT · Spanish bond futures' },
  { value: 'CA_FUT', label: 'CA_FUT · Canadian bond futures' },
  { value: 'AU_FUT', label: 'AU_FUT · Australian bond futures' },
  { value: 'JP_FUT', label: 'JP_FUT · Japanese bond futures' },
];

export const POLICY_FUTURES_CURVES: { value: string; label: string }[] = [
  { value: 'SOFR_FUT', label: 'SOFR_FUT · SOFR futures (STIR)' },
  { value: 'EUR_SHORT_RATE_FUT', label: 'EUR_SHORT_RATE_FUT · Euribor / ESTR futures' },
  { value: 'SONIA_FUT', label: 'SONIA_FUT · SONIA futures' },
];

export const ALL_CURVES = [
  ...SOVEREIGN_CURVES,
  ...OIS_CURVES,
  ...INFLATION_LINKER_CURVES,
  ...ZCIS_CURVES,
  ...RFR_CURVES,
  ...BOND_FUTURES_CURVES,
  ...POLICY_FUTURES_CURVES,
];

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
