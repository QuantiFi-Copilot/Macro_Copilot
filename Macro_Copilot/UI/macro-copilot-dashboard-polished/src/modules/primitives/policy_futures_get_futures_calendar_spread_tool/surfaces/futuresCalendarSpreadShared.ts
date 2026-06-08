// ============================================================================
// futuresCalendarSpreadShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``policy_futures_get_futures_calendar_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that a STIR strip-slot
// calendar spread is a 2-point spread on a SINGLE policy-futures
// curve_family (e.g. SOFR_FUT SFR1-SFR2, EUR_SHORT_RATE_FUT ER1-ER4), that
// the underlying futures quote 100-minus-rate (inverse_priced) for the V1
// universe (SFR / ER / SFI), that the desk-recognised slope quantity lives
// on the IMPLIED-RATE axis in PERCENT POINTS on the wire (the policy-
// futures sub-domain stays in PERCENT POINTS on implied-rate-derived
// objects; the sovereign / OIS curve-spread *_bps convention does NOT
// apply here), and that the desk-canonical display direction is
// BACK-MINUS-FRONT in bps (sign-flipped from the wire's FRONT-MINUS-BACK
// convention so a POSITIVE display value reads as STEEPER policy path /
// back-leg HIGHER implied rate).  The shared shells do not.
//
// Sign convention (wire-frozen):
//   spread_implied_rate_pct = rate_short_leg − rate_long_leg
//                           = rate_front      − rate_back
// where rate_* is the per-leg implied rate in PERCENT (derived from the
// leg's raw_price via the per-strip inverse_pricing flag).  POSITIVE wire
// ⇒ INVERTED strip (front rate above back rate).  We FLIP the sign at the
// display boundary so the desk-recognised "back minus front in bps"
// convention surfaces with the mockup-faithful interpretation: positive
// display bps = back rate HIGHER than front = steeper policy path.  The
// helper ``wirePctToDisplayBps`` is the single place this flip lives.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data per
// rendering_density.md §1.1 (both views consume the same typed-detail
// endpoint; the compact view just renders less of it).  KPI builders +
// formatting + tone live here in ONE place to prevent drift across
// surfaces.
//
// Methodology disclosure: this primitive ships ``methodology_disclosure``
// on the wire (P5 + ADR 0013, composed at compute() time).  The extended
// methodology card sources its "Disclosure" row from that field — NEVER
// a hardcoded TS literal — so YAML edits + per-curve_family regime
// labels flow to runtime.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesCalendar,
  type FuturesCalendarSpreadDetailParams,
} from '@/services/ratesApi';
import type { FuturesCalendarSpreadOutput } from '@/types/rates';
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
// Policy-futures curve-family metadata.  The V1 universe is three families
// (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT); this registry mirrors the
// sibling policy_futures_get_futures_butterfly_simple_tool's CURVE_REGISTRY
// so flag + label + stem behave identically across the two surfaces.
// ---------------------------------------------------------------------------

export interface PolicyFuturesCurveMeta {
  /** Curve family code (e.g. 'SOFR_FUT'). */
  family: string;
  /** Country flag emoji. */
  flag: string;
  /** Short market label for the identity chip (e.g. 'SOFR'). */
  shortLabel: string;
  /** Long human-facing market name (e.g. 'US Fed SOFR strip'). */
  longLabel: string;
  /** Underlying short-rate object — 'RFR' (SOFR / SONIA compounded
   *  daily) or 'IBOR' (3M Euribor unsecured term). */
  regime: 'RFR' | 'IBOR';
  /** Master-stem prefix for the strip slots (e.g. 'SFR' → 'SFR1'). */
  stripStemPrefix: string;
}

const CURVE_REGISTRY: Record<string, PolicyFuturesCurveMeta> = {
  SOFR_FUT: {
    family: 'SOFR_FUT',
    flag: '🇺🇸',
    shortLabel: 'SOFR',
    longLabel: 'US Fed SOFR strip',
    regime: 'RFR',
    stripStemPrefix: 'SFR',
  },
  EUR_SHORT_RATE_FUT: {
    family: 'EUR_SHORT_RATE_FUT',
    flag: '🇪🇺',
    shortLabel: 'Euribor',
    longLabel: 'ECB Euribor strip',
    regime: 'IBOR',
    stripStemPrefix: 'ER',
  },
  SONIA_FUT: {
    family: 'SONIA_FUT',
    flag: '🇬🇧',
    shortLabel: 'SONIA',
    longLabel: 'BOE SONIA strip',
    regime: 'RFR',
    stripStemPrefix: 'SFI',
  },
};

