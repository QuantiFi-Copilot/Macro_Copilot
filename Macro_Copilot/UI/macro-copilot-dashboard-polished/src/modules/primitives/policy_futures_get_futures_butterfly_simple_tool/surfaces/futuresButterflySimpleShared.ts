// ============================================================================
// futuresButterflySimpleShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``policy_futures_get_futures_butterfly_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that a STIR strip
// butterfly is a 3-point curvature on a SINGLE policy-futures
// curve_family (e.g. SOFR_FUT SFR1-SFR2-SFR3), that the underlying
// futures quote 100-minus-rate (inverse_priced) for the V1 universe
// (SFR / ER / SFI), that the desk-recognised butterfly quantity lives
// on the IMPLIED-RATE axis in PERCENT POINTS (NOT bps — the policy-
// futures sub-domain stays in PERCENT POINTS on implied-rate-derived
// objects; the sovereign / OIS butterfly *_bps convention does NOT
// apply here), and that the FIXED 50-50 simple-butterfly weighting is
// the per-strip read.  The shared shells do not.
//
// Sign convention (wire-frozen):
//   butterfly_value_pct = rate_body − 0.5 * (rate_wing_short + rate_wing_long)
// where rate_* is the per-leg implied rate in PERCENT (derived from
// the leg's raw_price via the per-strip inverse_pricing flag).  POSITIVE
// ⇒ belly CHEAP (body rate above wing average); NEGATIVE ⇒ belly RICH.
// The compact KPI strip + the extended methodology card surface this
// sign convention verbatim so a desk reader cannot misread the wire.
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
  fetchDetailPolicyFuturesButterfly,
  type FuturesButterflySimpleDetailParams,
} from '@/services/ratesApi';
import type { FuturesButterflySimpleOutput } from '@/types/rates';
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
// sibling policy_futures_get_futures_price_level_tool's CURVE_REGISTRY so
// flag + label + stem behave identically across the two surfaces.
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
  /** Underlying short-rate object — 'RFR' (SOFR / SONIA compounded daily)
   *  or 'IBOR' (3M Euribor unsecured term).  Surfaced inline on the
   *  methodology card. */
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

/** Build the master-stem label for a (curve_family, strip_position) pair —
 *  e.g. (SOFR_FUT, 2) → 'SFR2'.  Falls back to a generic '<family>#<n>'
 *  shape when the family is unknown. */
