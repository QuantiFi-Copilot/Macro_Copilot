// ============================================================================
// inflationSwapForwardShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``calculate_inflation_swap_forward_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "ZCIS forward
// rate" is — an IMPLIED forward zero-coupon inflation swap rate spanning a
// (start, end) window on ONE ZCIS curve family (USD_ZCIS / EUR_ZCIS /
// GBP_ZCIS).  Forward ZCIS rates are bootstrapped from the par-rate grid
// via the dual-compounding geometric formula:
//
//     f = ((1 + r_long)^T_long / (1 + r_short)^T_short)
//             ^ (1 / (T_long - T_short)) - 1
//
// Output is FORWARD INFLATION COMPENSATION — not a clean forward expected-
// inflation read.  ZCIS still carries an inflation risk premium and a
// (smaller, but non-zero) liquidity premium, and the geometric forward
// inherits both.  The honesty disclosure lives in
// ``current_metrics.methodology_label`` and is sourced from the YAML at
// runtime, NOT a hardcoded TS literal.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch
// the SAME typed-detail endpoint
// ``/api/v1/rates/detail/inflation-swap-forward`` (per
// rendering_density.md §1.1 + §10 — both views consume the same bridge;
// the compact view just renders less of it).  KPI builders + formatting +
// tone logic + the per-curve inflation-index caveat live here in ONE place
// to prevent drift across surfaces.
//
// Rolling-z-score conventions are YAML-locked on this primitive (mirrors
// the OIS forward_rate / ZCIS rate_level / curve_spread / butterfly
// siblings).  Only ``lookback_days`` + ``field_name`` are exposed at the
// controls layer — no "Advanced" panel.  The forward window itself is
// configured via the curve_family + (start_tenor, end_tenor) tenor-pair
// mode (the canonical desk read; USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS
// 2Y3Y, etc).  V1 supports tenor-pair mode only.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailInflationSwapForward,
  type InflationSwapForwardDetailParams,
} from '@/services/ratesApi';
import type { InflationSwapForwardOutput } from '@/types/rates';
import {
  bpsAsPercentSubtext,
  bucketForPercentile,
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
// ZCIS curve-family metadata.  The registry keys off curve_family because
// that uniquely determines the inflation index identity (CPI-U NSA / HICPxT
// / RPI) the surfaces render.  Curve families enumerated mirror the
// backend's ingested ZCIS universe per
// ``rates_agent/playbooks/inflation_swaps.yml``.
//
// NB: the shared ``countryCaveatFor`` registry covers the linker domain
// (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) and the OIS domain has
// its own per-tool registry.  ZCIS curve families are a disjoint universe
// (distinct inflation-index references with distinct lag + interpolation
// conventions), so we maintain a per-tool registry here (mirrors the
// sibling inflation_swap_rate_level / inflation_swap_curve_spread tools).
// ---------------------------------------------------------------------------

export interface ZcisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_ZCIS'). */
  family: string;
  /** Market short code used in headers (e.g. 'USD'). */
  marketShort: string;
  /** Inflation index short label (e.g. 'CPI-U', 'HICPxT', 'RPI'). */
  indexShort: string;
  /** Central bank that anchors the underlying inflation regime. */
  centralBank: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
  /** Full subtitle for the extended identity row. */
  subtitle: string;
  /** Default indexation lag for this curve family ('3M' / '2M'). */
  defaultIndexLag: string;
  /** Default index-fixing interpolation convention ('Daily' / 'Monthly'). */
  defaultInterpolation: string;
}