export function curveMetaFor(curveFamily: string): PolicyFuturesCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** Whites / Reds / Greens classification per the V1 universe (whites = 1-4,
 *  reds = 5-8, greens = 9-12).  Mockup-faithful tag surfaced on the
 *  identity row. */
export function stripSegmentLabel(
  stripPosition: number,
): 'WHITES' | 'REDS' | 'GREENS' {
  if (stripPosition <= 4) return 'WHITES';
  if (stripPosition <= 8) return 'REDS';
  return 'GREENS';
}

/** Combined calendar-pair label, e.g. (SOFR_FUT, 1, 3) → "1-3" or
 *  (EUR_SHORT_RATE_FUT, 1, 4) → "1-4".  Used in the identity row + the
 *  Monitor widget — short / long are the strip-position INTEGERS, not
 *  the master-stem stems. */
export function calendarPairLabel(
  shortPos: number,
  longPos: number,
): string {
  return `${shortPos}-${longPos}`;
}

/** Descriptive pack label (e.g. SOFR Whites for 1-3, mixed Whites/Reds
 *  for 1-5).  Mockup-faithful single-segment chip ("WHITES") when both
 *  legs share a pack; "Whites/Reds" when they straddle. */
export function calendarPackLabel(
  curveFamily: string,
  shortPos: number,
  longPos: number,
): string {
  const meta = curveMetaFor(curveFamily);
  const market = meta?.shortLabel ?? curveFamily;
  const segs = new Set<string>([
    stripSegmentLabel(shortPos),
    stripSegmentLabel(longPos),
  ]);
  if (segs.size === 1) {
    const seg = [...segs][0];
    return `${market} ${seg.charAt(0) + seg.slice(1).toLowerCase()}`;
  }
  return `${market} Mixed pack`;
}

/** Strip-position options for the Monitor widget + extended controls — V1
 *  universe of 1..8 (whites + reds; greens are not currently ingested). */
export const STRIP_POSITION_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '1', label: '1 — Front' },
  { value: '2', label: '2 — 2nd' },
  { value: '3', label: '3 — 3rd' },
  { value: '4', label: '4 — 4th (Whites tail)' },
  { value: '5', label: '5 — 1st Red' },
  { value: '6', label: '6 — 2nd Red' },
  { value: '7', label: '7 — 3rd Red' },
  { value: '8', label: '8 — 4th Red (Reds tail)' },
];

/** Curve-family options for the Monitor widget + extended controls. */
export const POLICY_FUTURES_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CURVE_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Pre-registered calendar pairs per curve_family — desk-canonical
 *  (short, long) tuples on the ingested strip grid.  Surfacing only
 *  registered pairs makes invalid orderings unreachable via the single
 *  "Pair" dropdown (mirrors the OIS curve-spread + butterfly precedent).
 *  Mockup default: SFR 1-3 (front-pack slope). */
export interface CalendarPair {
  short: number;
  long: number;
  label: string; // e.g. "1-3"
}

export const CALENDAR_PAIRS_BY_CURVE: Record<
  string,
  ReadonlyArray<CalendarPair>
> = {
  SOFR_FUT: [
    { short: 1, long: 2, label: '1-2' },
    { short: 1, long: 3, label: '1-3' },
    { short: 1, long: 4, label: '1-4 (Whites)' },
    { short: 2, long: 4, label: '2-4' },
    { short: 1, long: 5, label: '1-5 (Whites/Reds)' },
    { short: 1, long: 8, label: '1-8 (Whites/Reds tail)' },
  ],
  EUR_SHORT_RATE_FUT: [
    { short: 1, long: 2, label: '1-2' },
    { short: 1, long: 3, label: '1-3' },
    { short: 1, long: 4, label: '1-4 (Whites)' },
    { short: 1, long: 8, label: '1-8 (Whites/Reds tail)' },
  ],
  SONIA_FUT: [
    { short: 1, long: 2, label: '1-2' },
    { short: 1, long: 3, label: '1-3' },
    { short: 1, long: 4, label: '1-4 (Whites)' },
  ],
};

