// ============================================================================
// oisCurveSpreadShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for
// ``calculate_ois_curve_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "same-curve OIS
// tenor spread" is — a 2-point spread on a SINGLE OIS par-swap curve family
// (long_tenor − short_tenor).  The wire ships the spread + 1d change ALREADY
// IN BPS (the OIS sub-domain BPS convention); per-leg endpoint OIS rates
// ship in PERCENT (the natural unit for an OIS par-swap rate).
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch the
// SAME typed-detail endpoint ``/api/v1/rates/detail/ois-curve-spread`` (per
// rendering_density.md §1.1 + §10 — both views consume the same bridge; the
// compact view just renders less of it).  KPI builders + formatting + tone
// logic + the risk-neutral-policy-pricing caveat live here in ONE place to
// prevent drift across surfaces.
//
// NB on backend Output shape — distinct from the linker / sovereign curve-
// spread siblings the backend Output does NOT carry ``methodology_label``,
// ``weekly_change_bps`` / ``monthly_change_bps``, ``percentile_252d`` /
// ``high_252d_bps`` / ``low_252d_bps``, ``observation_count``, or separate
// ``short_tenor`` / ``long_tenor`` strings (only the combined
// ``spread_label`` like '2s10s').  The per-tenor identity is reconstructed
// from the request params + the per-tool curve_family registry below; the
// 252d percentile / range / observation_count are RE-DERIVED CLIENT-SIDE
// from the ``time_series_spread.rows`` array so the mockup's extended KPI
// strip is honoured without inventing wire data.  The methodology card
// sources the canonical caveat strings from this file with an explicit
// ``// TODO`` marker for when the backend ships a wire-honesty
// ``methodology_label`` (then the methodology card switches to consume it —
// one-line edit).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOisCurveSpread,
  type OisCurveSpreadDetailParams,
} from '@/services/ratesApi';
import type { OisCurveSpreadOutput } from '@/types/rates';
import {
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
// OIS curve-family metadata.  Same-curve spreads are single-family objects;
// the registry keys off curve_family because that uniquely determines the
// overnight-index identity (SOFR / ESTR / SONIA / TONA / AONIA / CORRA) the
// surfaces render.  Curve families enumerated mirror the backend's closed
// ``OIS_CURVE_FAMILY`` enum sourced from rates_agent/playbooks/ois.yml.
// ---------------------------------------------------------------------------

export interface OisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_SOFR_OIS'). */
  family: string;
  /** Market short code used on the wire (e.g. 'USD'). */
  marketShort: string;
  /** Short overnight-index label (e.g. 'SOFR', 'ESTR', 'SONIA'). */
  indexShort: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, OisFamilyMeta> = {
  USD_SOFR_OIS: {
    family: 'USD_SOFR_OIS',
    marketShort: 'USD',
    indexShort: 'SOFR',
    flag: '🇺🇸',
  },
  EUR_ESTR_OIS: {
    family: 'EUR_ESTR_OIS',
    marketShort: 'EUR',
    indexShort: 'ESTR',
    flag: '🇪🇺',
  },
  GBP_SONIA_OIS: {
    family: 'GBP_SONIA_OIS',
    marketShort: 'GBP',
    indexShort: 'SONIA',
    flag: '🇬🇧',
  },
  JPY_OIS: {
    family: 'JPY_OIS',
    marketShort: 'JPY',
    indexShort: 'TONA',
    flag: '🇯🇵',
  },
  AUD_OIS: {
    family: 'AUD_OIS',
    marketShort: 'AUD',
    indexShort: 'AONIA',
    flag: '🇦🇺',
  },
  CAD_OIS: {
    family: 'CAD_OIS',
    marketShort: 'CAD',
    indexShort: 'CORRA',
    flag: '🇨🇦',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family (caller
 *  renders a neutral fallback). */
export function oisFamilyFor(family: string): OisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The single "OIS Curve" dropdown options.  Single-curve primitive — no
 *  cross-family concept (distinct from ``calculate_ois_cross_market_spread``
 *  which crosses two families).  One curve, one set of pillars. */
export const OIS_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

/** Tenor pair presets per curve_family — the desk-canonical (short, long)
 *  tuples on the ingested OIS grid.  Surfacing only registered pairs makes
 *  invalid orderings unreachable on the Monitor widget; the Build views
 *  surface independent short/long dropdowns plus a long-tenor filter that
 *  enforces ``long > short`` at the input layer (the backend re-validates
 *  ``short_tenor != long_tenor`` regardless). */
export interface OisCurveSpreadPair {
  short: string;
  long: string;
  label: string; // e.g. "2s10s"
}

export const OIS_CURVE_SPREAD_PAIRS_BY_CURVE: Record<
  string,
  ReadonlyArray<OisCurveSpreadPair>
> = {
  USD_SOFR_OIS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
    { short: '2Y', long: '5Y', label: '2s5s' },
    { short: '10Y', long: '30Y', label: '10s30s' },
    { short: '3M', long: '2Y', label: '3M/2Y' },
  ],
  EUR_ESTR_OIS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
    { short: '1Y', long: '5Y', label: '1s5s' },
  ],
  GBP_SONIA_OIS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
    { short: '1Y', long: '5Y', label: '1s5s' },
  ],
  JPY_OIS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
    { short: '5Y', long: '30Y', label: '5s30s' },
  ],
  AUD_OIS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
  ],
  CAD_OIS: [
    { short: '2Y', long: '10Y', label: '2s10s' },
  ],
};

