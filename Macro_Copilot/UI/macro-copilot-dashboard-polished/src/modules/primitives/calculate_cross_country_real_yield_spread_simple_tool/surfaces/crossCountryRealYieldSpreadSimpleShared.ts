// ============================================================================
// crossCountryRealYieldSpreadSimpleShared.ts — Per-tool helpers shared
// between BuildExtended.tsx and BuildCompact.tsx for
// ``calculate_cross_country_real_yield_spread_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of crossCountryBreakevenSpreadSimple
// Shared.ts).  This module knows what a "cross-country linker real-yield
// spread" means — specifically that each leg is a sovereign linker REAL
// yield at the same tenor, that each linker references a DIFFERENT
// inflation index (USD TIPS → US CPI-U NSA, UK GILT linkers → RPI,
// FR/DE/IT linkers → Eurozone HICPxT, Canadian RRBs → Canada CPI), AND
// that cross-country linker markets carry materially different liquidity /
// issuance / on-the-run structure even at the same tenor pillar.  The
// spread therefore captures BOTH real-rate divergence AND structural
// index-family + market-structure differences.
//
// KEY SHAPE DELTA FROM THE BREAKEVEN SIBLING: real yields are in PERCENT
// (not BPS), so the spread + the canonical TimeSeries + the chart axis +
// the headline KPI units are all PERCENT.  Daily / weekly / monthly
// changes are in BPS per desk convention (change of a percent-units
// spread reported in bps).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic + the per-curve index-family caveat resolution
// live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailCrossCountryRealYieldSpread,
  type CrossCountryRealYieldSpreadDetailParams,
} from '@/services/ratesApi';
import type { CrossCountryRealYieldSpreadSimpleOutput } from '@/types/rates';
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
// Linker-curve registry.  Cross-country real-yield spreads are formed from
// two DIFFERENT linker curve families at the same tenor; this registry
// lets the surfaces render concise chips (e.g. "UK-US 10Y RY · RPI vs
// CPI-U") without re-doing the per-curve metadata lookup.  Finance-aware →
// lives in this per-tool layer.
//
// Each entry is a LINKER curve family directly (one identifier per curve);
// there is no nominal/linker pairing here because real-yield levels read
// off the linker curve alone — distinct from the breakeven sibling's
// (nominal, linker) packed-pair convention.
// ---------------------------------------------------------------------------

export interface LinkerCurveMeta {
  /** Linker curve family identifier (e.g. 'USD_TIPS', 'GBP_LINKER'). */
  curveFamily: string;
  /** Country short code (e.g. 'US', 'UK', 'FR'). */
  countryShort: string;
  /** Short inflation-index label (e.g. 'CPI-U', 'RPI', 'HICPxT', 'CA CPI'). */
  indexShort: string;
  /** Long inflation-index label (used in methodology card). */
  indexLong: string;
  /** Country flag emoji for the pair-chip footer. */
  flag: string;
}

const CURVE_REGISTRY: Record<string, LinkerCurveMeta> = {
  USD_TIPS: {
    curveFamily: 'USD_TIPS',
    countryShort: 'US',
    indexShort: 'CPI-U',
    indexLong: 'US CPI-U NSA',
    flag: '🇺🇸',
  },
  GBP_LINKER: {
    curveFamily: 'GBP_LINKER',
    countryShort: 'UK',
    indexShort: 'RPI',
    indexLong: 'UK RPI',
    flag: '🇬🇧',
  },
  EUR_FR_LINKER: {
    curveFamily: 'EUR_FR_LINKER',
    countryShort: 'FR',
    indexShort: 'HICPxT',
    indexLong: 'Eurozone HICP ex-Tobacco',
    flag: '🇫🇷',
  },
  EUR_DE_LINKER: {
    curveFamily: 'EUR_DE_LINKER',
    countryShort: 'DE',
    indexShort: 'HICPxT',
    indexLong: 'Eurozone HICP ex-Tobacco',
    flag: '🇩🇪',
  },
  EUR_IT_LINKER: {
    curveFamily: 'EUR_IT_LINKER',
    countryShort: 'IT',
    indexShort: 'HICPxT',
    indexLong: 'Eurozone HICP ex-Tobacco',
    flag: '🇮🇹',
  },
  CAD_RRB: {
    curveFamily: 'CAD_RRB',
    countryShort: 'CA',
    indexShort: 'CA CPI',
    indexLong: 'Canada CPI',
    flag: '🇨🇦',
  },
};

