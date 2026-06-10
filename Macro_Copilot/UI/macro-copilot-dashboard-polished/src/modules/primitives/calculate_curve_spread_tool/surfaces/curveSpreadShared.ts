// ============================================================================
// curveSpreadShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the existing Monitor widgets for
// ``calculate_curve_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "same-curve sovereign
// tenor spread" is — a 2-point spread on a SINGLE sovereign yield curve
// (long_tenor − short_tenor, expressed in basis points).  The wire ships the
// spread + 1d change ALREADY IN BPS; per-leg endpoint yields ship in PERCENT.
//
// The Build views fetch the SAME typed-detail endpoint
// ``/api/v1/rates/detail/spread`` as the Monitor widgets (per
// methodology_exposure.md §5 + rendering_density.md §1.1 — both views
// consume the same bridge; the compact view just renders less of it).  KPI
// builders, formatting, tone logic, and the canonical sovereign caveat live
// here in ONE place to prevent drift across surfaces.
//
// NB on backend Output shape — the sovereign curve_spread Output is leaner
// than its linker / cross_market siblings.  ``CurveSpreadCurrentMetrics``
// carries (as_of_date, curve_family, spread_label, current_spread_bps,
// daily_change_bps, current_z_score, rolling_window_days, short_tenor_yield,
// long_tenor_yield).  It does NOT carry ``methodology_label``,
// ``weekly_change_bps`` / ``monthly_change_bps``, ``percentile_252d``,
// ``high_252d_bps`` / ``low_252d_bps``, ``observation_count``, or separate
// ``short_tenor`` / ``long_tenor`` strings (only the combined
// ``spread_label``).  The per-tenor identity is reconstructed from the
// request params; 5d / 1m / 3m / 12m / percentile / 252d range / observation
// counts are RE-DERIVED CLIENT-SIDE from the ``time_series`` rows so the
// mockup's KPI strip is honoured without inventing wire data.  The
// methodology card sources the canonical caveat from this file with a
// ``TODO(PR10)`` marker for when the backend ships
// ``current_metrics.methodology_label`` (then the disclosure row switches to
// consume the wire — one-line edit).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailSpread,
  type SpreadDetailParams,
} from '@/services/ratesApi';
import type { CurveSpreadOutput } from '@/types/rates';
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
// Sovereign curve-family metadata.  Same-curve spreads are single-family
// objects; the registry keys off curve_family because that uniquely
// determines the sovereign identity (UST / Bund / Gilt / JGB / OAT / BTP /
// Bono / AUS / CAN) the surfaces render.  Mirrors the existing
// ``CURVE_LABEL`` map in the legacy ResultRenderer + Monitor widgets — kept
// in ONE place so a new sovereign curve is a one-line addition here, not
// three.
// ---------------------------------------------------------------------------