/** Per-curve tenor grids for the Build view's independent short/long
 *  dropdowns (long-tenor list is filtered by ``tenorToYears`` so the user
 *  cannot construct a long ≤ short pair). */
export const TENOR_OPTIONS_BY_CURVE: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  USD_SOFR_OIS: ['1W', '1M', '3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  EUR_ESTR_OIS: ['1W', '1M', '3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  GBP_SONIA_OIS: ['1W', '1M', '3M', '6M', '1Y', '2Y', '3Y', '5Y', '10Y', '20Y', '30Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  JPY_OIS: ['3M', '6M', '1Y', '2Y', '5Y', '10Y', '20Y', '30Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  AUD_OIS: ['3M', '6M', '1Y', '2Y', '5Y', '10Y'].map(
    (t) => ({ value: t, label: t }),
  ),
  CAD_OIS: ['3M', '6M', '1Y', '2Y', '5Y', '10Y'].map(
    (t) => ({ value: t, label: t }),
  ),
};

/** Minimal tenor → years parser (mirrors the backend's tenor_to_years for
 *  the common <n>W / <n>M / <n>Y forms).  Used CLIENT-SIDE to filter the
 *  long-tenor dropdown to strictly-longer tenors so the "long > short"
 *  validity rule is enforced at the input layer — the backend re-validates
 *  ``short_tenor != long_tenor`` regardless. */
export function tenorToYears(tenor: string): number {
  const m = /^(\d+(?:\.\d+)?)\s*([WMY])$/i.exec(tenor.trim());
  if (!m) return NaN;
  const n = parseFloat(m[1]);
  const unit = m[2].toUpperCase();
  if (unit === 'W') return n / 52;
  if (unit === 'M') return n / 12;
  return n;
}

/** Short label for a tenor pair, e.g. ('2Y','10Y') → '2s10s'; ('3M','2Y') →
 *  '3M/2Y'.  Pure-year pairs strip the 'Y'; sub-year shorts retain unit. */
export function spreadShortLabel(shortTenor: string, longTenor: string): string {
  const pureYear =
    /^\d+Y$/i.test(shortTenor) && /^\d+Y$/i.test(longTenor);
  if (pureYear) {
    return `${shortTenor.replace(/Y$/i, '')}s${longTenor.replace(/Y$/i, '')}s`;
  }
  return `${shortTenor}/${longTenor}`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the compact
 *  footer + the extended methodology card.  OIS curves price the EXPECTED
 *  POLICY PATH under the risk-neutral measure; a curve-spread is the SHAPE
 *  of that expectation (term structure of expected policy), NOT a forecast
 *  of realised central-bank decisions.  Mockup-faithful one-liner.
 *
 *  TODO(PR10): when the backend ships ``current_metrics.methodology_label``
 *  (today the OIS curve_spread Output schema lacks it — siblings in
 *  inflation_indexed_bonds / inflation_swaps already carry it), switch the
 *  methodology card's "Disclosure" row to source from the wire.  Tracked
 *  alongside the OIS sub-domain's PR10 backlog. */
export const OIS_CURVE_SPREAD_COMPACT_CAVEAT =
  'Risk-neutral OIS curve shape (policy expectations, not outcomes).';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseOisCurveSpreadArgs {
  curveFamily: string;
  shortTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseOisCurveSpreadResult {
  data: OisCurveSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS butterfly
 *  hook shape so the per-tool surfaces look the same shape file-for-file. */
export function useOisCurveSpread(
  args: UseOisCurveSpreadArgs,
): UseOisCurveSpreadResult {
  const [data, setData] = useState<OisCurveSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OisCurveSpreadDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.shortTenor || !args.longTenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailOisCurveSpread(params)
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
    args.shortTenor,
    args.longTenor,
    args.lookbackDays,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE spread = STEEPER curve (long > short);
// NEGATIVE = INVERTED.  Surfaced in the compact KPI primary cell.
// ---------------------------------------------------------------------------

export function shapeRegimeCaption(
  spreadBps: number | null | undefined,
): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  if (spreadBps > 0) return 'Steeper';
  if (spreadBps < 0) return 'Inverted';
  return 'Flat';
}

/** Z-score caption combining stretch regime + shape direction.  Positive z =
 *  spread elevated above its trailing mean → curve STEEPER than norm.
 *  Negative z = spread below trailing mean → curve FLATTER (or more inverted)
 *  than norm.  At |z| < 1.0 ("Normal") the caption is "Neutral". */
export function zScoreCaptionForSpread(
  z: number | null | undefined,
): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z)) return '—';
  if (regime === 'Normal') return 'Neutral';
  const direction = z > 0 ? 'Steeper' : 'Flatter';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// Wire-derived helpers — percentile / 252d range / observation_count are
// computed CLIENT-SIDE from time_series_spread.rows because the OIS
// curve-spread Output is leaner than its linker / sovereign siblings.
// ---------------------------------------------------------------------------

/** Numeric values from the canonical bps series in chronological order
 *  (null/NaN filtered).  Used for percentile / high / low / observation
 *  counting and for the change-over-N-trading-days computations. */
function spreadValues(data: OisCurveSpreadOutput): number[] {
  const rows = data.time_series_spread?.rows ?? [];
  return rows
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
}

/** Trailing 252 trading-day window of bps values, oldest → newest. */
function trailing252(values: number[]): number[] {
  return values.length <= 252 ? values : values.slice(values.length - 252);
}

/** Client-side percentile of the latest value within the trailing 252d
 *  window (0–100).  Returns null when the latest value or window is
 *  unavailable.  Matches the desk-canonical "where in the past year is
 *  today's value" framing (mockup Extended.png shows "78th"). */
export function trailingPercentile252d(
  data: OisCurveSpreadOutput,
): number | null {
  const values = spreadValues(data);
  if (values.length === 0) return null;
  const window = trailing252(values);
  if (window.length < 2) return null;
  const latest = values[values.length - 1];
  const sorted = [...window].sort((a, b) => a - b);
  let countLE = 0;
  for (const v of sorted) {
    if (v <= latest) countLE += 1;
  }
  return (countLE / sorted.length) * 100;
}

/** Trailing 252d high (bps).  Null when no observations. */
export function trailingHigh252d(data: OisCurveSpreadOutput): number | null {
  const window = trailing252(spreadValues(data));
  return window.length > 0 ? Math.max(...window) : null;
}

/** Trailing 252d low (bps).  Null when no observations. */
export function trailingLow252d(data: OisCurveSpreadOutput): number | null {
  const window = trailing252(spreadValues(data));
  return window.length > 0 ? Math.min(...window) : null;
}

/** Total observation count in the displayed window (mockup KPI strip). */
export function observationCount(data: OisCurveSpreadOutput): number {
  return spreadValues(data).length;
}

/** Change over the last ``lookbackDays`` trading-day rows on the wire
 *  series.  ``lookbackDays`` is interpreted as a row offset on the displayed
 *  series (NOT a calendar window) — the backend's canonical series is daily
 *  trading observations.  Returns null when the window is too short. */
function changeOverRows(
  data: OisCurveSpreadOutput,
  rowOffset: number,
): number | null {
  const values = spreadValues(data);
  if (values.length <= rowOffset) return null;
  const latest = values[values.length - 1];
  const past = values[values.length - 1 - rowOffset];
  return latest - past;
}

/** 3-month change in bps (~63 trading days, mockup Extended.png "3M
 *  CHANGE"). */
export function change3mBps(data: OisCurveSpreadOutput): number | null {
  return changeOverRows(data, 63);
}

/** 12-month change in bps (~252 trading days, mockup Extended.png "12M
 *  CHANGE"). */
export function change12mBps(data: OisCurveSpreadOutput): number | null {
  return changeOverRows(data, 252);
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (2s10s)   — signed bps, primary emphasis; the curve-shape
 *                          value.  Caption shows "Steeper"/"Inverted"/"Flat".
 *    2. 1D CHANGE        — signed bps, toneForChange.
 *    3. Z-SCORE (252D)   — signed value + regime+direction caption (e.g.
 *                          "Elevated Steeper" at z=+1.63).
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: OisCurveSpreadOutput,
  shortTenor: string,
  longTenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const label = spreadShortLabel(shortTenor, longTenor) || cm.spread_label;
  return [
    {
      label: `SPREAD (${label})`,
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: shapeRegimeCaption(cm.current_spread_bps),
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
      caption: zScoreCaptionForSpread(cm.current_z_score),
    },
  ];
}

/** Extended view's headline KPI strip (mockups/Extended.png).  Spread + 1d
 *  + 3m + 12m + z + percentile + 252d high/low + observations.  Percentile /
 *  high / low / observation_count are computed CLIENT-SIDE from
 *  ``time_series_spread`` because the OIS curve-spread Output is leaner than
 *  the linker / sovereign siblings (no wire fields for these). */
export function extendedKPIs(
  data: OisCurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const pct = trailingPercentile252d(data);
  const hi = trailingHigh252d(data);
  const lo = trailingLow252d(data);
  const obs = observationCount(data);
  const c3m = change3mBps(data);
  const c12m = change12mBps(data);
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: shapeRegimeCaption(cm.current_spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: '3M CHANGE',
      value: signedFixed(c3m, 1),
      unit: 'bp',
      tone: toneForChange(c3m),
    },
    {
      label: '12M CHANGE',
      value: signedFixed(c12m, 1),
      unit: 'bp',
      tone: toneForChange(c12m),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreCaptionForSpread(cm.current_z_score),
    },
    {
      label: 'PERCENTILE (252D)',
      value: pct != null ? `${Math.round(pct)}` : '—',
      unit: 'th',
      caption: bucketForPercentile(pct),
    },
    {
      label: '252D HIGH',
      value: signedFixed(hi, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(lo, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: obs > 0 ? String(obs) : '—',
      tone: 'neutral',
    },
  ];
}

/** Two-leg decomposition row — short + long endpoint OIS rates (PERCENT) so
 *  the desk can audit ``long − short`` on the same screen.  Mirrors the
 *  mockup's "TWO-LEG OIS DECOMPOSITION" row.  Per-tenor labels are
 *  reconstructed from the request pair (the wire does not carry separate
 *  ``short_tenor`` / ``long_tenor`` strings — only the combined
 *  ``spread_label``). */
export function decompositionKPIs(
  data: OisCurveSpreadOutput,
  shortTenor: string,
  longTenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT OIS (${shortTenor})`,
      value: signedFixed(cm.short_tenor_rate, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG OIS (${longTenor})`,
      value: signedFixed(cm.long_tenor_rate, 2),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for OIS curve spreads (bps).  Across the playbook universe
// OIS 2s10s / 5s30s / 1s5s spreads cluster within ±300 bps; stress
// excursions to ±400 bps or so are plausible.  Anything well outside is
// almost certainly a generic-ticker roll artifact on one endpoint.  Mirrors
// the sovereign / linker spread sanity bounds.
const OIS_SPREAD_SANITY_MIN_BPS = -500;
const OIS_SPREAD_SANITY_MAX_BPS = 500;

/** Sanity-bound the canonical OIS spread series rows for the chart layer.
 *  Values already arrive in bps; clamp outliers to null. */
export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < OIS_SPREAD_SANITY_MIN_BPS
        || r.value > OIS_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: OisCurveSpreadOutput,
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
  data: OisCurveSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  const pct = trailingPercentile252d(data);
  if (cm.current_z_score == null && pct == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(pct);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.curve_family,
  );

  return {
    percentile: pct != null ? { value: pct, bucket } : undefined,
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
  curveFamily: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'steeper' : 'flatter';
  const meta = oisFamilyFor(curveFamily);
  const family = meta ? `${meta.marketShort} ${meta.indexShort} OIS` : curveFamily;
  const policyLine =
    ' Shape of the expected policy path under the risk-neutral measure — read the move as how the market is pricing the TERM STRUCTURE of policy expectations, not as a forecast of realised central-bank decisions.';
  if (regime === 'Extreme') {
    return (
      `${family} curve is extreme ${direction} versus its trailing-year mean — `
      + `sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + policyLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} curve is elevated ${direction} versus trailing-year history.`
      + policyLine
    );
  }
  return (
    `${family} curve spread is within its trailing-year norm; `
    + 'no extreme steepening or flattening stretch.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty
 *  disclosure should flow from ``current_metrics.methodology_label``; the
 *  OIS curve_spread backend Output currently does NOT carry that field (the
 *  sub-domain hasn't caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds / inflation_swaps already do).  Until the
 *  backend ships it, the "Disclosure" row sources from the per-tool canonical
 *  caveat below (one-line edit to switch when the wire lands the field). */
export function buildMethodologyRows(
  data: OisCurveSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
  shortTenor: string,
  longTenor: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = oisFamilyFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = (long_rate − short_rate) × 100, single curve `
        + `(${cm.curve_family}); raw OIS par-rate space — no basis subtraction, `
        + 'no convexity adjustment, no fitted curve',
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = STEEPER (long > short) · NEGATIVE = INVERTED',
    },
    {
      label: 'Valid tenor pair',
      value: `long tenor (${longTenor}) > short tenor (${shortTenor}) — enforced at the input + compute layers`,
    },
    {
      label: 'Pair',
      value: `${shortTenor} / ${longTenor} (${cm.spread_label})`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (OIS quoted rate, both endpoint series)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the bps spread series (YAML-locked — no input-layer override on this primitive)`,
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days)`,
    },
    {
      label: 'Overnight index',
      value: meta
        ? `${meta.marketShort} ${meta.indexShort} (${cm.curve_family})`
        : cm.curve_family,
    },
    {
      label: 'Units',
      value: 'Spread + changes + 252d range in BPS (OIS sub-domain bps convention); per-leg endpoint rates in PERCENT.',
    },
    {
      label: 'Same-curve invariant',
      value: 'Both legs share curve_family — cross-curve OIS spreads are forbidden at the input schema layer (would compose on calculate_ois_cross_market_spread).',
    },
    {
      label: 'Disclosure',
      value: OIS_CURVE_SPREAD_COMPACT_CAVEAT,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'NY Fed SOFR' },
    { label: 'ECB ESTR' },
    { label: 'BoE SONIA' },
    { label: 'Bloomberg OIS' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
