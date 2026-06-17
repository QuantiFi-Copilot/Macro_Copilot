// ============================================================================
// crossMarketZcisShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_cross_market_inflation_swap_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of breakevenShared.ts).  This module
// knows what a "cross-market ZCIS spread" means — specifically that
// USD_ZCIS / EUR_ZCIS / GBP_ZCIS reference DIFFERENT inflation indices
// (US CPI-U / Eurozone HICP-xT / UK RPI), so the spread captures BOTH
// inflation-expectation differentials AND structural index-family
// differences (NOT a clean expected-inflation divergence).
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic + the index-family caveat resolution live here,
// in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailCrossMarketZcis,
  type CrossMarketZcisDetailParams,
} from '@/services/ratesApi';
import type { CrossMarketInflationSwapSpreadOutput } from '@/types/rates';
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
// ZCIS curve-family metadata.  Cross-market ZCIS spreads are formed from
// two DIFFERENT curve families at the same tenor; this registry lets the
// surfaces render concise pair chips (e.g. "USD-EUR · CPI-U vs HICPxT")
// without re-doing the per-leg metadata lookup the backend already
// surfaces on the wire.  Finance-aware → lives in this per-tool layer.
// ---------------------------------------------------------------------------

export interface ZcisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_ZCIS'). */
  family: string;
  /** Currency / market short code (e.g. 'USD'). */
  marketShort: string;
  /** Short inflation-index label (e.g. 'CPI-U', 'HICPxT', 'RPI'). */
  indexShort: string;
  /** Country flag emoji for the pair-chip footer. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, ZcisFamilyMeta> = {
  USD_ZCIS: {
    family: 'USD_ZCIS',
    marketShort: 'USD',
    indexShort: 'CPI-U',
    flag: '🇺🇸',
  },
  EUR_ZCIS: {
    family: 'EUR_ZCIS',
    marketShort: 'EUR',
    indexShort: 'HICPxT',
    flag: '🇪🇺',
  },
  GBP_ZCIS: {
    family: 'GBP_ZCIS',
    marketShort: 'GBP',
    indexShort: 'RPI',
    flag: '🇬🇧',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family. */