/** Desk-canonical caveat string for the compact view footer.  Encodes the
 *  load-bearing facts a reader must remember: the underlying STIR
 *  contract quote convention is 100-minus-rate (so UP price = DOWN
 *  implied rate = dovish), the spread is reported in BPS on the
 *  IMPLIED-RATE axis in the back-minus-front display convention, and a
 *  POSITIVE display value = BACK rate HIGHER = steeper policy path. */
export const POLICY_FUTURES_CALENDAR_COMPACT_CAVEAT =
  'Calendar spread on implied rates; underlying contracts quote inverse.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UsePolicyFuturesCalendarArgs {
  curveFamily: string;
  stripPositionShort: number;
  stripPositionLong: number;
  lookbackDays?: number;
  asOfDate?: string;
  fieldName?: string;
}

export interface UsePolicyFuturesCalendarResult {
  data: FuturesCalendarSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS curve-
 *  spread + sibling butterfly hook shape so the per-tool surfaces look
 *  the same file-for-file. */
export function usePolicyFuturesCalendar(
  args: UsePolicyFuturesCalendarArgs,
): UsePolicyFuturesCalendarResult {
  const [data, setData] = useState<FuturesCalendarSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: FuturesCalendarSpreadDetailParams = {
    curve_family: args.curveFamily,
    strip_position_short: args.stripPositionShort,
    strip_position_long: args.stripPositionLong,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.curveFamily
      || !args.stripPositionShort
      || !args.stripPositionLong
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    if (!(args.stripPositionShort < args.stripPositionLong)) {
      // Schema layer rejects unordered / duplicate pairs; short-circuit
      // here so the fetch doesn't fire and the shell renders the loading
      // skeleton until the user picks a valid pair.
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesCalendar(params)
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
    args.stripPositionShort,
    args.stripPositionLong,
    args.lookbackDays,
    args.asOfDate,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Sign-convention caption.  In the desk-canonical DISPLAY convention
// (back-minus-front in bps), POSITIVE = STEEPER (back rate above front =
// hawkish forward); NEGATIVE = INVERTED / FLATTER (back rate below front
// = dovish forward path).  Surfaced in the compact KPI primary cell.
// ---------------------------------------------------------------------------

export function shapeRegimeCaption(
  spreadBps: number | null | undefined,
): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  if (spreadBps > 0) return 'Back cheap';
  if (spreadBps < 0) return 'Front cheap';
  return 'Flat';
}

/** Z-score caption combining stretch regime + slope direction.  Z is
 *  computed on the WIRE convention (front − back).  We flip the
 *  direction word when surfacing the regime label so a desk reader
 *  cannot misread the sign — in the desk convention POSITIVE z on the
 *  flipped display series = back-cheaper-than-norm (steeper); NEGATIVE z
 *  = back-richer-than-norm (flatter). */
export function zScoreCaptionForSpread(
  z: number | null | undefined,
): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z)) return '—';
  if (regime === 'Normal') return 'Neutral';
  // Wire z is on the FRONT − BACK series.  Flip the direction so the
  // caption matches the display convention (back − front, in bps):
  //   wire z POSITIVE  ⇒ front rate elevated above back ⇒ display
  //                       value is NEGATIVE ⇒ "Flatter".
  //   wire z NEGATIVE  ⇒ back rate elevated above front ⇒ display value
  //                       is POSITIVE ⇒ "Steeper".
  const direction = z > 0 ? 'Flatter' : 'Steeper';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// Unit + sign conversion — wire PERCENT POINTS (front − back) → display
// bps (back − front).
//
// Wire ``spread_implied_rate_pct`` is in PERCENT POINTS, signed as
// ``front − back``.  Display convention is BPS, signed ``back − front``
// so a POSITIVE display value = back rate above front rate = steeper
// policy path (mockup-faithful).  The flip lives here in ONE place so
// the three surfaces cannot drift.
// ---------------------------------------------------------------------------

export function wirePctToDisplayBps(
  pct: number | null | undefined,
): number | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  return -pct * 100;
}

/** Z-score flips the same way as the level — wire z is on the FRONT −
 *  BACK series, display z is on the BACK − FRONT series.  Use this
 *  helper so the regime tone (toneForZScore) reads off the display-
 *  oriented sign. */
