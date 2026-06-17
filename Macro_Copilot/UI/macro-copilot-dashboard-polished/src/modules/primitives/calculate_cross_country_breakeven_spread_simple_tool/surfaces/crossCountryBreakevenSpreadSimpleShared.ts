// ============================================================================
// crossCountryBreakevenSpreadSimpleShared.ts — Per-tool helpers shared
// between BuildExtended.tsx and BuildCompact.tsx for
// ``calculate_cross_country_breakeven_spread_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of crossMarketZcisShared.ts).  This
// module knows what a "cross-country bond-implied breakeven spread" means
// — specifically that each sovereign linker references a DIFFERENT
// inflation index (USD TIPS → US CPI-U NSA, UK GILT linkers → RPI,
// FR OAT linkers → Eurozone HICPxT, Canadian RRBs → Canada CPI), so the
// spread captures BOTH inflation-expectation differentials AND structural
// index-family differences (NOT a clean expected-inflation differential).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic + the per-country index-family caveat
// resolution live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailCrossCountryBreakevenSpread,
  type CrossCountryBreakevenSpreadDetailParams,
} from '@/services/ratesApi';
import type { CrossCountryBreakevenSpreadSimpleOutput } from '@/types/rates';
import {
  bucketForPercentile,
  regimeForZScore,
  signedFixed,
  toneForChange,
  toneForZScore,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Country-pair registry.  Cross-country breakeven spreads are formed from
// two DIFFERENT sovereign (nominal, linker) pairs at the same tenor; this
// registry lets the surfaces render concise pair chips (e.g.
// "UK-US 10Y BE · RPI vs CPI-U") without re-doing the per-country
// metadata lookup.  Finance-aware → lives in this per-tool layer.
// ---------------------------------------------------------------------------

export interface CountryPairMeta {
  /** Curve-family pair identifier (e.g. 'UST/USD_TIPS'). */
  pairKey: string;
  /** Nominal sovereign curve family (e.g. 'UST', 'UK_GILT'). */
  nominalPair: string;
  /** Sovereign linker curve family (e.g. 'USD_TIPS', 'GBP_LINKER'). */
  linkerPair: string;
  /** Country short code (e.g. 'US', 'UK'). */
  countryShort: string;
  /** Short inflation-index label (e.g. 'CPI-U', 'RPI', 'HICPxT', 'CA CPI'). */
  indexShort: string;
  /** Long inflation-index label (used in methodology card). */
  indexLong: string;
  /** Country flag emoji for the pair-chip footer. */
  flag: string;
}

const PAIR_REGISTRY: Record<string, CountryPairMeta> = {
  'UST/USD_TIPS': {
    pairKey: 'UST/USD_TIPS',
    nominalPair: 'UST',
    linkerPair: 'USD_TIPS',
    countryShort: 'US',
    indexShort: 'CPI-U',
    indexLong: 'US CPI-U NSA',
    flag: '🇺🇸',
  },
  'UK_GILT/GBP_LINKER': {
    pairKey: 'UK_GILT/GBP_LINKER',
    nominalPair: 'UK_GILT',
    linkerPair: 'GBP_LINKER',
    countryShort: 'UK',
    indexShort: 'RPI',
    indexLong: 'UK RPI',
    flag: '🇬🇧',
  },
  'FR_OAT/EUR_FR_LINKER': {
    pairKey: 'FR_OAT/EUR_FR_LINKER',
    nominalPair: 'FR_OAT',
    linkerPair: 'EUR_FR_LINKER',
    countryShort: 'FR',
    indexShort: 'HICPxT',
    indexLong: 'Eurozone HICP ex-Tobacco',
    flag: '🇫🇷',
  },
  'DE_BUND/EUR_DE_LINKER': {
    pairKey: 'DE_BUND/EUR_DE_LINKER',
    nominalPair: 'DE_BUND',
    linkerPair: 'EUR_DE_LINKER',
    countryShort: 'DE',
    indexShort: 'HICPxT',
    indexLong: 'Eurozone HICP ex-Tobacco',
    flag: '🇩🇪',
  },
  'IT_BTP/EUR_IT_LINKER': {
    pairKey: 'IT_BTP/EUR_IT_LINKER',
    nominalPair: 'IT_BTP',
    linkerPair: 'EUR_IT_LINKER',
    countryShort: 'IT',
    indexShort: 'HICPxT',
    indexLong: 'Eurozone HICP ex-Tobacco',
    flag: '🇮🇹',
  },
  'CANADA_GOVT/CAD_RRB': {
    pairKey: 'CANADA_GOVT/CAD_RRB',
    nominalPair: 'CANADA_GOVT',
    linkerPair: 'CAD_RRB',
    countryShort: 'CA',
    indexShort: 'CA CPI',
    indexLong: 'Canada CPI',
    flag: '🇨🇦',
  },
};

/** Resolve the per-pair meta.  Returns null for an unknown pair. */
export function countryPairFor(
  nominalPair: string,
  linkerPair: string,
): CountryPairMeta | null {
  return PAIR_REGISTRY[`${nominalPair}/${linkerPair}`] ?? null;
}

/** Ordered list of country pairs exposed on the controls strip. */
export const COUNTRY_PAIR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(PAIR_REGISTRY).map((m) => ({
    value: m.pairKey,
    label: `${m.countryShort} · ${m.indexShort}`,
  }));

/** Tenor pillars on the ingested generic-bond grid. */
export const TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['2Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t }));

/** Short pair label (e.g. "UK-US") used in identity rows + chips. */
export function pairShortLabel(
  aNominal: string,
  aLinker: string,
  bNominal: string,
  bLinker: string,
): string {
  const a = countryPairFor(aNominal, aLinker)?.countryShort ?? aNominal;
  const b = countryPairFor(bNominal, bLinker)?.countryShort ?? bNominal;
  return `${a}-${b}`;
}

/** Identity subtitle (e.g. "UK 10Y BE (RPI) minus US 10Y BE (CPI-U)"). */
export function identitySubtitle(
  aNominal: string,
  aLinker: string,
  bNominal: string,
  bLinker: string,
  tenor: string,
): string {
  const a = countryPairFor(aNominal, aLinker);
  const b = countryPairFor(bNominal, bLinker);
  const aLabel = a
    ? `${a.countryShort} ${tenor} BE (${a.indexShort})`
    : `${aNominal} ${tenor}`;
  const bLabel = b
    ? `${b.countryShort} ${tenor} BE (${b.indexShort})`
    : `${bNominal} ${tenor}`;
  return `${aLabel} minus ${bLabel}`;
}

/** Compact-view one-liner caveat (e.g. "RPI vs CPI-U · not fungible inflation
 *  measures").  When BOTH pairs are known we render the index-family
 *  compare; otherwise we fall back to a neutral disclosure. */
export function shortIndexCaveat(
  aNominal: string,
  aLinker: string,
  bNominal: string,
  bLinker: string,
): string {
  const a = countryPairFor(aNominal, aLinker);
  const b = countryPairFor(bNominal, bLinker);
  if (a && b) {
    if (a.indexShort === b.indexShort) {
      return `Both legs reference ${a.indexShort} — index families match.`;
    }
    return `${a.indexShort} vs ${b.indexShort} · not fungible inflation measures`;
  }
  return 'Different inflation measures · not fungible';
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCrossCountryBreakevenSpreadArgs {
  countryANominalPair: string;
  countryALinkerPair: string;
  countryBNominalPair: string;
  countryBLinkerPair: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseCrossCountryBreakevenSpreadResult {
  data: CrossCountryBreakevenSpreadSimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useCrossCountryBreakevenSpread(
  args: UseCrossCountryBreakevenSpreadArgs,
): UseCrossCountryBreakevenSpreadResult {
  const [data, setData] =
    useState<CrossCountryBreakevenSpreadSimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: CrossCountryBreakevenSpreadDetailParams = {
    country_a_nominal_pair: args.countryANominalPair,
    country_a_linker_pair: args.countryALinkerPair,
    country_b_nominal_pair: args.countryBNominalPair,
    country_b_linker_pair: args.countryBLinkerPair,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    const incomplete =
      !args.countryANominalPair
      || !args.countryALinkerPair
      || !args.countryBNominalPair
      || !args.countryBLinkerPair
      || !args.tenor;
    const sameNominal =
      args.countryANominalPair
      && args.countryANominalPair === args.countryBNominalPair;
    const sameLinker =
      args.countryALinkerPair
      && args.countryALinkerPair === args.countryBLinkerPair;
    if (incomplete || sameNominal || sameLinker) {
      setData(null);
      setErrorMessage(
        sameNominal || sameLinker
          ? 'Cross-country breakeven spread requires two different sovereign issuers.'
          : null,
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailCrossCountryBreakevenSpread(params)
      .then((p) => {
        if (cancelled) return;
        setData(p);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErrorMessage(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    args.countryANominalPair,
    args.countryALinkerPair,
    args.countryBNominalPair,
    args.countryBLinkerPair,
    args.tenor,
    args.lookbackDays,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

function bpsToPercentSubtext(bps: number | null | undefined): string | undefined {
  if (bps == null || Number.isNaN(bps)) return undefined;
  const pct = bps / 100;
  return `(${signedFixed(pct, 2)}%)`;
}

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (BPS)     (signed bps, neutral, primary emphasis;
 *                         subtext shows the same value as percent)
 *    2. 1D CHANGE        (signed bps, toneForChange; subtext in percent)
 *    3. Z-SCORE (252D)   (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: CrossCountryBreakevenSpreadSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      subtext: bpsToPercentSubtext(cm.current_spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: bpsToPercentSubtext(cm.daily_change_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
  ];
}

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  strip + per-country breakeven decomposition + range / percentile /
 *  observation count). */
export function extendedKPIs(
  data: CrossCountryBreakevenSpreadSimpleOutput,
  observationCount: number,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      subtext: bpsToPercentSubtext(cm.current_spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.weekly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.weekly_change_bps),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.monthly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.monthly_change_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
    {
      label: 'PERCENTILE (252D)',
      value:
        cm.percentile_252d != null
          ? `${Math.round(cm.percentile_252d)}`
          : '—',
      unit: 'th',
      caption: bucketForPercentile(cm.percentile_252d),
    },
    {
      label: '252D HIGH',
      value: signedFixed(cm.high_252d_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${observationCount}`,
      tone: 'neutral',
    },
    {
      label: 'BE A',
      value: signedFixed(cm.breakeven_a_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'BE B',
      value: signedFixed(cm.breakeven_b_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for cross-country breakeven spreads (bps).  Across the
// playbook universe these sit roughly [-400, +400] bps with stress spikes;
// anything well outside is almost certainly a generic-ticker roll artifact
// on one country leg.  Mirrors the cross-market ZCIS sanity bounds.
const XC_BREAKEVEN_SPREAD_SANITY_MIN_BPS = -600;
const XC_BREAKEVEN_SPREAD_SANITY_MAX_BPS = 600;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < XC_BREAKEVEN_SPREAD_SANITY_MIN_BPS
        || r.value > XC_BREAKEVEN_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: CrossCountryBreakevenSpreadSimpleOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_spread?.rows ?? [];
  const sanitised = sanitiseSpreadSeries(rawRows);
  const values = sanitised
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
  if (values.length < 10) return [];

  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance =
    values.reduce((acc, v) => acc + (v - mean) ** 2, 0) / (values.length - 1);
  const std = Math.sqrt(variance);
  if (!Number.isFinite(std) || std === 0) return [];

  return [
    { value: mean + 2 * std, label: '+2σ', tone: 'extreme', style: 'dashed' },
    { value: mean + 1.5 * std, label: '+1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 1.5 * std, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 2 * std, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: CrossCountryBreakevenSpreadSimpleOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.country_a_nominal_pair,
    cm.country_a_linker_pair,
    cm.country_b_nominal_pair,
    cm.country_b_linker_pair,
  );

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.current_z_score != null
        ? {
            value: cm.current_z_score,
            regime,
            bands: { amber: 1.5, coral: 2.0 },
          }
        : undefined,
    interpretation: interp,
  };
}

function interpretationFor(
  regime: 'Normal' | 'Elevated' | 'Extreme',
  z: number | null | undefined,
  bucket: 'Low' | 'Normal' | 'High',
  aNominal: string,
  aLinker: string,
  bNominal: string,
  bLinker: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const pair = pairShortLabel(aNominal, aLinker, bNominal, bLinker);
  const indexNote = shortIndexCaveat(aNominal, aLinker, bNominal, bLinker);

  if (regime === 'Extreme') {
    return (
      `${pair} cross-country breakeven spread is extreme ${direction} than its `
      + `trailing-year mean.  Current observation sits in the `
      + `${bucket.toLowerCase()}-end of the 252d range.  Remember the legs `
      + `reference different inflation measures (${indexNote}), so this captures `
      + `BOTH inflation-expectation differentials AND structural index-family `
      + `differences — not a clean expected-inflation divergence.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `${pair} cross-country breakeven spread is elevated vs its trailing-year `
      + `history.  ${indexNote} → spread mixes inflation-compensation regimes.`
    );
  }
  return (
    `${pair} cross-country breakeven spread is within its trailing-year norm; `
    + `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: CrossCountryBreakevenSpreadSimpleOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const a = countryPairFor(cm.country_a_nominal_pair, cm.country_a_linker_pair);
  const b = countryPairFor(cm.country_b_nominal_pair, cm.country_b_linker_pair);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = breakeven_a_bps − breakeven_b_bps `
        + `(${cm.country_a_nominal_pair}/${cm.country_a_linker_pair} ${cm.tenor} `
        + `minus ${cm.country_b_nominal_pair}/${cm.country_b_linker_pair} ${cm.tenor})`,
    },
    {
      label: 'Sign convention',
      value: 'country_a − country_b (left minus right).  Wire-locked.',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (yield-to-maturity, threaded into all four underlying yield series)`,
    },
    {
      label: 'Composition',
      value:
        'Two independent calculate_breakeven_inflation_simple calls (one per '
        + 'country) → strict pandas inner-join on trade_date → bps subtraction.  '
        + 'No synthetic spread points.',
    },
    {
      label: 'Z-score model',
      value: '252d rolling window of the spread (bps), YAML-locked conventions',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, bps)',
    },
    {
      label: 'Country A',
      value: a
        ? `${a.countryShort} · ${cm.country_a_nominal_pair}/${cm.country_a_linker_pair} · ${a.indexLong}`
        : `${cm.country_a_nominal_pair}/${cm.country_a_linker_pair}`,
    },
    {
      label: 'Country B',
      value: b
        ? `${b.countryShort} · ${cm.country_b_nominal_pair}/${cm.country_b_linker_pair} · ${b.indexLong}`
        : `${cm.country_b_nominal_pair}/${cm.country_b_linker_pair}`,
    },
    {
      label: 'Index families',
      value:
        a && b
          ? a.indexShort === b.indexShort
            ? `${a.indexShort} (both legs)`
            : `${a.indexShort} vs ${b.indexShort} — not fungible inflation measures`
          : 'Cross-country (different sovereign issuers)',
    },
    {
      label: 'Inflation-compensation caveat',
      value:
        'Each leg is a generic linker-implied breakeven — carries an inflation '
        + 'risk premium AND a relative liquidity premium between its nominal and '
        + 'linker.  The cross-country differential inherits both at each leg.',
    },
    {
      label: 'Disclosure',
      value: cm.methodology_label,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'BLS CPI-U' },
    { label: 'UK ONS RPI' },
    { label: 'Eurostat HICP' },
    { label: 'StatCan CPI' },
    { label: 'Bloomberg YLD_YTM_MID' },
  ];
}