export function zcisFamilyFor(family: string): ZcisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** Ordered list of curve families exposed on the controls strip. */
export const ZCIS_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.marketShort} · ${m.indexShort}`,
  }));

/** Tenor pillars on the ingested ZCIS grid (USD / EUR / GBP share this set). */
export const ZCIS_TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t }));

/** Short pair label (e.g. "USD-EUR") used in identity rows + chips. */
export function pairShortLabel(legA: string, legB: string): string {
  const a = zcisFamilyFor(legA)?.marketShort ?? legA;
  const b = zcisFamilyFor(legB)?.marketShort ?? legB;
  return `${a}-${b}`;
}

/** Pair subtitle (e.g. "USD_ZCIS_CPIU 5Y vs EUR_ZCIS_HICPxT 5Y").
 *  Mockup-faithful — names BOTH legs + their index families. */
export function pairSubtitle(
  legA: string,
  legB: string,
  tenor: string,
): string {
  const a = zcisFamilyFor(legA);
  const b = zcisFamilyFor(legB);
  const aSlug = a ? `${a.family}_${a.indexShort.replace('-', '')}` : legA;
  const bSlug = b ? `${b.family}_${b.indexShort.replace('-', '')}` : legB;
  return `${aSlug} ${tenor} vs ${bSlug} ${tenor}`;
}

/** Compact-view caveat one-liner (e.g. "CPI-U vs HICPxT (not fungible)").
 *  When BOTH legs are known, we render the index-family compare; when
 *  the wire's structured ``index_family_caveat`` is null (the rare
 *  same-family case) we fall back to a neutral disclosure. */
export function shortIndexCaveat(legA: string, legB: string): string {
  const a = zcisFamilyFor(legA);
  const b = zcisFamilyFor(legB);
  if (a && b) {
    return `${a.indexShort} vs ${b.indexShort} (not fungible)`;
  }
  return 'Different inflation measures (not fungible)';
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCrossMarketZcisArgs {
  legACurveFamily: string;
  legBCurveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseCrossMarketZcisResult {
  data: CrossMarketInflationSwapSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useCrossMarketZcisSpread(
  args: UseCrossMarketZcisArgs,
): UseCrossMarketZcisResult {
  const [data, setData] = useState<CrossMarketInflationSwapSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: CrossMarketZcisDetailParams = {
    leg_a_curve_family: args.legACurveFamily,
    leg_b_curve_family: args.legBCurveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (
      !args.legACurveFamily
      || !args.legBCurveFamily
      || args.legACurveFamily === args.legBCurveFamily
      || !args.tenor
    ) {
      setData(null);
      setErrorMessage(
        args.legACurveFamily && args.legACurveFamily === args.legBCurveFamily
          ? 'Cross-market ZCIS requires two distinct curve families.'
          : null,
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailCrossMarketZcis(params)
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
    args.legACurveFamily,
    args.legBCurveFamily,
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

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (BPS)     (signed bps, neutral, primary emphasis;
 *                         subtext shows the same value in percent)
 *    2. 1D CHANGE        (signed bps, toneForChange; subtext in percent)
 *    3. Z-SCORE (252D)   (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: CrossMarketInflationSwapSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyPct =
    cm.change_1d_bps != null ? cm.change_1d_bps / 100 : null;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      subtext: `(${signedFixed(cm.spread_pct, 2)}%)`,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
      subtext:
        dailyPct != null ? `(${signedFixed(dailyPct, 2)}%)` : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_252d, 2),
      tone: toneForZScore(cm.z_score_252d),
      caption: regimeForZScore(cm.z_score_252d),
    },
  ];
}

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  strip + per-leg level decomposition + range / percentile / window). */
export function extendedKPIs(
  data: CrossMarketInflationSwapSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      subtext: `(${signedFixed(cm.spread_pct, 2)}%)`,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.change_1w_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1w_bps),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.change_1m_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1m_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_252d, 2),
      tone: toneForZScore(cm.z_score_252d),
      caption: regimeForZScore(cm.z_score_252d),
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
      label: 'OBS',
      value: cm.observation_count != null ? `${cm.observation_count}` : '—',
      tone: 'neutral',
    },
    {
      label: 'LEG A',
      value: signedFixed(cm.leg_a_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'LEG B',
      value: signedFixed(cm.leg_b_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for cross-market ZCIS spreads (bps).  Across the playbook
// universe ZCIS spreads sit roughly [-600, +600] bps with stress spikes.
// Anything well outside is almost certainly a generic-ticker roll artifact
// on one leg.  Mirrors the realYield / breakeven sanity bounds.
const ZCIS_SPREAD_SANITY_MIN_BPS = -800;
const ZCIS_SPREAD_SANITY_MAX_BPS = 800;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < ZCIS_SPREAD_SANITY_MIN_BPS
        || r.value > ZCIS_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: CrossMarketInflationSwapSpreadOutput,
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
  data: CrossMarketInflationSwapSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_252d == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_252d);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.z_score_252d,
    bucket,
    cm.leg_a_curve_family,
    cm.leg_b_curve_family,
  );

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score_252d != null
        ? {
            value: cm.z_score_252d,
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
  legA: string,
  legB: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const pair = pairShortLabel(legA, legB);
  const indexNote = shortIndexCaveat(legA, legB);

  if (regime === 'Extreme') {
    return (
      `${pair} ZCIS spread is ${regime.toLowerCase()} ${direction} than its `
      + `trailing-year mean.  Current observation sits in the `
      + `${bucket.toLowerCase()}-end of the 252d range.  Remember the legs `
      + `reference different inflation measures (${indexNote}), so this captures `
      + `BOTH inflation-expectation differentials AND structural index-family `
      + `differences — not a clean expected-inflation divergence.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `${pair} ZCIS spread is elevated vs its trailing-year history.  `
      + `${indexNote} → spread mixes inflation-compensation regimes.`
    );
  }
  return (
    `${pair} ZCIS spread is within its trailing-year norm; no extreme `
    + `stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: CrossMarketInflationSwapSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    {
      label: 'Construction',
      value: `spread = ${cm.leg_a_curve_family} ${cm.tenor} − ${cm.leg_b_curve_family} ${cm.tenor} (×100 for bps)`,
    },
    {
      label: 'Sign convention',
      value: 'leg_a − leg_b (left minus right).  Predictable from input ordering.',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (ZCIS mid quoted rate, both legs)`,
    },
    {
      label: 'Alignment',
      value: 'Strict pandas inner-join on trade_date after independent per-leg compute — no synthetic spread points.',
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
      label: 'Index families',
      value:
        `${cm.leg_a_inflation_index_family} (leg A) vs ${cm.leg_b_inflation_index_family} (leg B)`,
    },
    {
      label: 'Index lags',
      value: `${cm.leg_a_index_lag} (leg A) / ${cm.leg_b_index_lag} (leg B)`,
    },
    {
      label: 'Interpolation',
      value:
        `${cm.leg_a_interpolation} (leg A) / ${cm.leg_b_interpolation} (leg B)`,
    },
    {
      label: 'Underlying indices',
      value: `${cm.leg_a_underlying_index ?? '—'} (leg A) / ${cm.leg_b_underlying_index ?? '—'} (leg B)`,
    },
    {
      label: 'Index-family caveat',
      value:
        cm.index_family_caveat
          ?? 'Both legs reference the same inflation index family — direct spread is interpretable.',
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
    { label: 'Eurostat HICP' },
    { label: 'UK ONS RPI' },
    { label: 'Bloomberg ZCIS' },
  ];
}