export function flipWireZScore(
  z: number | null | undefined,
): number | null {
  if (z == null || !Number.isFinite(z)) return null;
  return -z;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *
 *    1. SPREAD (BPS)     — signed bps (back − front display convention),
 *                          primary emphasis, neutral-tone (sign IS the
 *                          info — let the caption carry the direction);
 *                          caption Back cheap / Front cheap / Flat.
 *    2. 1D CHANGE (BPS)  — signed bps (display convention), tone-
 *                          coloured by toneForChange against the
 *                          display sign.
 *    3. Z-SCORE (252D)   — display-oriented z (flipped from wire),
 *                          regime + direction caption (Elevated
 *                          Steeper, Extreme Flatter, etc.).
 *
 *  THESIS Q3 documents why these vs alternatives.  Matches the shell-
 *  standard 3-KPI density; the mockup's denser layout is preserved in
 *  the extended view (per the Option-(c) precedent — see THESIS.md). */
export function compactKPIs(
  data: FuturesCalendarSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const spreadBps = wirePctToDisplayBps(cm.spread_implied_rate_pct);
  const dailyBps = wirePctToDisplayBps(cm.daily_change_spread_implied_rate_pct);
  const displayZ = flipWireZScore(cm.z_score_spread_implied_rate);
  return [
    {
      label: 'SPREAD (BPS)',
      value: signedFixed(spreadBps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: shapeRegimeCaption(spreadBps),
    },
    {
      label: '1D CHANGE (BPS)',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(displayZ, 2),
      tone: toneForZScore(displayZ),
      caption: zScoreCaptionForSpread(cm.z_score_spread_implied_rate),
    },
  ];
}

/** The extended view's headline KPI strip (mockups/Extended.png).  The
 *  backend wire is in PERCENT POINTS for the spread series + per-leg
 *  rates in PERCENT; the headline cells display in bps for spread +
 *  daily change to match the desk-recognised quote-size unit and in
 *  PERCENT for the per-leg implied rates (the natural unit for a STIR
 *  implied rate). */
export function extendedKPIs(
  data: FuturesCalendarSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const spreadBps = wirePctToDisplayBps(cm.spread_implied_rate_pct);
  const dailyBps = wirePctToDisplayBps(cm.daily_change_spread_implied_rate_pct);
  // Wire 252d high / low are also FRONT − BACK; flip & invert order so
  // the display "high" is the MAX value on the back-minus-front display
  // series (i.e. the WIRE's lowest), and the display "low" is the WIRE's
  // highest.  Both come out signed in display bps.
  const highBpsDisplay = wirePctToDisplayBps(cm.low_252d_spread_implied_rate_pct);
  const lowBpsDisplay = wirePctToDisplayBps(cm.high_252d_spread_implied_rate_pct);
  const displayZ = flipWireZScore(cm.z_score_spread_implied_rate);
  // Wire ``percentile_252d`` is the rank of the wire spread within its
  // trailing window.  Under the FRONT − BACK convention the wire's
  // higher percentile means a more-inverted strip; on the display
  // (BACK − FRONT) convention it's the steeper end.  Flip the
  // percentile so display "78th" = "78th percentile of the back-
  // minus-front display series" (mockup-faithful).
  const displayPercentile =
    cm.percentile_252d != null && Number.isFinite(cm.percentile_252d)
      ? 100 - cm.percentile_252d
      : null;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(spreadBps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: shapeRegimeCaption(spreadBps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(displayZ, 2),
      tone: toneForZScore(displayZ),
      caption: zScoreCaptionForSpread(cm.z_score_spread_implied_rate),
    },
    {
      label: 'PERCENTILE (252D)',
      value:
        displayPercentile != null
          ? `${Math.round(displayPercentile)}`
          : '—',
      unit: 'th',
      caption: bucketForPercentile(displayPercentile),
    },
    {
      label: '252D HIGH',
      value: signedFixed(highBpsDisplay, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(lowBpsDisplay, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
  ];
}

/** Decomposition row — two per-leg implied rates (PERCENT, derived
 *  client-side from the raw_price_spread + wire spread relationship)
 *  surfaced via the wire's per-leg master stems + current-front
 *  underlying contracts so the desk can audit
 *  ``rate_back − rate_front`` on the same screen.  The backend Output
 *  does NOT carry per-leg implied rates directly (only the snapshot
 *  spread + raw_price_spread + the underlying-contract block), so the
 *  decomposition KPI row labels carry the contracts but the values are
 *  presented as the RAW PRICES (the wire DOES carry the underlying
 *  contract identifiers on each leg via contract_code_* +
 *  underlying_contract_code_*; the raw_price_spread itself is the
 *  derivable quantity).  Mockup-faithful identification: WING SHORT
 *  carries (SFR1, SFRM26-style codes), LONG carries (SFR2, SFRU26
 *  codes). */
export function decompositionKPIs(
  data: FuturesCalendarSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `SHORT LEG (${cm.contract_code_short})`,
      value: cm.underlying_contract_code_short ?? '—',
      unit: '',
      tone: 'neutral',
      subtext: cm.expiry_date_short
        ? `expiry ${cm.expiry_date_short}`
        : undefined,
    },
    {
      label: `LONG LEG (${cm.contract_code_long})`,
      value: cm.underlying_contract_code_long ?? '—',
      unit: '',
      tone: 'neutral',
      subtext: cm.expiry_date_long
        ? `expiry ${cm.expiry_date_long}`
        : undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread series in
// DISPLAY bps (back − front).  Computed from the sanitised display
// series so a single bad row doesn't blow the envelope out.  Mirrors
// the OIS curve-spread band-building shape.
// ---------------------------------------------------------------------------

// Sanity bounds on the policy-futures calendar spread series (bps after
// the wire-PERCENT POINTS → display-bps conversion).  STIR calendar
// spreads cluster within a few hundred bps; bound at ±500 bps as a
// defensive frontend safety net matching the OIS curve-spread bounds.
const FUTURES_CAL_SPREAD_SANITY_MIN_BPS = -500;
const FUTURES_CAL_SPREAD_SANITY_MAX_BPS = 500;

/** Sanitise + convert the bespoke calendar-spread time-series rows for
 *  the chart layer.  Input rows are in PERCENT POINTS (wire, signed
 *  front − back); output is in display bps (signed back − front, via
 *  ``wirePctToDisplayBps``).  Outliers clamp to null. */
export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; spread_implied_rate_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => {
    const bps = wirePctToDisplayBps(r.spread_implied_rate_pct);
    return {
      date: r.date,
      value:
        bps == null
        || bps < FUTURES_CAL_SPREAD_SANITY_MIN_BPS
        || bps > FUTURES_CAL_SPREAD_SANITY_MAX_BPS
          ? null
          : bps,
    };
  });
}

export function buildReferenceBands(
  data: FuturesCalendarSpreadOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseSpreadSeries(data.time_series ?? []);
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
// Stretch-context builder — built on the DISPLAY-flipped z + percentile
// so the regime narrative reads off the same convention the desk uses.
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: FuturesCalendarSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  const displayZ = flipWireZScore(cm.z_score_spread_implied_rate);
  const displayPercentile =
    cm.percentile_252d != null && Number.isFinite(cm.percentile_252d)
      ? 100 - cm.percentile_252d
      : null;
  if (displayZ == null && displayPercentile == null) return undefined;

  const regime = regimeForZScore(displayZ);
  const bucket = bucketForPercentile(displayPercentile);
  const interp = interpretationFor(
    regime,
    displayZ,
    bucket,
    cm.curve_family,
    cm.spread_label,
  );

  return {
    percentile:
      displayPercentile != null
        ? { value: displayPercentile, bucket }
        : undefined,
    zScoreRegime:
      displayZ != null
        ? {
            value: displayZ,
            regime,
            bands: { amber: 1.5, coral: 2.0 },
          }
        : undefined,
    interpretation: interp,
  };
}

function interpretationFor(
  regime: 'Normal' | 'Elevated' | 'Extreme',
  displayZ: number | null | undefined,
  bucket: 'Low' | 'Normal' | 'High',
  curveFamily: string,
  spreadLabel: string,
): string {
  if (displayZ == null) return 'Insufficient data to characterise stretch.';
  const direction = displayZ > 0 ? 'steeper' : 'flatter';
  const meta = curveMetaFor(curveFamily);
  const family = meta
    ? `${meta.shortLabel} ${spreadLabel}`
    : `${curveFamily} ${spreadLabel}`;
  const policyLine =
    ' Implied-rate slope on the STIR strip — read the move as how the market is pricing the SHAPE of near-term policy expectations, not as a forecast of realised central-bank meeting outcomes.';
  if (regime === 'Extreme') {
    return (
      `${family} calendar spread is extreme ${direction} versus its trailing-year mean — `
      + `back-leg implied rate is ${direction === 'steeper' ? 'high' : 'low'} relative to the front leg.  `
      + `Sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + policyLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} calendar spread is elevated ${direction} versus trailing-year history — `
      + `back-leg implied rate is ${direction === 'steeper' ? 'higher' : 'lower'} than the front on a normalised basis.`
      + policyLine
    );
  }
  return (
    `${family} calendar spread is within its trailing-year norm; `
    + 'no extreme steepening or flattening stretch.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 + PR10 / P5 contract the wire-
 *  honesty disclosure flows from ``methodology_disclosure`` on the
 *  response (composed at compute() time, sourced from config.yaml +
 *  per-curve_family regime label) — NEVER a hardcoded TS literal.  The
 *  card's "Disclosure" row consumes that field verbatim so YAML edits +
 *  per-curve_family regime labels flow to runtime. */
export function buildMethodologyRows(
  data: FuturesCalendarSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = curveMetaFor(cm.curve_family);
  const inversePricingRule = cm.inverse_priced
    ? 'Inverse-priced — implied_rate_pct = 100 − raw_price per leg (SFR / ER / SFI).'
    : 'Direct-priced — implied_rate_pct = raw_price per leg.';
  return [
    {
      label: 'Construction',
      value:
        'WIRE: spread_implied_rate_pct = rate_short − rate_long = rate_front − rate_back, '
        + 'per-leg implied rates in PERCENT (rate_* derived from raw_price via the per-strip inverse-pricing flag); '
        + 'wire signed in PERCENT POINTS. DISPLAY: bps in the BACK − FRONT convention (flipped from wire) so positive bps = back rate HIGHER than front (steeper policy path).',
    },
    {
      label: 'Sign convention (display)',
      value: 'POSITIVE = STEEPER (back rate above front) · NEGATIVE = FLATTER / INVERTED (front rate above back)',
    },
    {
      label: 'Pair',
      value: `${cm.contract_code_short} / ${cm.contract_code_long} (${cm.spread_label})`,
    },
    {
      label: 'Curve family',
      value: meta
        ? `${meta.shortLabel} · ${meta.longLabel} (${cm.curve_family})`
        : cm.curve_family,
    },
    {
      label: 'Underlying contracts',
      value: [
        cm.underlying_contract_code_short
          ? `${cm.contract_code_short} → ${cm.underlying_contract_code_short}`
          : null,
        cm.underlying_contract_code_long
          ? `${cm.contract_code_long} → ${cm.underlying_contract_code_long}`
          : null,
      ]
        .filter((s): s is string => s != null)
        .join(' · ') || '—',
    },
    {
      label: 'Quote convention',
      value: `100-minus-rate; UP price = DOWN implied rate (dovish). ${inversePricingRule}`,
    },
    {
      label: 'Short-rate regime',
      value:
        cm.short_rate_regime === 'RFR'
          ? 'RFR — compounded daily risk-free rate (SOFR / SONIA).'
          : cm.short_rate_regime === 'IBOR'
            ? 'IBOR — unsecured 3M term IBOR (Euribor).'
            : cm.short_rate_regime,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (price observation on each strip slot)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the IMPLIED-RATE SPREAD series (YAML-locked — no input-layer override on this primitive). Display z is the wire z sign-flipped so the regime tone tracks the back-minus-front convention.`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / mid / percentile, PERCENT POINTS on the wire; sign-flipped + rendered in bps in the display).',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days).`,
    },
    {
      label: 'Observations',
      value: `${cm.observation_count} aligned trading days (intersection of both legs).`,
    },
    {
      label: 'Disclosure',
      value: data.methodology_disclosure || '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0013 (policy_futures domain)' },
    { label: 'CME SOFR futures contract spec' },
    { label: 'ICE Euribor / SONIA futures contract spec' },
    { label: 'Catalog tool-20 (futures_calendar_spread)' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