export interface SovereignFamilyMeta {
  /** Curve family identifier (e.g. 'UST'). */
  family: string;
  /** Short market label used in identity chips (e.g. 'UST'). */
  shortLabel: string;
  /** Full descriptive name (e.g. 'US Treasuries'). */
  longLabel: string;
  /** Country flag emoji. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, SovereignFamilyMeta> = {
  UST: { family: 'UST', shortLabel: 'UST', longLabel: 'US Treasuries', flag: '🇺🇸' },
  DE_BUND: { family: 'DE_BUND', shortLabel: 'Bund', longLabel: 'German Bunds', flag: '🇩🇪' },
  UK_GILT: { family: 'UK_GILT', shortLabel: 'Gilt', longLabel: 'UK Gilts', flag: '🇬🇧' },
  JGB: { family: 'JGB', shortLabel: 'JGB', longLabel: 'Japan Government Bonds', flag: '🇯🇵' },
  FR_OAT: { family: 'FR_OAT', shortLabel: 'OAT', longLabel: 'French OATs', flag: '🇫🇷' },
  IT_BTP: { family: 'IT_BTP', shortLabel: 'BTP', longLabel: 'Italian BTPs', flag: '🇮🇹' },
  ES_BONO: { family: 'ES_BONO', shortLabel: 'Bono', longLabel: 'Spanish Bonos', flag: '🇪🇸' },
  AU_GOVT: { family: 'AU_GOVT', shortLabel: 'AUS', longLabel: 'Australian Govt Bonds', flag: '🇦🇺' },
  CANADA_GOVT: { family: 'CANADA_GOVT', shortLabel: 'CAN', longLabel: 'Canadian Govt Bonds', flag: '🇨🇦' },
};

export function sovereignFamilyFor(family: string): SovereignFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** All registered sovereign curve families, as the controls-strip "Curve"
 *  dropdown options.  Mirrors the existing CURVE_OPTIONS in
 *  @/lib/monitorParamOptions but with a per-tool label format (short ·
 *  long) that the Build identity bar reads naturally. */
export const SOVEREIGN_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Tenor grid for the sovereign curve_spread tool.  The ingested
 *  curated series cover the standard pillars (2/3/5/7/10/20/30 plus
 *  the Treasury-only 1Y / 6M).  Surfacing a broad-but-conservative
 *  list keeps the Build "Long tenor" / "Short tenor" dropdowns
 *  general across families; the backend will return an empty series
 *  if a particular family doesn't carry the chosen tenor. */
export const SOVEREIGN_TENOR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  { value: '2Y', label: '2Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '7Y', label: '7Y' },
  { value: '10Y', label: '10Y' },
  { value: '20Y', label: '20Y' },
  { value: '30Y', label: '30Y' },
];

/** Minimal tenor → years parser.  Used CLIENT-SIDE to filter the
 *  long-tenor dropdown to strictly-longer tenors so the
 *  ``short_tenor != long_tenor`` rule is enforced at the input
 *  layer (the backend re-validates on every request). */
export function tenorToYears(tenor: string): number {
  const m = /^(\d+(?:\.\d+)?)\s*([WMY])$/i.exec(tenor.trim());
  if (!m) return NaN;
  const n = parseFloat(m[1]);
  const unit = m[2].toUpperCase();
  if (unit === 'W') return n / 52;
  if (unit === 'M') return n / 12;
  return n;
}

/** Short label for a tenor pair, e.g. ('2Y','10Y') → '2s10s'.  Pure-
 *  year pairs strip the 'Y'; sub-year shorts retain the unit. */
export function spreadShortLabel(shortTenor: string, longTenor: string): string {
  const pureYear =
    /^\d+Y$/i.test(shortTenor) && /^\d+Y$/i.test(longTenor);
  if (pureYear) {
    return `${shortTenor.replace(/Y$/i, '')}s${longTenor.replace(/Y$/i, '')}s`;
  }
  return `${shortTenor}/${longTenor}`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the
 *  compact view's footer + the extended view's methodology card.
 *  Sovereign curves can drift from OIS curves on technicals (FRA-OIS,
 *  asset-swap basis, repo specialness), so the canonical cross-check
 *  is swap_spread for the sovereign-vs-OIS gap.  Mockup-faithful
 *  one-liner.
 *
 *  TODO(PR10): when the backend ships ``current_metrics.methodology_label``
 *  (the sub-domain hasn't caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds / inflation_swaps already carry it), switch the
 *  methodology card's "Disclosure" row to source from the wire.  One-line
 *  edit. */
export const SOVEREIGN_CURVE_SPREAD_COMPACT_CAVEAT =
  'Sovereign curve; cross-check swap_spread for sovereign-vs-OIS gap.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCurveSpreadArgs {
  curveFamily: string;
  shortTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseCurveSpreadResult {
  data: CurveSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS curve-
 *  spread sibling hook shape so the per-tool surfaces look the same
 *  shape file-for-file. */
export function useCurveSpread(
  args: UseCurveSpreadArgs,
): UseCurveSpreadResult {
  const [data, setData] = useState<CurveSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: SpreadDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor || undefined,
    long_tenor: args.longTenor || undefined,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
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
    fetchDetailSpread(params)
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
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Sign-convention captions.  POSITIVE spread = STEEPER curve (long > short);
// NEGATIVE = INVERTED.  Surfaced in the compact KPI primary cell + the
// extended top-right cards.
// ---------------------------------------------------------------------------

export function shapeRegimeCaption(
  spreadBps: number | null | undefined,
): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  if (spreadBps > 0) return 'Steeper';
  if (spreadBps < 0) return 'Inverted';
  return 'Flat';
}

/** Z-score caption combining stretch regime + shape direction.  Positive z
 *  = spread elevated above its trailing mean → curve STEEPER than norm.
 *  Negative z = spread below trailing mean → curve FLATTER (or more
 *  inverted) than norm. */
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
// Wire-derived helpers — percentile / 252d range / observation_count /
// trailing changes are computed CLIENT-SIDE from time_series because the
// sovereign curve_spread Output is leaner than its linker / cross_market
// siblings.
// ---------------------------------------------------------------------------

/** Pull the canonical bps spread series from the wire payload.  Prefers the
 *  canonical TimeSeries field ``time_series_spread.rows`` when present
 *  (legacy-TimeSeries cleanup landed it), falling back to the wire-frozen
 *  ``time_series`` row shape otherwise.  Returns rows in chronological
 *  order with null observations dropped. */
function spreadValues(data: CurveSpreadOutput): number[] {
  const canonical = data.time_series_spread?.rows;
  if (canonical && canonical.length > 0) {
    return canonical
      .map((r) => r.value)
      .filter((v): v is number => v != null && !Number.isNaN(v));
  }
  return (data.time_series ?? [])
    .map((r) => r.spread_bps)
    .filter((v): v is number => v != null && !Number.isNaN(v));
}

function trailing252(values: number[]): number[] {
  return values.length <= 252 ? values : values.slice(values.length - 252);
}

/** Client-side percentile of the latest value within the trailing 252d
 *  window (0–100).  Matches the desk-canonical "where in the past year is
 *  today's value" framing (mockup Extended.png shows "74th"). */
export function trailingPercentile252d(
  data: CurveSpreadOutput,
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

export function trailingHigh252d(data: CurveSpreadOutput): number | null {
  const window = trailing252(spreadValues(data));
  return window.length > 0 ? Math.max(...window) : null;
}

export function trailingLow252d(data: CurveSpreadOutput): number | null {
  const window = trailing252(spreadValues(data));
  return window.length > 0 ? Math.min(...window) : null;
}

export function observationCount(data: CurveSpreadOutput): number {
  return spreadValues(data).length;
}

function changeOverRows(
  data: CurveSpreadOutput,
  rowOffset: number,
): number | null {
  const values = spreadValues(data);
  if (values.length <= rowOffset) return null;
  const latest = values[values.length - 1];
  const past = values[values.length - 1 - rowOffset];
  return latest - past;
}

/** 5-trading-day change in bps (mockup Extended.png "5D"). */
export function change5dBps(data: CurveSpreadOutput): number | null {
  return changeOverRows(data, 5);
}

/** 1-month change in bps (~21 trading days, mockup "1M"). */
export function change1mBps(data: CurveSpreadOutput): number | null {
  return changeOverRows(data, 21);
}

/** 3-month change in bps (~63 trading days). */
export function change3mBps(data: CurveSpreadOutput): number | null {
  return changeOverRows(data, 63);
}

/** 12-month change in bps (~252 trading days). */
export function change12mBps(data: CurveSpreadOutput): number | null {
  return changeOverRows(data, 252);
}

// ---------------------------------------------------------------------------
// Chart-point helpers.  The shell's chart layer takes
// ``{ date: string, value: number }`` rows.  Prefer the canonical
// TimeSeries (BPS); fall back to the wire-frozen row shape.
// ---------------------------------------------------------------------------

export interface SpreadChartRow {
  date: string;
  value: number | null;
}

export function spreadChartRows(data: CurveSpreadOutput): SpreadChartRow[] {
  const canonical = data.time_series_spread?.rows;
  if (canonical && canonical.length > 0) {
    return canonical.map((r) => ({ date: r.date, value: r.value }));
  }
  return (data.time_series ?? []).map((r) => ({
    date: r.date,
    value: r.spread_bps ?? null,
  }));
}

// Sanity bounds for sovereign curve spreads (bps).  Across the playbook
// universe sovereign 2s10s / 5s30s spreads cluster within ±300 bps;
// stress excursions to ±500 bps remain plausible.  Anything well outside
// is almost certainly a generic-ticker roll artifact on one endpoint.
const SOVEREIGN_SPREAD_SANITY_MIN_BPS = -600;
const SOVEREIGN_SPREAD_SANITY_MAX_BPS = 600;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<SpreadChartRow>,
): Array<SpreadChartRow> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < SOVEREIGN_SPREAD_SANITY_MIN_BPS
        || r.value > SOVEREIGN_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (<label>) — signed bps, primary emphasis; the curve-
 *                          shape value.  Caption shows
 *                          "Steeper"/"Inverted"/"Flat" + percent-of-
 *                          short equivalent.
 *    2. 1D CHANGE        — signed bps, toneForChange.
 *    3. Z-SCORE (252D)   — signed value + regime+direction caption
 *                          (e.g. "Elevated Steeper" at z=+1.48).
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: CurveSpreadOutput,
  shortTenor: string,
  longTenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const label = spreadShortLabel(shortTenor, longTenor) || cm.spread_label;
  // Percent-of-short subtext (mockup: "(0.42%)" under +42.0bp at short=2Y).
  const short = cm.short_tenor_yield;
  const spreadPctOfShort =
    cm.current_spread_bps != null && short != null && short !== 0
      ? `(${(cm.current_spread_bps / (short * 100)).toFixed(2)}%)`
      : undefined;
  const dailyPctOfShort =
    cm.daily_change_bps != null && short != null && short !== 0
      ? `(${signedFixed(cm.daily_change_bps / (short * 100), 2)}%)`
      : undefined;
  return [
    {
      label: `SPREAD (${label})`,
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: shapeRegimeCaption(cm.current_spread_bps),
      subtext: spreadPctOfShort,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: dailyPctOfShort,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreCaptionForSpread(cm.current_z_score),
    },
  ];
}

/** Extended view's headline KPI strip (mockups/Extended.png).  Spread +
 *  1d + 5d + 1m + 252d high/low + percentile + z + observations.
 *  Trailing changes / percentile / high / low / observation count are
 *  computed CLIENT-SIDE from ``time_series`` because the sovereign
 *  curve_spread Output is leaner than the linker / cross_market
 *  siblings (no wire fields for these). */
export function extendedKPIs(
  data: CurveSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const pct = trailingPercentile252d(data);
  const hi = trailingHigh252d(data);
  const lo = trailingLow252d(data);
  const obs = observationCount(data);
  const c5d = change5dBps(data);
  const c1m = change1mBps(data);
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
      label: '5D',
      value: signedFixed(c5d, 1),
      unit: 'bp',
      tone: toneForChange(c5d),
    },
    {
      label: '1M',
      value: signedFixed(c1m, 1),
      unit: 'bp',
      tone: toneForChange(c1m),
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

/** Two-leg decomposition row — short + long endpoint yields (PERCENT) so
 *  the desk can audit ``long − short`` on the same screen.  Mockup
 *  Extended.png shows "TWO-LEG SOVEREIGN DECOMPOSITION" with short
 *  (2Y UST) = 4.27% and long (10Y UST) = 4.69%. */
export function decompositionKPIs(
  data: CurveSpreadOutput,
  shortTenor: string,
  longTenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const meta = sovereignFamilyFor(cm.curve_family);
  const label = meta?.shortLabel ?? cm.curve_family;
  return [
    {
      label: `SHORT (${shortTenor} ${label})`,
      value: signedFixed(cm.short_tenor_yield, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG (${longTenor} ${label})`,
      value: signedFixed(cm.long_tenor_yield, 3),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference bands — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

export function buildReferenceBands(
  data: CurveSpreadOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseSpreadSeries(spreadChartRows(data));
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
// Stretch context — populates the extended view's side card.
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: CurveSpreadOutput,
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
  const meta = sovereignFamilyFor(curveFamily);
  const family = meta ? meta.longLabel : curveFamily;
  const cavLine =
    ' Sovereign curve shape; cross-check the swap_spread tool to isolate the sovereign-vs-OIS basis (FRA-OIS, asset-swap, repo specialness).';
  if (regime === 'Extreme') {
    return (
      `${family} curve is extreme ${direction} versus its trailing-year mean — `
      + `sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + cavLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} curve is elevated ${direction} versus trailing-year history.`
      + cavLine
    );
  }
  return (
    `${family} curve spread is within its trailing-year norm; `
    + 'no extreme steepening or flattening stretch.'
  );
}

// ---------------------------------------------------------------------------
// Methodology card rows + reference chips
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + request context.
 *  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty disclosure should
 *  flow from ``current_metrics.methodology_label``; the sovereign
 *  curve_spread backend Output currently does NOT carry that field (the
 *  sub-domain hasn't caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds / inflation_swaps already do).  Until the backend
 *  ships it, the "Disclosure" row sources from the per-tool canonical caveat
 *  below (one-line edit to switch when the wire lands the field). */
export function buildMethodologyRows(
  data: CurveSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
  shortTenor: string,
  longTenor: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = sovereignFamilyFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = (long_yield − short_yield) × 100, single curve `
        + `(${cm.curve_family}); raw yield space — no basis subtraction, `
        + 'no convexity adjustment, no fitted curve',
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = STEEPER (long > short) · NEGATIVE = INVERTED',
    },
    {
      label: 'Valid tenor pair',
      value: `long tenor (${longTenor}) > short tenor (${shortTenor}) — enforced at the input + compute layers (short_tenor != long_tenor Pydantic validator)`,
    },
    {
      label: 'Pair',
      value: `${shortTenor} / ${longTenor} (${cm.spread_label})`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (Bloomberg mid-yield, both endpoint series)`,
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
      label: 'Sovereign curve',
      value: meta
        ? `${meta.shortLabel} · ${meta.longLabel} (${cm.curve_family})`
        : cm.curve_family,
    },
    {
      label: 'Units',
      value: 'Spread + changes + 252d range in BPS; per-leg endpoint yields in PERCENT.',
    },
    {
      label: 'Same-curve invariant',
      value: 'Both legs share curve_family — cross-curve sovereign spreads compose on calculate_cross_market_spread_tool (e.g. BTP-Bund 10Y).',
    },
    {
      label: 'Disclosure',
      value: SOVEREIGN_CURVE_SPREAD_COMPACT_CAVEAT,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.5' },
    { label: 'Fabozzi Bond Markets' },
    { label: 'Bloomberg YLD_YTM_MID' },
  ];
}
