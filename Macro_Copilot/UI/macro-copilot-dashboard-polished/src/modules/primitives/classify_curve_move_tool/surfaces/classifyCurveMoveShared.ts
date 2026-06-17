// ============================================================================
// classifyCurveMoveShared.ts — per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for ``classify_curve_move_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the breakeven pilot's
// breakevenShared.ts).  The tool deterministically classifies a
// two-point curve move over a discrete lookback into one of six
// canonical regime labels; the wire is a PURE SNAPSHOT
// (``RegimeOutput.current_metrics`` only — no time series).
//
// Both Build views fetch the SAME typed-detail endpoint
// ``GET /api/v1/rates/detail/regime`` (rendering_density.md §1.1).
//
// Methodology honesty (P5): the wire carries NO methodology prose —
// the card below assembles its rows from wire fields (prior_date /
// lookback_period / spread_label) plus the classification thresholds,
// which are YAML-LOCKED on the backend
// (curve_move_classifier/config.yaml: parallel_threshold_bps = 1.0,
// move_threshold_bps = 0.5) and cited here as config.yaml-locked
// values, never invented copy.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailRegime,
  type RegimeDetailParams,
} from '@/services/ratesApi';
import type { RegimeOutput } from '@/types/rates';
import type {
  KPIDescriptor,
  MethodologyRow,
  ValueTone,
} from '@/components/shared/build';
import { signedFixed } from '@/components/shared/build';
import type { MetricItem } from '@/components/build/primitive/PrimitiveMetrics';

// ---------------------------------------------------------------------------
// Param conventions + defaults
// ---------------------------------------------------------------------------

export const CLASSIFY_DEFAULTS = {
  curve_family: 'UST',
  front_tenor: '2Y',
  back_tenor: '10Y',
  lookback_period: '1d',
} as const;

export const CURVE_FAMILY_OPTIONS = [
  { value: 'UST', label: 'UST (US)' },
  { value: 'DE_BUND', label: 'Bund (DE)' },
  { value: 'UK_GILT', label: 'Gilt (UK)' },
  { value: 'JGB', label: 'JGB (JP)' },
  { value: 'FR_OAT', label: 'OAT (FR)' },
  { value: 'IT_BTP', label: 'BTP (IT)' },
  { value: 'USD_SOFR_OIS', label: 'SOFR OIS (US)' },
  { value: 'EUR_ESTR_OIS', label: 'ESTR OIS (EU)' },
];

export const TENOR_OPTIONS = ['2Y', '3Y', '5Y', '7Y', '10Y', '30Y'].map(
  (t) => ({ value: t, label: t }),
);

export const LOOKBACK_PERIOD_OPTIONS = [
  { value: '1d', label: '1 day' },
  { value: '5d', label: '1 week (5d)' },
  { value: '22d', label: '1 month (22d)' },
  { value: '63d', label: '1 quarter (63d)' },
];

// ---------------------------------------------------------------------------
// Data hook — single source for BOTH views (rendering_density.md §1.1)
// ---------------------------------------------------------------------------