export function stripStemLabel(
  curveFamily: string,
  stripPosition: number,
): string {
  const meta = curveMetaFor(curveFamily);
  if (!meta) return `${curveFamily}#${stripPosition}`;
  return `${meta.stripStemPrefix}${stripPosition}`;
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

/** Combined butterfly-triple label, e.g. (SFR1, SFR2, SFR3) → "1-2-3"
 *  or (ER1, ER2, ER4) → "1-2-4".  Used in the identity row per the
 *  mockup.  Falls back to a hyphenated string of raw strip positions
 *  when the master-stem prefix is unknown. */
export function butterflyTripletLabel(
  curveFamily: string,
  wingShort: number,
  body: number,
  wingLong: number,
): string {
  const meta = curveMetaFor(curveFamily);
  if (!meta) return `${wingShort}-${body}-${wingLong}`;
  return `${wingShort}-${body}-${wingLong}`;
}

/** Descriptive pack label (e.g. SOFR Whites for 1-2-3, mixed Whites/Reds
 *  for 1-2-5).  Surfaced as the secondary identity span. */
export function butterflyPackLabel(
  curveFamily: string,
  wingShort: number,
  body: number,
  wingLong: number,
): string {
  const meta = curveMetaFor(curveFamily);
  const market = meta?.shortLabel ?? curveFamily;
  const segments = new Set<string>([
    stripSegmentLabel(wingShort),
    stripSegmentLabel(body),
    stripSegmentLabel(wingLong),
  ]);
  if (segments.size === 1) {
    const seg = [...segments][0];
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

/** Pre-registered butterfly triplets per curve_family — desk-canonical
 *  (wing_short, body, wing_long) tuples on the ingested strip grid.
 *  Surfacing only registered triplets makes invalid orderings unreachable
 *  via the single "Triplet" dropdown (mirrors the OIS butterfly
 *  triplet-dropdown precedent).  Mockup default: SFR 1-2-3 (front-pack
 *  curvature). */
export interface ButterflyTriplet {
  wingShort: number;
  body: number;
  wingLong: number;
  label: string; // e.g. "1-2-3"
}

export const BUTTERFLY_TRIPLETS_BY_CURVE: Record<
  string,
  ReadonlyArray<ButterflyTriplet>
> = {
  SOFR_FUT: [
    { wingShort: 1, body: 2, wingLong: 3, label: '1-2-3' },
    { wingShort: 2, body: 3, wingLong: 4, label: '2-3-4' },
    { wingShort: 1, body: 3, wingLong: 5, label: '1-3-5' },
    { wingShort: 1, body: 4, wingLong: 8, label: '1-4-8 (Whites/Reds)' },
    { wingShort: 1, body: 2, wingLong: 4, label: '1-2-4' },
  ],
  EUR_SHORT_RATE_FUT: [
    { wingShort: 1, body: 2, wingLong: 3, label: '1-2-3' },
    { wingShort: 2, body: 3, wingLong: 4, label: '2-3-4' },
    { wingShort: 1, body: 2, wingLong: 4, label: '1-2-4' },
    { wingShort: 1, body: 4, wingLong: 8, label: '1-4-8 (Whites/Reds)' },
  ],
  SONIA_FUT: [
    { wingShort: 1, body: 2, wingLong: 3, label: '1-2-3' },
    { wingShort: 2, body: 3, wingLong: 4, label: '2-3-4' },
    { wingShort: 1, body: 4, wingLong: 8, label: '1-4-8 (Whites/Reds)' },
  ],
};

/** Desk-canonical caveat string for the compact view footer.  Encodes the
 *  three load-bearing facts a reader must remember: the underlying STIR
 *  contract quote convention is 100-minus-rate (so UP price = DOWN implied
 *  rate = dovish), the butterfly is reported on the IMPLIED-RATE axis in
 *  bps for the headline display (and PERCENT POINTS on the wire — the
 *  display helpers below multiply by 100 to deliver bps), and the sign
 *  convention is body − 0.5 × (wing_short + wing_long) ⇒ POSITIVE = belly
 *  cheap. */
export const POLICY_FUTURES_BUTTERFLY_COMPACT_CAVEAT =
  'Implied-rate bps. Underlying STIR futures quote inverse (100-minus-rate).';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UsePolicyFuturesButterflyArgs {
  curveFamily: string;
  stripPositionWingShort: number;
  stripPositionBody: number;
  stripPositionWingLong: number;
  lookbackDays?: number;
  asOfDate?: string;
  fieldName?: string;
}

export interface UsePolicyFuturesButterflyResult {
  data: FuturesButterflySimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the OIS-butterfly
 *  hook shape so the per-tool surfaces look the same file-for-file. */
export function usePolicyFuturesButterfly(
  args: UsePolicyFuturesButterflyArgs,
): UsePolicyFuturesButterflyResult {
  const [data, setData] = useState<FuturesButterflySimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: FuturesButterflySimpleDetailParams = {
    curve_family: args.curveFamily,
    strip_position_wing_short: args.stripPositionWingShort,
    strip_position_body: args.stripPositionBody,
    strip_position_wing_long: args.stripPositionWingLong,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.curveFamily
      || !args.stripPositionWingShort
      || !args.stripPositionBody
      || !args.stripPositionWingLong
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    if (
      !(args.stripPositionWingShort < args.stripPositionBody
        && args.stripPositionBody < args.stripPositionWingLong)
    ) {
      // Schema layer rejects unordered triples; short-circuit here so the
      // fetch doesn't fire and the shell renders the loading skeleton
      // until the user picks a valid triplet.
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesButterfly(params)
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
    args.stripPositionWingShort,
    args.stripPositionBody,
    args.stripPositionWingLong,
    args.lookbackDays,
    args.asOfDate,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE = belly CHEAP (body rate above wing
// average).  NEGATIVE = belly RICH.  Surfaced in the compact KPI caption +
// the extended fly cell.
// ---------------------------------------------------------------------------

export function bellyRegimeCaption(
  butterflyBps: number | null | undefined,
): string {
  if (butterflyBps == null || Number.isNaN(butterflyBps)) return '—';
  if (butterflyBps > 0) return 'Belly cheap';
  if (butterflyBps < 0) return 'Belly rich';
  return 'Flat';
}

/** Z-score caption combining stretch regime + belly direction.  Extreme
 *  positive z = belly extreme-CHEAP; extreme negative z = belly extreme-
 *  RICH.  Mockup-faithful — at z=+1.84 the compact caption reads
 *  "Elevated Cheap" (matching the OIS butterfly precedent). */
export function zScoreCaptionForButterfly(
  z: number | null | undefined,
): string {
  const regime = regimeForZScore(z);
  if (z == null || Number.isNaN(z) || regime === 'Normal') {
    return regime === 'Normal' ? 'Neutral' : '—';
  }
  const direction = z > 0 ? 'Cheap' : 'Rich';
  return `${regime} ${direction}`;
}

// ---------------------------------------------------------------------------
// Unit conversion — PERCENT POINTS (wire) → bps (display).  The backend
// reports butterfly_value_pct in PERCENT POINTS (the policy-futures sub-
// domain convention; matches implied_rate_pct / spread_implied_rate_pct
// siblings).  The compact + extended headline KPIs display in bps because
// that's the desk-recognised quote-size unit for a 3-strip-slot curvature
// (mockup-faithful: "+8.4 bps" for an SFR 1-2-3 fly).  Multiply by 100 and
// keep the inverse-pricing sign UNCHANGED — implied-rate-axis quantities
// preserve direction (positive bps = body rate above wing average).
// ---------------------------------------------------------------------------

export function pctToBps(
  pct: number | null | undefined,
): number | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  return pct * 100;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *
 *    1. FLY (BPS)       — signed bps (butterfly_value_pct * 100), primary
 *                         emphasis, sign-aware tone (positive = belly cheap
 *                         lean coral; negative = belly rich lean mint);
 *                         caption Belly cheap / Belly rich / Flat.
 *    2. 1D CHANGE       — signed bps (daily_change_butterfly_value_pct *
 *                         100), tone-coloured by toneForChange; subtext
 *                         shows the equivalent percent of body implied rate
 *                         (the mockup: "(+0.23%)" for the SFR 1-2-3 case).
 *    3. Z-SCORE (252D)  — z of the implied-rate butterfly series, regime
 *                         + direction caption (Elevated Cheap, Extreme
 *                         Rich, etc.).
 *
 *  THESIS Q3 documents why these vs alternatives.  This matches the
 *  shell-standard 3-KPI density; the mockup's denser layout is preserved
 *  in the extended view (per the Option-(c) precedent — see THESIS.md). */
export function compactKPIs(
  data: FuturesButterflySimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const flyBps = pctToBps(cm.butterfly_value_pct);
  const dailyBps = pctToBps(cm.daily_change_butterfly_value_pct);
  // Express the bps move as an approximate percent of CURRENT body implied
  // rate so the desk gets a sense of relative size — mockup shows
  // "(+0.23%)" subtext for a +1.9 bp move on a ~0.82% body rate.  Body
  // implied rate is in PERCENT; divide bps by (body_pct * 100) for percent.
  const dailyPctOfBody = bpsRelativeToBody(dailyBps, cm.implied_rate_pct_body);
  return [
    {
      label: 'FLY (BPS)',
      value: signedFixed(flyBps, 1),
      unit: 'bps',
      tone: toneForFly(flyBps),
      emphasis: 'primary',
      caption: bellyRegimeCaption(flyBps),
    },
    {
      label: '1D CHANGE (BPS)',
      value: signedFixed(dailyBps, 1),
      unit: 'bps',
      tone: toneForChange(dailyBps),
      subtext:
        dailyPctOfBody != null
          ? `(${signedFixed(dailyPctOfBody, 2)}%)`
          : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_butterfly, 2),
      tone: toneForZScore(cm.z_score_butterfly),
      caption: zScoreCaptionForButterfly(cm.z_score_butterfly),
    },
  ];
}

/** Belly-direction tone — POSITIVE bps (belly cheap) leans coral; NEGATIVE
 *  bps (belly rich) leans mint per the OIS butterfly tone convention.  The
 *  fly cell carries 'neutral' tone when zero or unavailable. */
function toneForFly(bps: number | null | undefined): KPIDescriptor['tone'] {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'negative' : 'positive';
}

/** Compute bps move as percent of body implied rate (mockup subtext).
 *  bps / (body_pct * 100) → percent.  Returns null when either input is
 *  missing or body is zero. */
function bpsRelativeToBody(
  bps: number | null | undefined,
  bodyPct: number | null | undefined,
): number | null {
  if (
    bps == null
    || bodyPct == null
    || !Number.isFinite(bps)
    || !Number.isFinite(bodyPct)
    || bodyPct === 0
  ) {
    return null;
  }
  return bps / (bodyPct * 100);
}

/** The extended view's headline KPI strip (mockups/Extended.png).  The
 *  backend wire is in PERCENT POINTS for the butterfly series + per-leg
 *  rates in PERCENT; the headline cells display in bps for butterfly +
 *  daily change to match the desk-recognised quote-size unit and in PERCENT
 *  for the per-leg implied rates (the natural unit for a STIR implied
 *  rate). */
export function extendedKPIs(
  data: FuturesButterflySimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const flyBps = pctToBps(cm.butterfly_value_pct);
  const dailyBps = pctToBps(cm.daily_change_butterfly_value_pct);
  const highBps = pctToBps(cm.high_252d_butterfly_value_pct);
  const lowBps = pctToBps(cm.low_252d_butterfly_value_pct);
  return [
    {
      label: 'BUTTERFLY',
      value: signedFixed(flyBps, 1),
      unit: 'bps',
      tone: 'neutral',
      caption: bellyRegimeCaption(flyBps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(dailyBps, 1),
      unit: 'bps',
      tone: toneForChange(dailyBps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_butterfly, 2),
      tone: toneForZScore(cm.z_score_butterfly),
      caption: zScoreCaptionForButterfly(cm.z_score_butterfly),
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
      value: signedFixed(highBps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(lowBps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
  ];
}

/** Decomposition row — three per-leg implied rates (PERCENT) so the desk
 *  can audit ``rate_body − 0.5 * (rate_wing_short + rate_wing_long)`` on
 *  the same screen.  Per-leg labels carry both the strip-slot master stem
 *  (stable) and the current-front underlying contract (rotates at roll)
 *  per the catalog disclosure rule. */
export function decompositionKPIs(
  data: FuturesButterflySimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `WING SHORT (${cm.contract_code_wing_short})`,
      value: signedFixed(cm.implied_rate_pct_wing_short, 3),
      unit: '%',
      tone: 'neutral',
      subtext: cm.underlying_contract_code_wing_short ?? undefined,
    },
    {
      label: `BODY (${cm.contract_code_body})`,
      value: signedFixed(cm.implied_rate_pct_body, 3),
      unit: '%',
      tone: 'neutral',
      subtext: cm.underlying_contract_code_body ?? undefined,
    },
    {
      label: `WING LONG (${cm.contract_code_wing_long})`,
      value: signedFixed(cm.implied_rate_pct_wing_long, 3),
      unit: '%',
      tone: 'neutral',
      subtext: cm.underlying_contract_code_wing_long ?? undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the butterfly series
// (in bps, the display unit).  Computed from the sanitised series so a
// single bad row doesn't blow the envelope out.  Mirrors the OIS butterfly
// band-building shape.
// ---------------------------------------------------------------------------

// Sanity bounds on the policy-futures butterfly series (bps after the
// PERCENT POINTS → bps conversion).  STIR butterflies cluster around zero
// with stress excursions to ±50 bps or so; bound at ±400 bps as a defensive
// frontend safety net matching the OIS butterfly bounds.
const FUTURES_BUTTERFLY_SANITY_MIN_BPS = -400;
const FUTURES_BUTTERFLY_SANITY_MAX_BPS = 400;

/** Sanitise + convert the bespoke butterfly time-series rows for the chart
 *  layer.  Input rows are in PERCENT POINTS (wire); output is in bps
 *  (display).  Outliers clamp to null. */
export function sanitiseButterflySeries(
  rows: ReadonlyArray<{ date: string; butterfly_value_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => {
    const bps =
      r.butterfly_value_pct == null || Number.isNaN(r.butterfly_value_pct)
        ? null
        : r.butterfly_value_pct * 100;
    return {
      date: r.date,
      value:
        bps == null
        || bps < FUTURES_BUTTERFLY_SANITY_MIN_BPS
        || bps > FUTURES_BUTTERFLY_SANITY_MAX_BPS
          ? null
          : bps,
    };
  });
}

export function buildReferenceBands(
  data: FuturesButterflySimpleOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseButterflySeries(data.time_series ?? []);
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
  data: FuturesButterflySimpleOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_butterfly == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_butterfly);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.z_score_butterfly,
    bucket,
    cm.curve_family,
    cm.butterfly_label,
  );

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score_butterfly != null
        ? {
            value: cm.z_score_butterfly,
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
  butterflyLabel: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'cheap' : 'rich';
  const meta = curveMetaFor(curveFamily);
  const family = meta
    ? `${meta.shortLabel} ${butterflyLabel}`
    : `${curveFamily} ${butterflyLabel}`;
  const policyLine =
    ' Implied-rate curvature on the STIR strip — read the move as how the market is pricing the SHAPE of near-term policy expectations, not as a forecast of realised central-bank meeting outcomes.';
  if (regime === 'Extreme') {
    return (
      `${family} butterfly is extreme ${direction} versus its trailing-year mean — `
      + `the body implied rate is ${direction === 'cheap' ? 'high' : 'low'} relative to the linearly interpolated wings.  `
      + `Sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + policyLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} butterfly is elevated ${direction} versus trailing-year history — `
      + `the body is ${direction === 'cheap' ? 'cheaper' : 'richer'} than the linearly interpolated wings on a normalised basis.`
      + policyLine
    );
  }
  return (
    `${family} butterfly is within its trailing-year norm; `
    + 'no extreme richness or cheapness in the body.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 + PR10 / P5 contract the wire-
 *  honesty disclosure flows from ``methodology_disclosure`` on the
 *  response (composed at compute() time, sourced from config.yaml +
 *  per-curve regime label) — NEVER a hardcoded TS literal.  The card's
 *  "Disclosure" row consumes that field verbatim so YAML edits flow to
 *  runtime. */
export function buildMethodologyRows(
  data: FuturesButterflySimpleOutput,
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
        'butterfly_value_pct = rate_body − 0.5 × (rate_wing_short + rate_wing_long), '
        + 'per-leg implied rates in PERCENT (rate_* derived from raw_price via the per-strip inverse-pricing flag); '
        + 'butterfly itself in PERCENT POINTS on the wire — multiplied by 100 in the display layer for the bps headline.',
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = belly CHEAP (body rate above wing average) · NEGATIVE = belly RICH',
    },
    {
      label: 'Triplet',
      value: `${cm.contract_code_wing_short} / ${cm.contract_code_body} / ${cm.contract_code_wing_long} (${cm.butterfly_label})`,
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
        cm.underlying_contract_code_wing_short
          ? `${cm.contract_code_wing_short} → ${cm.underlying_contract_code_wing_short}`
          : null,
        cm.underlying_contract_code_body
          ? `${cm.contract_code_body} → ${cm.underlying_contract_code_body}`
          : null,
        cm.underlying_contract_code_wing_long
          ? `${cm.contract_code_wing_long} → ${cm.underlying_contract_code_wing_long}`
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
      label: 'Weighting',
      value: 'FIXED 50-50 simple-butterfly (body = 1, wing_short = -0.5, wing_long = -0.5) — NOT DV01-neutral / NOT regression-fitted (PR11 planned-extension territory; refused at the methodology layer to prevent silent variant drift).',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (price observation on each strip slot)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the IMPLIED-RATE BUTTERFLY series (YAML-locked — no input-layer override on this primitive).`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / mid / percentile, PERCENT POINTS; rendered in bps in the display).',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days).`,
    },
    {
      label: 'Observations',
      value: `${cm.observation_count} aligned trading days (intersection of all three legs).`,
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
    { label: 'Catalog tool-19 (futures_butterfly_simple)' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
