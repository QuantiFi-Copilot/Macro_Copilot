// ============================================================================
// financingRateShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``compute_financing_rate_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (per docs_revamped/02_components/frontend_module/README.md FM4
// + the rendering-density standard's standalone-module contract).  This
// module knows what "OIS-implied financing rate" means; the shared shells
// do not.
//
// ARCHITECTURAL DEVIATION — ROUTE-SIDE SYNTHESIS.  The backend
// ``FinancingRateOutput`` is Panel-shaped (no ``current_metrics`` /
// ``time_series``).  Per the 2026-06-08 human resolution (Option (a))
// the route at ``/api/v1/rates/detail/financing-rate`` SYNTHESIZES the
// snapshot shape from ``result.panel.payload``; this file consumes the
// synthesized response, structurally identical to other snapshot tools.
// See ../THESIS.md "Backend shape note" subsection.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailFinancingRate,
  type FinancingRateDetailParams,
} from '@/services/ratesApi';
import type { FinancingRateDetailResponse } from '@/types/rates';
import {
  bpsAsPercentSubtext,
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
// Proxy-curve → bond-family display registry (FRONT-END ONLY).  Drives
// the identity row label ("UST · Financing" / "Bund · Financing" / etc.)
// and the country flag.  The backend takes ONLY proxy_curve; this is the
// human-facing framing of WHAT the OIS curve proxies financing for.
// ---------------------------------------------------------------------------

interface ProxyCurveDescriptor {
  /** Backend proxy_curve enum value. */
  value: string;
  /** Human-facing label in the controls strip ("USD SOFR · UST proxy"). */
  label: string;
  /** Underlying-bond family identifier surfaced in the identity row. */
  bondFamily: string;
  /** Sovereign-flag emoji for the identity row. */
  flag: string;
  /** Curve-family kicker shown alongside the identity primary. */
  shortLabel: string;
}

export const PROXY_CURVE_REGISTRY: ReadonlyArray<ProxyCurveDescriptor> = [
  {
    value: 'USD_SOFR_OIS',
    label: 'USD SOFR · UST proxy',
    bondFamily: 'UST',
    flag: '🇺🇸',
    shortLabel: 'SOFR',
  },
  {
    value: 'EUR_ESTR_OIS',
    label: 'EUR €STR · Bund proxy',
    bondFamily: 'Bund',
    flag: '🇩🇪',
    shortLabel: '€STR',
  },
  {
    value: 'GBP_SONIA_OIS',
    label: 'GBP SONIA · Gilt proxy',
    bondFamily: 'Gilt',
    flag: '🇬🇧',
    shortLabel: 'SONIA',
  },
  {
    value: 'JPY_TONA_OIS',
    label: 'JPY TONA · JGB proxy',
    bondFamily: 'JGB',
    flag: '🇯🇵',
    shortLabel: 'TONA',
  },
  {
    value: 'AUD_AONIA_OIS',
    label: 'AUD AONIA · ACGB proxy',
    bondFamily: 'ACGB',
    flag: '🇦🇺',
    shortLabel: 'AONIA',
  },
  {
    value: 'CAD_CORRA_OIS',
    label: 'CAD CORRA · CGB proxy',
    bondFamily: 'CGB',
    flag: '🇨🇦',
    shortLabel: 'CORRA',
  },
];

export function proxyCurveDescriptorFor(
  proxyCurve: string,
): ProxyCurveDescriptor | undefined {
  return PROXY_CURVE_REGISTRY.find((p) => p.value === proxyCurve);
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseFinancingRateArgs {
  proxyCurve: string;
  method?: string;
  lookbackDays?: number;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseFinancingRateResult {
  data: FinancingRateDetailResponse | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views + the Monitor widget.
 *  Fetches the synthesized typed-detail response and stringifies any
 *  error verbatim. */
export function useFinancingRate(
  args: UseFinancingRateArgs,
): UseFinancingRateResult {
  const [data, setData] = useState<FinancingRateDetailResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchParams: FinancingRateDetailParams = {
    proxy_curve: args.proxyCurve,
    method: args.method,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.proxyCurve) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailFinancingRate(fetchParams)
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
  }, [args.proxyCurve, args.method, args.lookbackDays, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Compact view's THREE canonical headline KPIs (per Compact.png mockup +
 *  rendering_density.md §2.2):
 *
 *    1. FINANCING RATE      (signed %, primary emphasis)
 *    2. 1D CHANGE           (signed bps + % subtext, toneForChange)
 *    3. Z-SCORE (252D)      (signed value + regime caption, toneForZScore)
 *
 *  THESIS Mockup conformance: the Compact.png mockup additionally shows
 *  a small auxiliary strip (5D / 1M / 252D PCTL / 252D HIGH / 252D LOW /
 *  OBSERVATIONS).  Per Option (c) from the Batch 1 fdac7d2 precedent the
 *  BuildCompactShell enforces 3 headline KPIs; the extras live in the
 *  extended view's 9-cell strip.  THESIS §"Mockup conformance" notes
 *  this collapse. */
export function compactKPIs(
  data: FinancingRateDetailResponse,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.z_score);
  return [
    {
      label: 'FINANCING RATE',
      value: signedFixed(cm.financing_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: bpsAsPercentSubtext(cm.daily_change_bps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score, 2),
      tone: toneForZScore(cm.z_score),
      caption: zRegime,
    },
  ];
}

/** Extended view's FULL KPI strip — 9 cells per Extended.png mockup. */
export function extendedKPIs(
  data: FinancingRateDetailResponse,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'FINANCING RATE',
      value: signedFixed(cm.financing_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: bpsAsPercentSubtext(cm.daily_change_bps, 3),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.weekly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.weekly_change_bps),
      subtext: bpsAsPercentSubtext(cm.weekly_change_bps, 3),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.monthly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.monthly_change_bps),
      subtext: bpsAsPercentSubtext(cm.monthly_change_bps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score, 2),
      tone: toneForZScore(cm.z_score),
      caption: regimeForZScore(cm.z_score),
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
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope
// ---------------------------------------------------------------------------

export function buildReferenceBands(
  data: FinancingRateDetailResponse,
): ReadonlyArray<ReferenceBand> {
  const rows = data.time_series?.rows ?? [];
  const values = rows
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
  data: FinancingRateDetailResponse,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.z_score, bucket);

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score != null
        ? {
            value: cm.z_score,
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
      ? 'consistent with tighter policy / repo conditions'
      : 'consistent with easier policy / repo conditions';

  if (regime === 'Extreme') {
    return (
      `Financing rate is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d ` +
      `range and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Financing rate is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `Financing rate is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references — sourced from the joined
// methodology_disclosure ROUTE-SYNTHESIZED field, NOT hardcoded prose.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: FinancingRateDetailResponse,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  return [
    {
      label: 'Method',
      value: cm.method,
    },
    {
      label: 'Proxy curve',
      value: cm.proxy_curve,
    },
    {
      label: 'Disclosure',
      // SOURCE OF TRUTH: backend-joined methodology_disclosure (PR10 / P5
      // — YAML edits flow to runtime; NEVER hardcoded as a TS literal).
      value: data.methodology_disclosure || '—',
    },
    {
      label: 'Z-score model',
      value: '252d rolling window, sample std (ddof=1)',
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile)',
    },
    {
      label: 'Caveat',
      value:
        'OIS proxy approximates true overnight repo; does NOT reflect '
        + 'CUSIP-level specials, GC scarcity, or term-repo basis.',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.13 (Repo)' },
    { label: 'ARRC SOFR docs' },
    { label: 'ECB €STR docs' },
    { label: 'BoE SONIA docs' },
  ];
}

// ---------------------------------------------------------------------------
// Compact view caveat — short line; falls back to a generic disclosure
// when the backend-joined disclosure is empty.
// ---------------------------------------------------------------------------

export function compactCaveatText(
  data: FinancingRateDetailResponse | null,
): string {
  // Prefer the backend-joined disclosure (source of truth).  Truncate to
  // a single line for the compact footer; the extended view shows the
  // full text in the methodology card.
  const fromBackend = data?.methodology_disclosure?.trim();
  if (fromBackend) {
    // Take everything up to the first full stop for compact-line fit.
    const firstSentence = fromBackend.split(/\.\s/)[0];
    return firstSentence.endsWith('.') ? firstSentence : `${firstSentence}.`;
  }
  return "OIS proxy; doesn't reflect specials/scarcity.";
}