export interface UseRegimeDataArgs {
  curveFamily: string;
  frontTenor: string;
  backTenor: string;
  lookbackPeriod: string;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseRegimeDataResult {
  data: RegimeOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useRegimeData(args: UseRegimeDataArgs): UseRegimeDataResult {
  const [data, setData] = useState<RegimeOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!args.curveFamily || !args.frontTenor || !args.backTenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    const params: RegimeDetailParams = {
      curve_family: args.curveFamily,
      front_tenor: args.frontTenor,
      back_tenor: args.backTenor,
      lookback_period: args.lookbackPeriod || undefined,
      field_name: args.fieldName || undefined,
      as_of_date: args.asOfDate || undefined,
    };
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailRegime(params)
      .then((out) => {
        if (cancelled) return;
        setData(out);
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
    args.curveFamily,
    args.frontTenor,
    args.backTenor,
    args.lookbackPeriod,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Regime tone — the desk read of the six canonical labels
// ---------------------------------------------------------------------------

/** Bull moves (yields falling) read mint, bear moves coral, the
 *  direction-neutral labels (PARALLEL_SHIFT, TWIST) neutral.  Display
 *  vocabulary only — the tag itself comes from the backend verbatim. */
export function toneForRegimeTag(tag: string | null | undefined): ValueTone {
  if (!tag) return 'neutral';
  if (tag.startsWith('BULL_')) return 'positive';
  if (tag.startsWith('BEAR_')) return 'negative';
  return 'neutral';
}

/** MetricItem tone domain for the extended hero. */
export function metricToneForRegimeTag(
  tag: string | null | undefined,
): MetricItem['tone'] {
  const t = toneForRegimeTag(tag);
  return t === 'positive' ? 'positive' : t === 'negative' ? 'negative' : 'neutral';
}

/** Human form of the wire tag: BULL_STEEPENER → "Bull Steepener". */
export function prettyRegimeTag(tag: string | null | undefined): string {
  if (!tag) return '—';
  return tag
    .toLowerCase()
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

// ---------------------------------------------------------------------------
// Driver — DISPLAY-ONLY derivation (FP9)
// ---------------------------------------------------------------------------

/** Which leg dominated the move — a DISPLAY-ONLY comparison of the
 *  backend's own per-leg changes (|front_change_bps| vs
 *  |back_change_bps|); no client-side analytics beyond the compare
 *  (FP9).  The wire carries no driver field. */
export function deriveDriverLabel(
  frontChangeBps: number | null | undefined,
  backChangeBps: number | null | undefined,
): string {
  if (frontChangeBps == null || backChangeBps == null) return '—';
  const f = Math.abs(frontChangeBps);
  const b = Math.abs(backChangeBps);
  if (f === 0 && b === 0) return 'BALANCED';
  if (f > b * 1.5) return 'FRONT-LED';
  if (b > f * 1.5) return 'BACK-LED';
  return 'BALANCED';
}

// ---------------------------------------------------------------------------
// KPI builders
// ---------------------------------------------------------------------------

/** Extended hero — the regime read leads (emphasis), then the spread
 *  move and the derived driver. */
export function heroMetrics(data: RegimeOutput): MetricItem[] {
  const cm = data.current_metrics;
  return [
    {
      label: 'REGIME',
      value: prettyRegimeTag(cm.regime_tag),
      tone: metricToneForRegimeTag(cm.regime_tag),
      emphasis: true,
      subtext: cm.regime_description,
    },
    {
      label: `SPREAD Δ (${cm.lookback_period.toUpperCase()})`,
      value: signedFixed(cm.spread_change_bps, 1),
      unit: 'bp',
      tone:
        cm.spread_change_bps == null || cm.spread_change_bps === 0
          ? 'neutral'
          : cm.spread_change_bps > 0
            ? 'positive'
            : 'negative',
      subtext:
        cm.spread_change_bps == null
          ? undefined
          : cm.spread_change_bps > 0
            ? 'Steeper'
            : cm.spread_change_bps < 0
              ? 'Flatter'
              : 'Unchanged',
    },
    {
      label: 'DRIVER (DISPLAY-ONLY)',
      value: deriveDriverLabel(cm.front_change_bps, cm.back_change_bps),
    },
    {
      label: 'SPREAD NOW',
      value: signedFixed(cm.spread_current_bps, 1),
      unit: 'bp',
      subtext: `was ${signedFixed(cm.spread_prior_bps, 1)} bp (${cm.prior_date})`,
    },
  ];
}

/** The four-quadrant per-leg context strip (extended view). */
export function quadrantMetrics(data: RegimeOutput): MetricItem[] {
  const cm = data.current_metrics;
  return [
    {
      label: `${cm.front_tenor} LEVEL`,
      value: cm.front_level_current != null ? cm.front_level_current.toFixed(3) : '—',
      subtext: `was ${cm.front_level_prior != null ? cm.front_level_prior.toFixed(3) : '—'}`,
    },
    {
      label: `${cm.front_tenor} Δ`,
      value: signedFixed(cm.front_change_bps, 1),
      unit: 'bp',
      tone:
        cm.front_change_bps == null || cm.front_change_bps === 0
          ? 'neutral'
          : cm.front_change_bps > 0
            ? 'negative'
            : 'positive',
    },
    {
      label: `${cm.back_tenor} LEVEL`,
      value: cm.back_level_current != null ? cm.back_level_current.toFixed(3) : '—',
      subtext: `was ${cm.back_level_prior != null ? cm.back_level_prior.toFixed(3) : '—'}`,
    },
    {
      label: `${cm.back_tenor} Δ`,
      value: signedFixed(cm.back_change_bps, 1),
      unit: 'bp',
      tone:
        cm.back_change_bps == null || cm.back_change_bps === 0
          ? 'neutral'
          : cm.back_change_bps > 0
            ? 'negative'
            : 'positive',
    },
  ];
}

/** The compact view's THREE canonical headline KPIs
 *  (rendering_density.md §2.2):
 *    1. REGIME    — the classification (the desk read)
 *    2. SPREAD Δ  — the move that produced it, in bps
 *    3. DRIVER    — which leg owned it (display-only derivation)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(data: RegimeOutput): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'REGIME',
      value: prettyRegimeTag(cm.regime_tag),
      tone: toneForRegimeTag(cm.regime_tag),
      emphasis: 'primary',
    },
    {
      label: `SPREAD Δ (${cm.lookback_period.toUpperCase()})`,
      value: signedFixed(cm.spread_change_bps, 1),
      unit: 'bp',
      tone:
        cm.spread_change_bps == null || cm.spread_change_bps === 0
          ? 'neutral'
          : cm.spread_change_bps > 0
            ? 'positive'
            : 'negative',
    },
    {
      label: 'DRIVER',
      value: deriveDriverLabel(cm.front_change_bps, cm.back_change_bps),
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Methodology + caveat copy
// ---------------------------------------------------------------------------

export const CLASSIFY_COMPACT_CAVEAT =
  'Single-observation classification over the chosen lookback; thresholds config.yaml-locked.';

/** Methodology rows — wire fields first (P5), then the YAML-locked
 *  classification thresholds cited as config.yaml-locked (the wire
 *  carries no methodology prose; these values are the backend's own
 *  curve_move_classifier/config.yaml constants, not invented copy). */
export function buildMethodologyRows(
  data: RegimeOutput,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    { label: 'Spread', value: cm.spread_label },
    {
      label: 'Window',
      value: `${cm.lookback_period} (${cm.prior_date} → ${cm.as_of_date})`,
    },
    {
      label: 'Labels',
      value:
        'Six canonical regimes: bull/bear steepener, bull/bear flattener, parallel shift, twist — assigned deterministically from the two legs’ signed changes.',
    },
    {
      label: 'Thresholds',
      value:
        'parallel_threshold_bps = 1.0, move_threshold_bps = 0.5 (config.yaml-locked on the backend; not exposed as inputs).',
    },
    {
      label: 'Driver label',
      value:
        'FRONT-LED / BACK-LED / BALANCED is a display-only comparison of the backend’s own per-leg changes (1.5× dominance rule); it is not a wire field.',
    },
  ];
}

export { signedFixed };