const FAMILY_REGISTRY: Record<string, ZcisFamilyMeta> = {
  USD_ZCIS: {
    family: 'USD_ZCIS',
    marketShort: 'USD',
    indexShort: 'CPI-U',
    centralBank: 'Federal Reserve',
    flag: '🇺🇸',
    subtitle: 'Forward zero-coupon inflation swap · US CPI-U reference',
    defaultIndexLag: '3M',
    defaultInterpolation: 'Daily',
  },
  EUR_ZCIS: {
    family: 'EUR_ZCIS',
    marketShort: 'EUR',
    indexShort: 'HICPxT',
    centralBank: 'European Central Bank',
    flag: '🇪🇺',
    subtitle: 'Forward zero-coupon inflation swap · Eurozone HICPxT reference',
    defaultIndexLag: '3M',
    defaultInterpolation: 'Monthly',
  },
  GBP_ZCIS: {
    family: 'GBP_ZCIS',
    marketShort: 'GBP',
    indexShort: 'RPI',
    centralBank: 'Bank of England',
    flag: '🇬🇧',
    subtitle: 'Forward zero-coupon inflation swap · UK RPI reference',
    defaultIndexLag: '2M',
    defaultInterpolation: 'Monthly',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family
 *  (caller renders a neutral fallback). */
export function zcisFamilyFor(family: string): ZcisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The "ZCIS Curve" dropdown options.  Single-curve primitive — no
 *  cross-family concept (distinct from
 *  ``calculate_cross_market_inflation_swap_spread`` which crosses two
 *  families). */
export const ZCIS_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

// ---------------------------------------------------------------------------
// Canonical forward-pair grid.  Maps human-readable "1Y1Y" / "2Y3Y" /
// "5Y5Y" labels (the desk-canonical forward shorthand) to (start_tenor,
// end_tenor) pairs the backend's tenor-pair input accepts.  Hand-curated
// to the V1-supported ZCIS curve tenor grid: 1Y / 2Y / 3Y / 5Y / 10Y /
// 20Y / 30Y on each of USD_ZCIS / EUR_ZCIS / GBP_ZCIS.
// ---------------------------------------------------------------------------

export interface ForwardPair {
  /** Canonical desk label (e.g. '5Y5Y'). */
  label: string;
  /** Start tenor of the forward window (e.g. '5Y'). */
  startTenor: string;
  /** End tenor of the forward window (e.g. '10Y'). */
  endTenor: string;
  /** Optional short description for the controls-strip tooltip. */
  description?: string;
}

export const ZCIS_FORWARD_PAIRS: ReadonlyArray<ForwardPair> = [
  {
    label: '1Y1Y',
    startTenor: '1Y',
    endTenor: '2Y',
    description:
      '1-year forward inflation starting 1 year out — near-front inflation-compensation read.',
  },
  {
    label: '2Y3Y',
    startTenor: '2Y',
    endTenor: '5Y',
    description:
      '3-year forward inflation starting 2 years out — mid-cycle inflation-compensation read.',
  },
  {
    label: '3Y2Y',
    startTenor: '3Y',
    endTenor: '5Y',
    description:
      '2-year forward inflation starting 3 years out — short-cycle inflation-compensation read.',
  },
  {
    label: '5Y5Y',
    startTenor: '5Y',
    endTenor: '10Y',
    description:
      '5-year forward inflation starting 5 years out — long-run inflation anchor (desk-canonical).',
  },
  {
    label: '10Y10Y',
    startTenor: '10Y',
    endTenor: '20Y',
    description:
      '10-year forward inflation starting 10 years out — secular inflation read.',
  },
  {
    label: '10Y20Y',
    startTenor: '10Y',
    endTenor: '30Y',
    description:
      '20-year forward inflation starting 10 years out — ultra-long inflation read.',
  },
];

/** The "Forward Pair" dropdown / pill options.  Each value encodes both
 *  start and end tenor in the canonical label form. */
export const ZCIS_FORWARD_PAIR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = ZCIS_FORWARD_PAIRS.map((p) => ({ value: p.label, label: p.label }));

/** Resolve a canonical forward-pair label to its (startTenor, endTenor)
 *  pair, or null for an unrecognised label (caller falls back to the
 *  default pair). */
export function forwardPairFor(label: string): ForwardPair | null {
  return ZCIS_FORWARD_PAIRS.find((p) => p.label === label) ?? null;
}

/** Curated short-form caveat for the compact footer when the wire
 *  ``methodology_label`` prose is too long to fit on a single line.
 *  Clearly distinct from the wire prose (sibling-tool pattern); the full
 *  wire-honesty disclosure is surfaced verbatim on the extended
 *  methodology card.  Documented in THESIS Q3 + Q5. */
export const INFLATION_SWAP_FORWARD_COMPACT_CAVEAT_PREFIX =
  'Swap-implied forward; index-family caveat applies';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseInflationSwapForwardArgs {
  curveFamily: string;
  startTenor: string;
  endTenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseInflationSwapForwardResult {
  data: InflationSwapForwardOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-
 *  detail endpoint; re-fetches when any input changes.  Mirrors the
 *  sibling OIS forward_rate hook shape so the per-tool surfaces look
 *  the same file-for-file. */
export function useInflationSwapForward(
  args: UseInflationSwapForwardArgs,
): UseInflationSwapForwardResult {
  const [data, setData] = useState<InflationSwapForwardOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: InflationSwapForwardDetailParams = {
    curve_family: args.curveFamily,
    start_tenor: args.startTenor,
    end_tenor: args.endTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.startTenor || !args.endTenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailInflationSwapForward(params)
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
    args.curveFamily,
    args.startTenor,
    args.endTenor,
    args.lookbackDays,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders — one source of truth for which numbers go where
// + how they're formatted + toned.
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  docs_revamped/03_standards/rendering_density.md §2.2 + the mockup
 *  design at this module's mockups/Compact.png:
 *
 *    1. FORWARD ZCIS (%)   (signed %, neutral tone, primary emphasis)
 *    2. 1D CHANGE (bps)    (signed bps + secondary % subtext,
 *                           toneForChange — POSITIVE = forward inflation
 *                           compensation repricing higher)
 *    3. Z-SCORE (252D)     (signed value + regime caption, toneForZScore)
 *
 *  These three are the desk-canonical "first three numbers" a PM reads
 *  off a ZCIS forward snapshot.  THESIS Q3 documents why these vs
 *  alternatives. */
export function compactKPIs(
  data: InflationSwapForwardOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.z_score_252d);
  const meta = zcisFamilyFor(cm.curve_family);
  return [
    {
      label: 'FORWARD ZCIS',
      value: signedFixed(cm.forward_zcis_pct, 3),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
      caption: meta
        ? `${forwardShortLabel(cm)} ${meta.indexShort} forward`
        : `${forwardShortLabel(cm)} forward`,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
      subtext: bpsAsPercentSubtext(cm.change_1d_bps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_252d, 2),
      tone: toneForZScore(cm.z_score_252d),
      caption: zRegime,
    },
  ];
}

/** The extended view's FULL KPI strip — every observable in
 *  current_metrics that the backend Output schema exposes.  Order matches
 *  the mockup design at this module's mockups/Extended.png as closely as
 *  the backend schema allows.
 *
 *  Unlike the OIS forward_rate sibling (which substitutes START / END
 *  SPOT cells because OIS doesn't expose 5D / 1M changes), the ZCIS
 *  schema DOES expose ``change_1w_bps`` + ``change_1m_bps``, so this
 *  KPI strip uses the mockup's canonical 5D / 1M change cells directly.
 *  The two endpoint ZCIS rates (``start_zcis_pct`` / ``end_zcis_pct``)
 *  are surfaced separately on the dual-compounding decomposition card
 *  upstream of the KPI strip. */
export function extendedKPIs(
  data: InflationSwapForwardOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'FORWARD ZCIS',
      value: signedFixed(cm.forward_zcis_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.change_1d_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.change_1d_bps),
      subtext: bpsAsPercentSubtext(cm.change_1d_bps, 3),
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
      value: bpsToPctFixed(cm.high_252d_bps, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: bpsToPctFixed(cm.low_252d_bps, 3),
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

/** Strip the curve_family prefix from ``forward_window_label`` to
 *  produce a short forward label like "5Y5Y" / "2Y3Y" / "10Y10Y" — the
 *  canonical desk shorthand for a forward pair.  Falls back to the
 *  start/end_tenor pair if the wire label is unexpectedly shaped. */
export function forwardShortLabel(
  cm: { forward_window_label?: string; start_tenor: string; end_tenor: string },
): string {
  if (cm.forward_window_label) {
    const parts = cm.forward_window_label.split(/\s+/);
    if (parts.length > 1) return parts[parts.length - 1];
  }
  return `${cm.start_tenor}-${cm.end_tenor}`;
}

/** Convert a bps value to a signed-fixed pct string (`+1.234`).  The
 *  ZCIS forward schema carries high / low only in BPS — the display
 *  needs PERCENT for consistency with the headline FORWARD ZCIS (%)
 *  cell.  Returns '—' for null/NaN. */
function bpsToPctFixed(bps: number | null, decimals: number): string {
  if (bps == null || Number.isNaN(bps)) return '—';
  const pct = bps / 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(decimals)}`;
}

// ---------------------------------------------------------------------------
// Reference-band builder — the chart's ±2σ z-score envelope translated to
// ZCIS forward % levels.
// ---------------------------------------------------------------------------

/** Empirical sanity bound for sovereign ZCIS forward rates.  Across every
 *  market in the playbook universe (USD CPI-U / EUR HICPxT / GBP RPI)
 *  forward inflation compensation fits comfortably inside [-1%, 10%].
 *  Values outside this band are nulled so recharts skips them and the
 *  area/line render continues uninterrupted.  Defensive layer; the real
 *  fix lives in the data pipeline.  Mirrors the OIS forward_rate
 *  sanitisation. */
const ZCIS_FORWARD_SANITY_MIN = -1.0;
const ZCIS_FORWARD_SANITY_MAX = 10;

/** Apply the sanity bound to the raw time-series.  Returns a new array
 *  with out-of-bound values nulled.  Caller hands the cleaned array to
 *  both the chart AND the reference-band builder so neither is distorted
 *  by outliers. */
export function sanitiseForwardSeries(
  rows: ReadonlyArray<{ date: string; forward_zcis_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.forward_zcis_pct == null
      || Number.isNaN(r.forward_zcis_pct)
      || r.forward_zcis_pct < ZCIS_FORWARD_SANITY_MIN
      || r.forward_zcis_pct > ZCIS_FORWARD_SANITY_MAX
        ? null
        : r.forward_zcis_pct,
  }));
}

/** Compute z-score envelope reference bands at the per-tool defaults:
 *  ±2σ extreme + ±1.5σ elevated.  Mean + std are computed from the
 *  SANITISED time-series so outliers don't distort the bands. */
export function buildReferenceBands(
  data: InflationSwapForwardOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseForwardSeries(data.time_series ?? []);
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
    { value: mean, label: 'Mean', tone: 'neutral', style: 'dashed' },
    { value: mean - 1.5 * std, label: '-1.5σ', tone: 'elevated', style: 'dashed' },
    { value: mean - 2 * std, label: '-2σ', tone: 'positive', style: 'dashed' },
  ];
}

// ---------------------------------------------------------------------------
// Stretch-context builder
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: InflationSwapForwardOutput,
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
  const direction = z > 0 ? 'above' : 'below';
  const directionPhrase =
    z > 0
      ? 'consistent with forward inflation compensation repricing HIGHER (hawkish inflation stretch)'
      : 'consistent with forward inflation compensation repricing LOWER (dovish inflation stretch)';

  if (regime === 'Extreme') {
    return (
      `Forward ZCIS is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Forward ZCIS is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `Forward ZCIS is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references — sourced from config.yaml conventions.
// Wire-honesty pattern (PR10 / P5): the headline ``methodology_label``
// disclosure on the EXTENDED methodology card is sourced from
// ``data.current_metrics.methodology_label`` (the YAML's
// ``methodology.what_it_does``), NOT hardcoded as a TS literal.  YAML
// edits flow to runtime.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: InflationSwapForwardOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = zcisFamilyFor(cm.curve_family);
  return [
    {
      label: 'Series',
      value: `${cm.curve_family} ${cm.forward_window_label} forward ZCIS rate`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid quoted ZCIS rate)`,
    },
    {
      label: 'Forward formula',
      value:
        'Dual-compounding geometric: '
        + 'f = ((1 + r_long)^T_long / (1 + r_short)^T_short)^(1 / (T_long − T_short)) − 1.',
    },
    {
      label: 'Sign convention',
      value:
        'POSITIVE 1D change = forward ZCIS repriced HIGHER (forward inflation '
        + 'compensation stretched higher); NEGATIVE = lower.',
    },
    {
      label: 'Z-score model',
      value: '252d rolling window, min periods 60, ddof 1 (YAML-locked)',
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile)',
    },
    {
      label: 'Index family',
      value: meta
        ? `${cm.inflation_index_family} · lag ${cm.index_lag} · ${cm.interpolation} interpolation`
          + (cm.underlying_index ? ` · ${cm.underlying_index}` : '')
        : `${cm.inflation_index_family} · lag ${cm.index_lag} · ${cm.interpolation} interpolation`,
    },
    {
      label: 'Disclosure',
      value: cm.methodology_label,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.16-17' },
    { label: 'BLS · CPI-U' },
    { label: 'Eurostat · HICP' },
    { label: 'ONS · RPI' },
    { label: 'ISDA · 2008 Inflation Definitions' },
  ];
}

// ---------------------------------------------------------------------------
// Compact-card caveat
// ---------------------------------------------------------------------------

/** Per-family compact-card caveat.  Combines the central-bank /
 *  inflation-index anchor with the short-form swap-implied honesty
 *  disclosure.  Designed to fit on a single line in the compact footer.
 *  The full wire-honesty prose lives on the extended methodology card. */
export function compactCaveatText(curveFamily: string): string {
  const meta = zcisFamilyFor(curveFamily);
  if (!meta) {
    return INFLATION_SWAP_FORWARD_COMPACT_CAVEAT_PREFIX;
  }
  return `${meta.indexShort} · ${INFLATION_SWAP_FORWARD_COMPACT_CAVEAT_PREFIX}`;
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need.
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
