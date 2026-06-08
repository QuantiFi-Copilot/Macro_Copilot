// ============================================================================
// swapBreakevenBasisShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``calculate_swap_breakeven_basis_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of breakevenShared.ts /
// crossMarketZcisShared.ts).  This module knows what a "swap-breakeven
// basis" means — specifically that it is the SAME-CURRENCY differential
// between a ZCIS rate and the corresponding bond-implied breakeven at the
// same pillar.  POSITIVE = ZCIS rich vs bond breakeven (catalog sign
// convention ``zcis_minus_breakeven``).  NOT a clean liquidity-premium
// read — also reflects index-lag differences between the ZCIS conventions
// and the linker's realised CPI accrual, linker on-the-run / liquidity
// premium effects in the nominal-vs-real decomposition, and the structural
// ZCIS vs linker-breakeven basis present even in benign markets.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic + the load-bearing index-family caveat resolution
// live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailSwapBreakevenBasis,
  type SwapBreakevenBasisDetailParams,
} from '@/services/ratesApi';
import type { SwapBreakevenBasisSimpleOutput } from '@/types/rates';
import {
  bucketForPercentile,
  countryCaveatFor,
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
// Country-pair metadata.  A swap-breakeven basis is a same-CURRENCY
// triplet (ZCIS leg + nominal sovereign + matching linker) at a shared
// tenor.  Keyed by a synthetic ``pairKey`` (the currency / market code)
// because the country uniquely determines the canonical triplet under
// the V1 same-currency invariant.  Finance-aware → lives in this per-tool
// layer, NOT the shared registry.
// ---------------------------------------------------------------------------

export interface SwapBreakevenBasisPairMeta {
  /** Short pair key used on the wire (e.g. 'USD', 'EUR', 'GBP'). */
  key: string;
  /** ZCIS curve_family for the swap leg. */
  zcisFamily: string;
  /** Nominal sovereign curve_family feeding the breakeven leg. */
  nominalFamily: string;
  /** Linker curve_family feeding the breakeven leg. */
  linkerFamily: string;
  /** Country / market label (e.g. 'US', 'EUR/FR', 'UK'). */
  country: string;
  /** Country flag emoji. */
  flag: string;
  /** Short ZCIS-leg index label (e.g. 'CPI-U', 'HICPxT', 'RPI'). */
  zcisIndexShort: string;
  /** Short nominal-leg label (e.g. 'UST', 'OAT', 'Gilt'). */
  nominalShort: string;
  /** Short linker-leg label (e.g. 'TIPS', 'OATei', 'Linker'). */
  linkerShort: string;
}

/** Same-currency triplets exposed in V1.  The catalog's
 *  ``swap_breakeven_basis_simple/config.yaml`` ingested universe is
 *  USD_ZCIS+UST+USD_TIPS, EUR_ZCIS+FR_OAT+EUR_FR_LINKER, GBP_ZCIS+
 *  UK_GILT+GBP_LINKER (per rates_agent/playbooks/inflation_swaps.yml). */
const PAIR_REGISTRY: Record<string, SwapBreakevenBasisPairMeta> = {
  USD: {
    key: 'USD',
    zcisFamily: 'USD_ZCIS',
    nominalFamily: 'UST',
    linkerFamily: 'USD_TIPS',
    country: 'US',
    flag: '🇺🇸',
    zcisIndexShort: 'CPI-U',
    nominalShort: 'UST',
    linkerShort: 'TIPS',
  },
  EUR: {
    key: 'EUR',
    zcisFamily: 'EUR_ZCIS',
    nominalFamily: 'FR_OAT',
    linkerFamily: 'EUR_FR_LINKER',
    country: 'EUR/FR',
    flag: '🇪🇺',
    zcisIndexShort: 'HICPxT',
    nominalShort: 'OAT',
    linkerShort: 'OATei',
  },
  GBP: {
    key: 'GBP',
    zcisFamily: 'GBP_ZCIS',
    nominalFamily: 'UK_GILT',
    linkerFamily: 'GBP_LINKER',
    country: 'UK',
    flag: '🇬🇧',
    zcisIndexShort: 'RPI',
    nominalShort: 'Gilt',
    linkerShort: 'Linker',
  },
};

/** Resolve the pair meta from a pair key (currency code). */
export function pairForKey(key: string): SwapBreakevenBasisPairMeta | null {
  return PAIR_REGISTRY[key] ?? null;
}

/** Inverse lookup: resolve the pair meta from a ZCIS curve_family — used
 *  when only the wire's resolved curve_family is available (e.g. monitor
 *  widget reading from cm.zcis_curve_family). */
export function pairForZcisFamily(
  zcisFamily: string,
): SwapBreakevenBasisPairMeta | null {
  const hit = Object.values(PAIR_REGISTRY).find(
    (p) => p.zcisFamily === zcisFamily,
  );
  return hit ?? null;
}

/** Single "Country Pair" dropdown options (mockup ExtendedBasis: one
 *  dropdown, not three) — keyed by currency because the V1 same-currency
 *  invariant uniquely determines the triplet. */
export const SWAP_BREAKEVEN_BASIS_PAIR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(PAIR_REGISTRY).map((p) => ({
  value: p.key,
  label: `${p.country} · ${p.zcisIndexShort} (ZCIS vs ${p.nominalShort}/${p.linkerShort} BE)`,
}));

/** Tenor sets per pair — intersection of the ZCIS grid (1Y / 2Y / 3Y /
 *  5Y / 10Y / 20Y / 30Y on each ZCIS curve) with the linker / nominal
 *  pillars.  Conservative: tenors where ALL THREE legs realistically
 *  have data (e.g. UK 20Y absent because 20Y TIPS-style benchmarks are
 *  the US-specific complication, not UK; EUR 30Y trimmed because the
 *  French linker grid thins out beyond 15Y). */
export const SWAP_BREAKEVEN_BASIS_TENOR_OPTIONS_BY_PAIR: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD: ['5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  EUR: ['2Y', '5Y', '10Y', '15Y'].map((t) => ({ value: t, label: t })),
  GBP: ['5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
};

/** Compact-view caveat one-liner — surfaces the LOAD-BEARING basis
 *  caveat (NOT a clean liquidity-premium read) in a single line, with the
 *  resolved index families when the wire surfaces them. */
export const SWAP_BREAKEVEN_BASIS_COMPACT_CAVEAT =
  'Liquidity-premium proxy; ZCIS vs bond BE.';

/** Build the per-pair caveat shown when the wire has not yet resolved
 *  ``index_family_caveat`` (rare — typically only during the initial
 *  render before the fetch lands).  Falls back to the static caveat. */
export function fallbackBasisCaveat(
  pair: SwapBreakevenBasisPairMeta | null,
): string {
  if (!pair) return SWAP_BREAKEVEN_BASIS_COMPACT_CAVEAT;
  return `Liquidity-premium proxy; ${pair.zcisIndexShort} ZCIS vs bond BE.`;
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseSwapBreakevenBasisArgs {
  zcisCurveFamily: string;
  nominalCurveFamily: string;
  linkerCurveFamily: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseSwapBreakevenBasisResult {
  data: SwapBreakevenBasisSimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes. */
export function useSwapBreakevenBasis(
  args: UseSwapBreakevenBasisArgs,
): UseSwapBreakevenBasisResult {
  const [data, setData] = useState<SwapBreakevenBasisSimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: SwapBreakevenBasisDetailParams = {
    zcis_curve_family: args.zcisCurveFamily,
    nominal_curve_family: args.nominalCurveFamily,
    linker_curve_family: args.linkerCurveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.zcisCurveFamily
      || !args.nominalCurveFamily
      || !args.linkerCurveFamily
      || args.nominalCurveFamily === args.linkerCurveFamily
      || !args.tenor
    ) {
      setData(null);
      setErrorMessage(
        args.nominalCurveFamily
          && args.nominalCurveFamily === args.linkerCurveFamily
          ? 'Swap-breakeven basis requires distinct nominal + linker legs.'
          : null,
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailSwapBreakevenBasis(params)
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
    args.zcisCurveFamily,
    args.nominalCurveFamily,
    args.linkerCurveFamily,
    args.tenor,
    args.lookbackDays,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. BASIS (bps)      (signed bps, neutral, primary emphasis;
 *                         subtext shows the same value in percent)
 *    2. 1D CHANGE (bps)  (signed bps, toneForChange)
 *    3. Z-SCORE (252D)   (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: SwapBreakevenBasisSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'BASIS',
      value: signedFixed(cm.basis_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      subtext: `(${signedFixed(cm.basis_pct, 3)}%)`,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
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
  data: SwapBreakevenBasisSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'BASIS',
      value: signedFixed(cm.basis_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      subtext: `(${signedFixed(cm.basis_pct, 3)}%)`,
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
      label: 'ZCIS LEG',
      value: signedFixed(cm.zcis_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'BOND BE',
      value: signedFixed(cm.breakeven_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the basis (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for swap-breakeven basis (bps).  Empirically the basis
// sits in roughly [-150, +150] bps with stress spikes; anything well
// outside is almost certainly a generic-ticker roll artifact on one leg.
// Mirrors the realYield / breakeven / crossMarketZcis sanity bounds.
const BASIS_SANITY_MIN_BPS = -400;
const BASIS_SANITY_MAX_BPS = 400;

export function sanitiseBasisSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < BASIS_SANITY_MIN_BPS
        || r.value > BASIS_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: SwapBreakevenBasisSimpleOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_basis?.rows ?? [];
  const sanitised = sanitiseBasisSeries(rawRows);
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
  data: SwapBreakevenBasisSimpleOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_252d == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_252d);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.z_score_252d, bucket);

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
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const sideNote =
    z > 0
      ? 'ZCIS richening vs the bond-implied breakeven'
      : 'ZCIS cheapening vs the bond-implied breakeven';

  if (regime === 'Extreme') {
    return (
      `Swap-breakeven basis is ${regime.toLowerCase()} ${direction} than its `
      + `trailing-year mean.  Current observation sits in the `
      + `${bucket.toLowerCase()}-end of the 252d range — ${sideNote}.  `
      + `Remember the basis is NOT a clean liquidity-premium read; it also `
      + `reflects index-lag differences, linker on-the-run effects, and `
      + `structural ZCIS basis.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Swap-breakeven basis is elevated vs its trailing-year history — `
      + `${sideNote}.  Liquidity-premium proxy, not a clean read.`
    );
  }
  return (
    'Swap-breakeven basis is within its trailing-year norm; no extreme '
    + 'stretch in either direction.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: SwapBreakevenBasisSimpleOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const pair = pairForZcisFamily(cm.zcis_curve_family);
  const linkerCaveat = countryCaveatFor(cm.linker_curve_family);
  return [
    {
      label: 'Construction',
      value:
        `basis = ${cm.zcis_curve_family} ${cm.tenor} ZCIS − `
        + `(${cm.nominal_curve_family} − ${cm.linker_curve_family}) `
        + `${cm.tenor} breakeven (×100 for bps)`,
    },
    {
      label: 'Sign convention',
      value:
        'zcis_minus_breakeven (POSITIVE = ZCIS rich vs bond BE).  '
        + 'Wire-locked; compute() refuses any other value.',
    },
    {
      label: 'Field',
      value:
        `${effectiveFieldName || 'YAML default'} (threaded into both inner `
        + 'calls — ZCIS rate level + bond-implied breakeven)',
    },
    {
      label: 'Alignment',
      value:
        'Strict inner-join on trade_date across the ZCIS leg + breakeven '
        + 'leg per-day outputs — no synthetic basis points.',
    },
    {
      label: 'Z-score model',
      value: '252d rolling window of the basis (bps), YAML-locked conventions',
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
      label: 'Same-country pair',
      value: pair
        ? `${pair.country} (ZCIS ${pair.zcisIndexShort} · ${pair.nominalShort} / ${pair.linkerShort})`
        : '—',
    },
    {
      label: 'ZCIS index family',
      value:
        `${cm.zcis_inflation_index_family} (lag ${cm.zcis_index_lag}, `
        + `${cm.zcis_interpolation} interpolation)`
        + (cm.zcis_underlying_index ? ` · ${cm.zcis_underlying_index}` : ''),
    },
    {
      label: 'Linker index family',
      value:
        cm.linker_inflation_index_family
          ? `${cm.linker_inflation_index_family}`
            + (cm.linker_index_lag ? ` (lag ${cm.linker_index_lag})` : '')
          : 'Not surfaced on wire (treated as caveat below)',
    },
    {
      label: 'Index-family caveat',
      value:
        cm.index_family_caveat
          ?? 'Both legs reference the same inflation index family — basis '
            + 'is interpretable as a swap-vs-bond pricing differential.',
    },
    {
      label: 'Linker caveat',
      value: linkerCaveat ? linkerCaveat.caveatLong : '—',
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
