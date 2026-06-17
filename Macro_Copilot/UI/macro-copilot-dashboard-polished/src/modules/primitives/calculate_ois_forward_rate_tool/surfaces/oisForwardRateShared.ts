// ============================================================================
// oisForwardRateShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for ``calculate_ois_forward_rate_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what an "OIS forward
// rate" is — an IMPLIED forward rate spanning a (start, end) window on
// ONE OIS curve family (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS /
// JPY_OIS / AUD_OIS / CAD_OIS).  OIS forwards are bootstrapped from the
// par-rate grid via the dual-compounding formula (simple for T ≤ 1Y,
// annual for T > 1Y) in ``shared.analytics.curve_bootstrap``.  Sign
// convention: forward_rate_pct is the absolute implied forward rate;
// daily_change_bps POSITIVE = the forward repriced HIGHER (hawkish
// implied-policy-path stretch).
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch
// the SAME typed-detail endpoint ``/api/v1/rates/detail/ois-forward-rate``
// (per rendering_density.md §1.1 + §10 — both views consume the same
// bridge; the compact view just renders less of it).  KPI builders +
// formatting + tone logic + the risk-neutral-implied-policy-path caveat
// live here in ONE place to prevent drift across surfaces.
//
// Rolling-z-score conventions are YAML-locked on this primitive (mirrors
// the OIS rate_level / curve_spread / butterfly siblings).  Only
// ``lookback_days`` + ``field_name`` are exposed at the controls layer —
// no "Advanced" panel.  The forward window itself is configured via the
// curve_family + (start_tenor, end_tenor) tenor-pair mode (the canonical
// desk read; SOFR 1Y1Y, 5Y5Y ESTR, 2Y1Y SONIA, etc); the backend's
// date-pair mode is reachable via the typed-detail endpoint but not
// surfaced as a controls-strip mode in V1.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOisForwardRate,
  type OisForwardRateDetailParams,
} from '@/services/ratesApi';
import type { OisForwardRateOutput } from '@/types/rates';
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
// OIS curve-family metadata.  The registry keys off curve_family because
// that uniquely determines the overnight-index identity (SOFR / ESTR /
// SONIA / TONA / AONIA / CORRA) the surfaces render.  Curve families
// enumerated mirror the backend's closed ``OIS_CURVE_FAMILY`` enum sourced
// from rates_agent/playbooks/ois.yml.
//
// NB: the shared ``countryCaveatFor`` registry only covers the linker
// domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) — OIS curve
// families are a disjoint universe, so we maintain a per-tool registry
// here (mirrors the sibling get_ois_rate_level_tool /
// calculate_ois_curve_spread_tool / calculate_ois_butterfly_tool
// patterns).
// ---------------------------------------------------------------------------

export interface OisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_SOFR_OIS'). */
  family: string;
  /** Market short code used in headers (e.g. 'USD'). */
  marketShort: string;
  /** Short overnight-index label (e.g. 'SOFR', 'ESTR', 'SONIA'). */
  indexShort: string;
  /** Central bank that anchors the overnight reference rate. */
  centralBank: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
  /** Full subtitle for the extended identity row. */
  subtitle: string;
}

