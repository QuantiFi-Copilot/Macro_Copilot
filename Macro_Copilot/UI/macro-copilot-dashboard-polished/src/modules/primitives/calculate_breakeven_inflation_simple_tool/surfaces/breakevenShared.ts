// ============================================================================
// breakevenShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``calculate_breakeven_inflation_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of get_real_yield_level_tool's
// realYieldShared.ts).  This module knows what "breakeven inflation"
// means — specifically that it is inflation COMPENSATION, not a clean
// expected-inflation read; the shared shells do not.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailBreakeven,
  type BreakevenDetailParams,
} from '@/services/ratesApi';
import type { BreakevenInflationSimpleOutput } from '@/types/rates';
import {
  bucketForPercentile,
  countryCaveatFor,
  regimeForZScore,
  signedFixed,
  signedFixedWithUnit,
  toneForChange,
  toneForZScore,
  unsignedFixed,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Country-pair metadata.  A bond-implied breakeven is a same-country pair
// (nominal sovereign + matching linker).  Keyed by the LINKER curve_family
// because the linker uniquely determines the valid nominal counterparty.
// Finance-aware → lives in this per-tool layer, NOT the shared registry.
// ---------------------------------------------------------------------------

export interface BreakevenPairMeta {
  /** Linker curve_family (e.g. 'USD_TIPS'). */
  linkerFamily: string;
  /** Nominal sovereign curve_family (e.g. 'UST'). */
  nominalFamily: string;
  /** Country label (e.g. 'US'). */
  country: string;
  /** Short nominal label for the pair chip (e.g. 'UST'). */
  nominalShort: string;
  /** Short linker label for the pair chip (e.g. 'TIPS'). */
  linkerShort: string;
}

const PAIR_BY_LINKER: Record<string, BreakevenPairMeta> = {
  USD_TIPS: {
    linkerFamily: 'USD_TIPS',
    nominalFamily: 'UST',
    country: 'US',
    nominalShort: 'UST',
    linkerShort: 'TIPS',
  },
  GBP_LINKER: {
    linkerFamily: 'GBP_LINKER',
    nominalFamily: 'UK_GILT',
    country: 'UK',
    nominalShort: 'Gilt',
    linkerShort: 'Linker',
  },
  EUR_FR_LINKER: {
    linkerFamily: 'EUR_FR_LINKER',
    nominalFamily: 'FR_OAT',
    country: 'France',
    nominalShort: 'OAT',
    linkerShort: 'OATei',
  },
  CAD_RRB: {
    linkerFamily: 'CAD_RRB',
    nominalFamily: 'CANADA_GOVT',
    country: 'Canada',
    nominalShort: 'Govt',
    linkerShort: 'RRB',
  },
};

/** Resolve the pair metadata from a linker curve_family.  Returns null
 *  for an unknown family (caller renders a neutral fallback). */
export function pairForLinker(linkerFamily: string): BreakevenPairMeta | null {
  return PAIR_BY_LINKER[linkerFamily] ?? null;
}

/** The single "COUNTRY PAIR" dropdown options (mockup ExtendedBreakeven:
 *  one dropdown, not two) — keyed by linker family because the linker
 *  determines the valid nominal counterparty. */
export const BREAKEVEN_PAIR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(PAIR_BY_LINKER).map((p) => ({
    value: p.linkerFamily,
    label: `${p.country} · ${p.nominalShort} / ${p.linkerShort}`,
  }));

/** Tenor sets per pair (intersection of the nominal + linker grids).
 *  Conservative: the tenors where BOTH legs realistically have data. */
export const BREAKEVEN_TENOR_OPTIONS_BY_PAIR: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_TIPS: ['5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  GBP_LINKER: ['2Y', '5Y', '10Y', '15Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  EUR_FR_LINKER: ['2Y', '5Y', '10Y', '15Y'].map((t) => ({ value: t, label: t })),
  CAD_RRB: ['5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
};

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology.  Same for every country
 *  (it's a property of the bond-implied breakeven, not the issuer). */
export const BREAKEVEN_COMPACT_CAVEAT =
  'Inflation compensation, not expected inflation.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseBreakevenArgs {
  nominalCurveFamily: string;
  linkerCurveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  zScoreWindowDays?: number;
  zScoreMinPeriods?: number;
  zScoreDdof?: number;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseBreakevenResult {
  data: BreakevenInflationSimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useBreakevenInflation(args: UseBreakevenArgs): UseBreakevenResult {
  const [data, setData] = useState<BreakevenInflationSimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: BreakevenDetailParams = {
    nominal_curve_family: args.nominalCurveFamily,
    linker_curve_family: args.linkerCurveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    z_score_window_days: args.zScoreWindowDays,
    z_score_min_periods: args.zScoreMinPeriods,
    z_score_ddof: args.zScoreDdof,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.nominalCurveFamily || !args.linkerCurveFamily || !args.tenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailBreakeven(params)
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
    args.nominalCurveFamily,
    args.linkerCurveFamily,
    args.tenor,
    args.lookbackDays,
    args.fieldName,
    args.zScoreWindowDays,
    args.zScoreMinPeriods,
    args.zScoreDdof,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. BREAKEVEN     (signed bps, neutral, primary emphasis)
 *    2. 1D CHANGE     (signed bps, toneForChange)
 *    3. Z-SCORE       (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives (e.g. breakeven %,
 *  weekly change, percentile). */
export function compactKPIs(
  data: BreakevenInflationSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'BREAKEVEN',
      value: signedFixed(cm.breakeven_bps, 1),
      unit: 'bp',
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

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  9-cell strip + the two underlying yields for the decomposition). */
export function extendedKPIs(
  data: BreakevenInflationSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'BREAKEVEN',
      value: signedFixed(cm.breakeven_bps, 1),
      unit: 'bp',
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
      label: 'NOMINAL YIELD',
      value: signedFixed(cm.nominal_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'REAL YIELD',
      value: signedFixed(cm.real_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the breakeven (bps).
// ---------------------------------------------------------------------------

// Sanity bound for bond-implied breakevens (bps).  Breakeven = nominal −
// real; across the playbook universe breakevens sit roughly [-100, +600]
// bps with stress spikes.  Anything well outside is almost certainly a
// generic-ticker roll artifact on one leg.  Defensive frontend safety
// net (mirror of realYieldShared's REAL_YIELD_SANITY bounds); the real
// fix lives in the data pipeline (docs/technical_debt.md TD #26 / #31).
const BREAKEVEN_SANITY_MIN_BPS = -300;
const BREAKEVEN_SANITY_MAX_BPS = 900;

export function sanitiseBreakevenSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < BREAKEVEN_SANITY_MIN_BPS
        || r.value > BREAKEVEN_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: BreakevenInflationSimpleOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_breakeven?.rows ?? [];
  const sanitised = sanitiseBreakevenSeries(rawRows);
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
  data: BreakevenInflationSimpleOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.current_z_score, bucket);

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
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'above' : 'below';
  const directionPhrase =
    z > 0
      ? 'consistent with a higher inflation-compensation pricing'
      : 'consistent with a lower inflation-compensation pricing';

  if (regime === 'Extreme') {
    return (
      `Breakeven inflation is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.  Remember this is inflation compensation (carries ` +
      `an inflation risk premium + liquidity premium), not a clean expected-inflation read.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Breakeven inflation is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.  Inflation compensation, not expected inflation.`
    );
  }
  return (
    `Breakeven inflation is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: BreakevenInflationSimpleOutput,
  effectiveFieldName: string,
  effectiveZWindow: number,
  effectiveZMinPeriods: number,
  effectiveZDdof: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const linkerCaveat = countryCaveatFor(cm.linker_curve_family);
  const pair = pairForLinker(cm.linker_curve_family);
  return [
    {
      label: 'Construction',
      value: `breakeven = nominal yield (${cm.nominal_curve_family}) − real yield (${cm.linker_curve_family}), ×100 for bps`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid yield-to-maturity, both legs)`,
    },
    {
      label: 'Z-score model',
      value: `${effectiveZWindow}d rolling window, min periods ${effectiveZMinPeriods}, ddof ${effectiveZDdof}`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, bps)',
    },
    {
      label: 'Disclosure',
      value: 'Inflation compensation, NOT expected inflation — carries inflation risk premium + relative liquidity premium.',
    },
    {
      label: 'Same-country pair',
      value: pair ? `${pair.country} (${pair.nominalShort} / ${pair.linkerShort})` : '—',
    },
    {
      label: 'Linker caveat',
      value: linkerCaveat ? linkerCaveat.caveat : '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'US TreasuryDirect' },
    { label: 'UK DMO' },
    { label: 'Agence France Trésor' },
    { label: 'Bank of Canada' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