/** Resolve the per-curve meta.  Returns null for an unknown curve. */
export function linkerCurveFor(curveFamily: string): LinkerCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** Ordered list of linker curve families exposed on the controls strip. */
export const CURVE_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(CURVE_REGISTRY).map((m) => ({
    value: m.curveFamily,
    label: `${m.countryShort} · ${m.indexShort}`,
  }));

/** Tenor pillars on the ingested generic-bond grid. */
export const TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['2Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t }));

/** Short pair label (e.g. "UK-US") used in identity rows + chips. */
export function pairShortLabel(
  firstCurve: string,
  secondCurve: string,
): string {
  const a = linkerCurveFor(firstCurve)?.countryShort ?? firstCurve;
  const b = linkerCurveFor(secondCurve)?.countryShort ?? secondCurve;
  return `${a}-${b}`;
}

/** Identity subtitle (e.g. "UK 10Y Real Yield (RPI) minus US 10Y Real
 *  Yield (CPI-U)"). */
export function identitySubtitle(
  firstCurve: string,
  secondCurve: string,
  tenor: string,
): string {
  const a = linkerCurveFor(firstCurve);
  const b = linkerCurveFor(secondCurve);
  const aLabel = a
    ? `${a.countryShort} ${tenor} Real Yield (${a.indexShort})`
    : `${firstCurve} ${tenor}`;
  const bLabel = b
    ? `${b.countryShort} ${tenor} Real Yield (${b.indexShort})`
    : `${secondCurve} ${tenor}`;
  return `${aLabel} minus ${bLabel}`;
}

/** Compact-view one-liner caveat (e.g. "RPI vs CPI-U · not fungible
 *  inflation measures").  When BOTH curves are known we render the
 *  index-family compare; otherwise we fall back to a neutral disclosure. */
export function shortIndexCaveat(
  firstCurve: string,
  secondCurve: string,
): string {
  const a = linkerCurveFor(firstCurve);
  const b = linkerCurveFor(secondCurve);
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

export interface UseCrossCountryRealYieldSpreadArgs {
  firstCurveFamily: string;
  secondCurveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseCrossCountryRealYieldSpreadResult {
  data: CrossCountryRealYieldSpreadSimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useCrossCountryRealYieldSpread(
  args: UseCrossCountryRealYieldSpreadArgs,
): UseCrossCountryRealYieldSpreadResult {
  const [data, setData] =
    useState<CrossCountryRealYieldSpreadSimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: CrossCountryRealYieldSpreadDetailParams = {
    first_curve_family: args.firstCurveFamily,
    second_curve_family: args.secondCurveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    const incomplete =
      !args.firstCurveFamily || !args.secondCurveFamily || !args.tenor;
    const sameCurve =
      args.firstCurveFamily
      && args.firstCurveFamily === args.secondCurveFamily;
    if (incomplete || sameCurve) {
      setData(null);
      setErrorMessage(
        sameCurve
          ? 'Cross-country real-yield spread requires two different linker curves.'
          : null,
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailCrossCountryRealYieldSpread(params)
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
    args.firstCurveFamily,
    args.secondCurveFamily,
    args.tenor,
    args.lookbackDays,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Real yields are quoted in PERCENT.  Daily/weekly/monthly *changes* of
 *  a percent-units spread are reported in BPS per desk convention.  This
 *  helper renders a percent-units value (typically the current spread)
 *  with the "%" unit token; the BPS-units change KPIs use the bp unit. */

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (PCT/PERCENT)  (signed percent, neutral, primary emphasis)
 *    2. 1D CHANGE (BPS)       (signed bps, toneForChange)
 *    3. Z-SCORE (252D)        (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: CrossCountryRealYieldSpreadSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_pct, 2),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
  ];
}

/** The extended view's FULL KPI strip (mockups/Extended.png — pct + bps
 *  changes + range / percentile / observation count + per-curve REAL-
 *  YIELD endpoint decomposition). */
export function extendedKPIs(
  data: CrossCountryRealYieldSpreadSimpleOutput,
  observationCount: number,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_pct, 2),
      unit: '%',
      tone: 'neutral',
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
      value: signedFixed(cm.high_252d_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${observationCount}`,
      tone: 'neutral',
    },
    {
      label: 'RY A',
      value: signedFixed(cm.first_curve_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'RY B',
      value: signedFixed(cm.second_curve_real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the percent-units
// spread.
// ---------------------------------------------------------------------------

// Sanity bounds for cross-country real-yield spreads (PERCENT).  Across
// the playbook universe these sit roughly [-6%, +6%] with stress spikes;
// anything well outside is almost certainly a generic-ticker roll
// artifact on one leg.  Mirrors the cross-country breakeven sanity-bound
// pattern but on percent units (NOT bps).
const XC_REAL_YIELD_SPREAD_SANITY_MIN_PCT = -10;
const XC_REAL_YIELD_SPREAD_SANITY_MAX_PCT = 10;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < XC_REAL_YIELD_SPREAD_SANITY_MIN_PCT
        || r.value > XC_REAL_YIELD_SPREAD_SANITY_MAX_PCT
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: CrossCountryRealYieldSpreadSimpleOutput,
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
  data: CrossCountryRealYieldSpreadSimpleOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.first_curve_family,
    cm.second_curve_family,
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
  firstCurve: string,
  secondCurve: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const pair = pairShortLabel(firstCurve, secondCurve);
  const indexNote = shortIndexCaveat(firstCurve, secondCurve);

  if (regime === 'Extreme') {
    return (
      `${pair} cross-country real-yield spread is extreme ${direction} than its `
      + `trailing-year mean.  Current observation sits in the `
      + `${bucket.toLowerCase()}-end of the 252d range.  The two legs reference `
      + `different inflation measures (${indexNote}) AND carry country-specific `
      + `linker liquidity / market-structure differentials — interpret as a `
      + `mix of real-rate divergence and structural differences, not a clean `
      + `real-rate read.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `${pair} cross-country real-yield spread is elevated vs its trailing-year `
      + `history.  ${indexNote} → spread mixes real-rate divergence with linker-`
      + `market-structure differences.`
    );
  }
  return (
    `${pair} cross-country real-yield spread is within its trailing-year norm; `
    + `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: CrossCountryRealYieldSpreadSimpleOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const a = linkerCurveFor(cm.first_curve_family);
  const b = linkerCurveFor(cm.second_curve_family);
  return [
    {
      label: 'Construction',
      value:
        `spread_pct = first_curve_real_yield_pct − second_curve_real_yield_pct `
        + `(${cm.first_curve_family} ${cm.tenor} `
        + `minus ${cm.second_curve_family} ${cm.tenor})`,
    },
    {
      label: 'Sign convention',
      value: 'first_curve_family − second_curve_family (left minus right).  Wire-locked.',
    },
    {
      label: 'Units',
      value:
        'Spread in PERCENT (real yields are quoted in PERCENT — NOT BPS).  '
        + 'Daily / weekly / monthly *changes* are reported in BPS per desk '
        + 'convention (change of a percent-units spread reported in bps).',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (real-yield-to-maturity, threaded into both endpoint real-yield series)`,
    },
    {
      label: 'Composition',
      value:
        'Two independent get_real_yield_level calls (one per linker curve) → '
        + 'strict pandas inner-join on trade_date → percent-units subtraction.  '
        + 'No synthetic spread points.',
    },
    {
      label: 'Z-score model',
      value: '252d rolling window of the percent-units spread, YAML-locked conventions',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, PERCENT)',
    },
    {
      label: 'First curve',
      value: a
        ? `${a.countryShort} (${cm.first_curve_country}/${cm.first_curve_currency}) · ${cm.first_curve_family} · ${a.indexLong}`
        : `${cm.first_curve_family} (${cm.first_curve_country}/${cm.first_curve_currency})`,
    },
    {
      label: 'Second curve',
      value: b
        ? `${b.countryShort} (${cm.second_curve_country}/${cm.second_curve_currency}) · ${cm.second_curve_family} · ${b.indexLong}`
        : `${cm.second_curve_family} (${cm.second_curve_country}/${cm.second_curve_currency})`,
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
      label: 'Market-structure caveat',
      value:
        'Cross-country linker markets differ materially in benchmark '
        + 'availability at the same tenor pillar, issuance size, liquidity '
        + 'premium, and deflation-floor treatment.  The displayed real-yield '
        + 'differential therefore reflects BOTH real-rate divergence AND '
        + 'relative linker-market liquidity / structure differences.',
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