const FAMILY_REGISTRY: Record<string, OisFamilyMeta> = {
  USD_SOFR_OIS: {
    family: 'USD_SOFR_OIS',
    marketShort: 'USD',
    indexShort: 'SOFR',
    centralBank: 'Federal Reserve',
    flag: '🇺🇸',
    subtitle: 'Forward-implied OIS rate · SOFR overnight reference',
  },
  EUR_ESTR_OIS: {
    family: 'EUR_ESTR_OIS',
    marketShort: 'EUR',
    indexShort: 'ESTR',
    centralBank: 'European Central Bank',
    flag: '🇪🇺',
    subtitle: 'Forward-implied OIS rate · €STR overnight reference',
  },
  GBP_SONIA_OIS: {
    family: 'GBP_SONIA_OIS',
    marketShort: 'GBP',
    indexShort: 'SONIA',
    centralBank: 'Bank of England',
    flag: '🇬🇧',
    subtitle: 'Forward-implied OIS rate · SONIA overnight reference',
  },
  JPY_OIS: {
    family: 'JPY_OIS',
    marketShort: 'JPY',
    indexShort: 'TONA',
    centralBank: 'Bank of Japan',
    flag: '🇯🇵',
    subtitle: 'Forward-implied OIS rate · TONA overnight reference',
  },
  AUD_OIS: {
    family: 'AUD_OIS',
    marketShort: 'AUD',
    indexShort: 'AONIA',
    centralBank: 'Reserve Bank of Australia',
    flag: '🇦🇺',
    subtitle: 'Forward-implied OIS rate · AONIA overnight reference',
  },
  CAD_OIS: {
    family: 'CAD_OIS',
    marketShort: 'CAD',
    indexShort: 'CORRA',
    centralBank: 'Bank of Canada',
    flag: '🇨🇦',
    subtitle: 'Forward-implied OIS rate · CORRA overnight reference',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family (caller
 *  renders a neutral fallback). */
export function oisFamilyFor(family: string): OisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The "OIS Curve" dropdown options.  Single-curve primitive — no
 *  cross-family concept (distinct from
 *  ``calculate_ois_cross_market_spread`` which crosses two families). */
export const OIS_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

// ---------------------------------------------------------------------------
// Canonical forward-pair grid.  Maps human-readable "1Y1Y" / "2Y1Y" /
// "5Y5Y" labels (the desk-canonical forward shorthand) to (start_tenor,
// end_tenor) pairs the backend's tenor-mode input accepts.  Hand-curated
// to the V1-supported curve tenor grid (most OIS curves have 1Y, 2Y, 3Y,
// 5Y, 10Y, 20Y, 30Y on the long-end — the short-end 1W/1M/3M points are
// outside the typical forward read).
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

export const OIS_FORWARD_PAIRS: ReadonlyArray<ForwardPair> = [
  {
    label: '1Y1Y',
    startTenor: '1Y',
    endTenor: '2Y',
    description: '1-year forward starting 1 year out — near-front policy-path read.',
  },
  {
    label: '2Y1Y',
    startTenor: '2Y',
    endTenor: '3Y',
    description: '1-year forward starting 2 years out — terminal-rate read.',
  },
  {
    label: '3Y2Y',
    startTenor: '3Y',
    endTenor: '5Y',
    description: '2-year forward starting 3 years out — mid-cycle read.',
  },
  {
    label: '5Y5Y',
    startTenor: '5Y',
    endTenor: '10Y',
    description: '5-year forward starting 5 years out — long-run policy anchor.',
  },
  {
    label: '10Y10Y',
    startTenor: '10Y',
    endTenor: '20Y',
    description: '10-year forward starting 10 years out — secular-rate read.',
  },
];

/** The "Forward Pair" dropdown / pill options.  Each value encodes both
 *  start and end tenor in the canonical label form. */
export const OIS_FORWARD_PAIR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = OIS_FORWARD_PAIRS.map((p) => ({ value: p.label, label: p.label }));

/** Resolve a canonical forward-pair label to its (startTenor, endTenor)
 *  pair, or null for an unrecognised label (caller falls back to the
 *  default pair). */
export function forwardPairFor(label: string): ForwardPair | null {
  return OIS_FORWARD_PAIRS.find((p) => p.label === label) ?? null;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact footer + the extended methodology card.  OIS forward rates
 *  price the RISK-NEUTRAL EXPECTED POLICY PATH between two curve points;
 *  they are NOT a forecast of realised central-bank decisions.
 *
 *  TODO(PR10): when the backend ships
 *  ``current_metrics.methodology_label`` on this primitive's Output
 *  (today the schema lacks it — mirrors the OIS rate_level / curve_spread
 *  / butterfly siblings), switch the methodology card's "Disclosure" row
 *  to source from the wire. */
export const OIS_FORWARD_RATE_COMPACT_CAVEAT_PREFIX =
  'OIS forward (risk-neutral implied policy path)';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseOisForwardRateArgs {
  curveFamily: string;
  startTenor: string;
  endTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseOisForwardRateResult {
  data: OisForwardRateOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-
 *  detail endpoint; re-fetches when any input changes.  Mirrors the
 *  sibling OIS rate_level hook shape so the per-tool surfaces look the
 *  same file-for-file. */
export function useOisForwardRate(
  args: UseOisForwardRateArgs,
): UseOisForwardRateResult {
  const [data, setData] = useState<OisForwardRateOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OisForwardRateDetailParams = {
    curve_family: args.curveFamily,
    start_tenor: args.startTenor,
    end_tenor: args.endTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
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
    fetchDetailOisForwardRate(params)
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
    args.asOfDate,
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
 *    1. FORWARD RATE       (signed %, neutral tone, primary emphasis)
 *    2. 1D CHANGE (bps)    (signed bps + secondary % subtext,
 *                           toneForChange — POSITIVE = hawkish forward
 *                           repricing)
 *    3. Z-SCORE (252D)     (signed value + regime caption, toneForZScore)
 *
 *  These three are the desk-canonical "first three numbers" a PM reads
 *  off an OIS forward-rate snapshot.  THESIS Q3 documents why these vs
 *  alternatives. */
export function compactKPIs(
  data: OisForwardRateOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.current_z_score);
  return [
    {
      label: 'FORWARD RATE',
      value: signedFixed(cm.forward_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
      caption: `${cm.forward_label} forward`,
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
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zRegime,
    },
  ];
}

/** The extended view's FULL KPI strip — every observable in
 *  current_metrics that the backend Output schema exposes.  Order matches
 *  the mockup design at this module's mockups/Extended.png as closely as
 *  the backend schema allows.
 *
 *  KNOWN MOCKUP DEVIATION: the mockup's 9-cell strip includes "5D CHANGE"
 *  and "1M CHANGE" cells.  The backend's ``OISForwardRateCurrentMetrics``
 *  schema does NOT expose ``weekly_change_bps`` / ``monthly_change_bps``
 *  in V1 — config.yaml's ``methodology.planned_extensions`` documents
 *  this as a deferred follow-up PR (period_offsets convention block).
 *  We substitute START SPOT and END SPOT cells (the
 *  ``start_spot_rate_pct`` / ``end_spot_rate_pct`` interpolated par
 *  rates at the forward window's endpoints) — these are the natural
 *  contextual reads for an OIS forward (the forward IS the bootstrap-
 *  implied rate that ties the two spots together) and are both exposed
 *  on the backend wire today.  When the backend lands the period-changes
 *  extension, this builder swaps in the 5D / 1M cells in their canonical
 *  ordering. */
export function extendedKPIs(
  data: OisForwardRateOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'FORWARD RATE',
      value: signedFixed(cm.forward_rate_pct, 3),
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
      label: 'START SPOT',
      value: signedFixed(cm.start_spot_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
      caption:
        cm.start_years != null
          ? `${unsignedFixed(cm.start_years, 2)}y point`
          : undefined,
    },
    {
      label: 'END SPOT',
      value: signedFixed(cm.end_spot_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
      caption:
        cm.end_years != null
          ? `${unsignedFixed(cm.end_years, 2)}y point`
          : undefined,
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
      value: signedFixed(cm.high_252d_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${data.time_series.length}`,
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — the chart's ±2σ z-score envelope translated to
// OIS-forward % levels.
// ---------------------------------------------------------------------------

/** Empirical sanity bound for sovereign OIS forward rates.  Across every
 *  market in the playbook universe forward rates fit comfortably inside
 *  [-1.5%, 12%] (BoJ pre-2024 ≈ 0%, post-2022 Fed cycle peak ≈ 5.5%).
 *  Values outside this band are nulled so recharts skips them and the
 *  area/line render continues uninterrupted.  Defensive layer; the real
 *  fix lives in the data pipeline.  Mirrors the OIS rate_level
 *  sanitisation. */
const OIS_FORWARD_SANITY_MIN = -1.5;
const OIS_FORWARD_SANITY_MAX = 12;

/** Apply the sanity bound to the raw time-series.  Returns a new array
 *  with out-of-bound values nulled.  Caller hands the cleaned array to
 *  both the chart AND the reference-band builder so neither is distorted
 *  by outliers. */
export function sanitiseForwardSeries(
  rows: ReadonlyArray<{ date: string; forward_rate_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.forward_rate_pct == null
      || Number.isNaN(r.forward_rate_pct)
      || r.forward_rate_pct < OIS_FORWARD_SANITY_MIN
      || r.forward_rate_pct > OIS_FORWARD_SANITY_MAX
        ? null
        : r.forward_rate_pct,
  }));
}

/** Compute z-score envelope reference bands at the per-tool defaults:
 *  ±2σ extreme + ±1.5σ elevated.  Mean + std are computed from the
 *  SANITISED time-series so outliers don't distort the bands. */
export function buildReferenceBands(
  data: OisForwardRateOutput,
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
  data: OisForwardRateOutput,
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
      ? 'consistent with the implied policy path repricing higher (hawkish stretch)'
      : 'consistent with the implied policy path repricing lower (dovish stretch)';

  if (regime === 'Extreme') {
    return (
      `OIS forward is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `OIS forward is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `OIS forward is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references — sourced from config.yaml conventions.
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: OisForwardRateOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = oisFamilyFor(cm.curve_family);
  return [
    {
      label: 'Series',
      value: `${cm.curve_family} ${cm.forward_label} implied forward rate`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid par-swap rate)`,
    },
    {
      label: 'Forward formula',
      value:
        'Dual compounding: DF(T) = 1/(1+R·T) for T ≤ 1Y, 1/(1+R)^T for T > 1Y; '
        + 'forward = (DF_start / DF_end − 1) / (end_years − start_years).',
    },
    {
      label: 'Sign convention',
      value:
        'POSITIVE 1D change = forward repriced HIGHER (hawkish implied-policy-path stretch); '
        + 'NEGATIVE = lower (dovish).',
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
      label: 'Data cleaning',
      value: 'Weekdays only · Forward-fill up to 5 days',
    },
    {
      label: 'Disclosure',
      value: meta
        ? `Risk-neutral implied policy path anchored to ${meta.centralBank}'s ${meta.indexShort} overnight reference. OIS forwards price the EXPECTED policy path, not realised central-bank decisions.`
        : 'Risk-neutral implied policy path. OIS forwards price the EXPECTED policy path, not realised central-bank decisions.',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.18' },
    { label: 'Federal Reserve · SOFR' },
    { label: 'ECB · €STR' },
    { label: 'BoE · SONIA' },
    { label: 'BoJ · TONA' },
  ];
}

// ---------------------------------------------------------------------------
// Display-window utility (for the compact card's short caveat).
// ---------------------------------------------------------------------------

/** Per-family compact-card caveat.  Combines the central-bank anchor with
 *  the canonical risk-neutral policy-path disclosure.  Designed to fit on
 *  a single line in the compact footer. */
export function compactCaveatText(curveFamily: string): string {
  const meta = oisFamilyFor(curveFamily);
  if (!meta) {
    return `OIS forward (risk-neutral implied policy path)`;
  }
  return `${meta.centralBank} · ${OIS_FORWARD_RATE_COMPACT_CAVEAT_PREFIX}`;
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need.
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
