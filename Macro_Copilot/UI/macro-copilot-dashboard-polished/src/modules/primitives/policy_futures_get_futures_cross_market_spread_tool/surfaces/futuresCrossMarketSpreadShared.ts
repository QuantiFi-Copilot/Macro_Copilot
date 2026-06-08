// ============================================================================
// futuresCrossMarketSpreadShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``policy_futures_get_futures_cross_market_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that a STIR matched-strip
// cross-market spread is a 2-leg implied-rate differential between TWO
// different policy-futures curve_family values at ONE strip position (e.g.
// SOFR_FUT vs SONIA_FUT strip 1 = SFR1 − SFI1, SOFR_FUT vs EUR_SHORT_RATE_FUT
// strip 4 = SFR4 − ER4), that the underlying futures quote 100-minus-rate
// (inverse_priced) for the V1 universe (SFR / ER / SFI), that the desk-
// recognised cross-CB divergence quantity lives on the IMPLIED-RATE axis
// in PERCENT POINTS on the wire (the policy-futures sub-domain stays in
// PERCENT POINTS on implied-rate-derived objects), and that the desk-
// canonical display direction is A − B in bps (orientation-honest — UNLIKE
// the same-curve calendar spread there is NO sign flip; swapping the
// inputs flips the sign by construction).  The shared shells do not.
//
// Sign convention (wire-frozen, display-preserved):
//   spread_value_pct = implied_rate_pct(curve_family_a)
//                    − implied_rate_pct(curve_family_b)
// where rate_* is the per-leg implied rate in PERCENT (derived from the
// leg's raw_price via the per-strip inverse_pricing flag).  Display layer
// renders the spread in bps via ``* 100`` but keeps the A − B orientation
// — POSITIVE = A's policy path PRICED ABOVE B's at this strip slot
// (cross-CB divergence direction).
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
// labels + the mixed-regime call-out flow to runtime.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesCrossMarket,
  type FuturesCrossMarketSpreadDetailParams,
} from '@/services/ratesApi';
import type { FuturesCrossMarketSpreadOutput } from '@/types/rates';
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
// (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT); mirrors the sibling
// policy_futures_get_futures_calendar_spread_tool's CURVE_REGISTRY so flag
// + label + stem behave identically across the policy-futures roster.
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
  /** Central-bank short label (mockup uses 'Fed' / 'ECB' / 'BOE'
   *  in the cross-CB caveat line). */
  cbShort: string;
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
    cbShort: 'Fed',
    stripStemPrefix: 'SFR',
  },
  EUR_SHORT_RATE_FUT: {
    family: 'EUR_SHORT_RATE_FUT',
    flag: '🇪🇺',
    shortLabel: 'Euribor',
    longLabel: 'ECB Euribor strip',
    regime: 'IBOR',
    cbShort: 'ECB',
    stripStemPrefix: 'ER',
  },
  SONIA_FUT: {
    family: 'SONIA_FUT',
    flag: '🇬🇧',
    shortLabel: 'SONIA',
    longLabel: 'BOE SONIA strip',
    regime: 'RFR',
    cbShort: 'BOE',
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

/** Curve-family options for the Monitor widget + extended controls. */
export const POLICY_FUTURES_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CURVE_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

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

/** Short pair label combining the two markets at the strip slot, e.g.
 *  "SFR1-SFI1" / "SFR4-ER4".  Mirrors the wire's ``spread_label`` shape
 *  so the identity row reads the same whether the data has resolved or
 *  not.  Falls back to the curve-family codes when meta is missing. */
export function pairStripLabel(
  curveFamilyA: string,
  curveFamilyB: string,
  stripPosition: number,
): string {
  const a = curveMetaFor(curveFamilyA);
  const b = curveMetaFor(curveFamilyB);
  const aStem = a ? `${a.stripStemPrefix}${stripPosition}` : `${curveFamilyA} pos${stripPosition}`;
  const bStem = b ? `${b.stripStemPrefix}${stripPosition}` : `${curveFamilyB} pos${stripPosition}`;
  return `${aStem}-${bStem}`;
}

/** Cross-CB short pair label, e.g. "Fed − ECB" / "Fed − BOE".  Surfaced
 *  in the identity subtitle + the compact secondary line. */
export function crossCBLabel(
  curveFamilyA: string,
  curveFamilyB: string,
): string {
  const a = curveMetaFor(curveFamilyA);
  const b = curveMetaFor(curveFamilyB);
  const aCb = a?.cbShort ?? curveFamilyA;
  const bCb = b?.cbShort ?? curveFamilyB;
  return `${aCb} − ${bCb}`;
}

/** Desk-canonical caveat builder for the compact view footer.  Encodes the
 *  load-bearing facts a reader must remember: the spread is FRONT-QUARTER
 *  cross-CB divergence on implied rates and the underlying STIR contract
 *  quote convention is 100-minus-rate (so the implied-rate axis is
 *  load-bearing — UP price = DOWN implied rate).  Templated on the
 *  cross-CB pair so the caveat tracks the actual selection (P5 wire-
 *  honesty — no hardcoded "Fed vs ECB" when a user picks a different
 *  cross-market pair).  Mockup-faithful for the Fed/ECB default. */
export function compactCaveatFor(
  curveFamilyA: string,
  curveFamilyB: string,
): string {
  return `Front-quarter ${crossCBLabel(curveFamilyA, curveFamilyB)} divergence on implied rates; underlying contracts quote inverse.`;
}

// ---------------------------------------------------------------------------
// Mixed-regime detector.  Per the catalog guardrail and ADR 0013 mixed-
// regime pairs (RFR vs IBOR — e.g. SOFR_FUT vs EUR_SHORT_RATE_FUT) MUST
// surface the regime-mismatch caveat inline; the wire's
// ``methodology_disclosure`` already calls this out, but the extended
// view's per-leg disclosure card also needs the short label.  Mirrors
// the sovereign cross_market_spread tool's same handling.
// ---------------------------------------------------------------------------

export function isMixedRegime(
  legARegime: string | null | undefined,
  legBRegime: string | null | undefined,
): boolean {
  if (!legARegime || !legBRegime) return false;
  return legARegime !== legBRegime;
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UsePolicyFuturesCrossMarketArgs {
  curveFamilyA: string;
  curveFamilyB: string;
  stripPosition: number;
  lookbackDays?: number;
  asOfDate?: string;
  fieldName?: string;
}

export interface UsePolicyFuturesCrossMarketResult {
  data: FuturesCrossMarketSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the sibling
 *  policy_futures calendar-spread hook shape so the per-tool surfaces
 *  look the same file-for-file. */
export function usePolicyFuturesCrossMarket(
  args: UsePolicyFuturesCrossMarketArgs,
): UsePolicyFuturesCrossMarketResult {
  const [data, setData] = useState<FuturesCrossMarketSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: FuturesCrossMarketSpreadDetailParams = {
    curve_family_a: args.curveFamilyA,
    curve_family_b: args.curveFamilyB,
    strip_position: args.stripPosition,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.curveFamilyA
      || !args.curveFamilyB
      || !args.stripPosition
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    if (args.curveFamilyA === args.curveFamilyB) {
      // Schema layer rejects self-spreads; short-circuit here so the
      // fetch doesn't fire and the shell renders the loading skeleton
      // until the user picks a distinct B leg.
      setData(null);
      setErrorMessage(
        'Cross-market spread requires two distinct curve families.',
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesCrossMarket(params)
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
    args.curveFamilyA,
    args.curveFamilyB,
    args.stripPosition,
    args.lookbackDays,
    args.asOfDate,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Unit conversion — wire PERCENT POINTS → display bps.  NO sign flip
// (A − B is desk-canonical for cross-CB divergence; swapping inputs
// flips the sign by construction).  Lives in ONE place so the three
// surfaces cannot drift.
// ---------------------------------------------------------------------------

export function pctToBps(pct: number | null | undefined): number | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  return pct * 100;
}

// ---------------------------------------------------------------------------
// Sign-direction caption combining stretch + cross-CB direction.  In the
// A − B convention, POSITIVE = A priced ABOVE B (A's policy path higher
// than B's); NEGATIVE = A priced BELOW B.  Surfaced in the compact KPI
// primary cell.
// ---------------------------------------------------------------------------

export function crossCBDirectionCaption(
  spreadBps: number | null | undefined,
  curveFamilyA: string,
  curveFamilyB: string,
): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  const a = curveMetaFor(curveFamilyA)?.cbShort ?? curveFamilyA;
  const b = curveMetaFor(curveFamilyB)?.cbShort ?? curveFamilyB;
  if (spreadBps > 0) return `${a} pricing > ${b}`;
  if (spreadBps < 0) return `${a} pricing < ${b}`;
  return 'Flat';
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *
 *    1. SPREAD (BPS)     — signed bps (A − B convention), primary
 *                          emphasis, neutral tone (sign is the info);
 *                          caption "Fed pricing > ECB" / "< ECB" / "Flat".
 *    2. 1D CHANGE (BPS)  — signed bps, tone-coloured by toneForChange.
 *    3. Z-SCORE (252D)   — wire z (no flip — A − B orientation is the
 *                          desk-canonical direction), regime caption
 *                          (Normal / Elevated / Extreme).
 *
 *  THESIS Q3 documents why these vs alternatives.  Matches the shell-
 *  standard 3-KPI density per the Option-(c) precedent (catalog
 *  design_guardrail #4); the mockup's denser layout is preserved in the
 *  extended view. */
export function compactKPIs(
  data: FuturesCrossMarketSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const spreadBps = pctToBps(cm.spread_value_pct);
  const dailyBps = pctToBps(cm.daily_change_spread_value_pct);
  return [
    {
      label: 'SPREAD (BPS)',
      value: signedFixed(spreadBps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: `(${signedFixed(cm.spread_value_pct, 2)}%)`,
    },
    {
      label: '1D CHANGE (BPS)',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_spread, 2),
      tone: toneForZScore(cm.z_score_spread),
      caption: regimeForZScore(cm.z_score_spread),
    },
  ];
}

/** The extended view's headline KPI strip (mockups/Extended.png).  The
 *  backend wire is in PERCENT POINTS for the spread series + per-leg
 *  rates in PERCENT; the headline cells display in bps for spread +
 *  daily change to match the desk-recognised quote-size unit and in
 *  PERCENT for the per-leg implied rates. */
export function extendedKPIs(
  data: FuturesCrossMarketSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const spreadBps = pctToBps(cm.spread_value_pct);
  const dailyBps = pctToBps(cm.daily_change_spread_value_pct);
  const highBps = pctToBps(cm.high_252d_spread_value_pct);
  const lowBps = pctToBps(cm.low_252d_spread_value_pct);
  return [
    {
      label: 'SPREAD',
      value: signedFixed(spreadBps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: `(${signedFixed(cm.spread_value_pct, 2)}%)`,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_spread, 2),
      tone: toneForZScore(cm.z_score_spread),
      caption: regimeForZScore(cm.z_score_spread),
    },
    {
      label: 'PERCENTILE (252D)',
      value:
        cm.percentile_252d != null && Number.isFinite(cm.percentile_252d)
          ? `${Math.round(cm.percentile_252d)}`
          : '—',
      unit: 'th',
      caption: bucketForPercentile(cm.percentile_252d),
    },
    {
      label: '252D HIGH',
      value: signedFixed(highBps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(lowBps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'OBSERVATIONS',
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
    {
      label: `LEG A (${cm.contract_code_a})`,
      value: signedFixed(cm.implied_rate_pct_a, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LEG B (${cm.contract_code_b})`,
      value: signedFixed(cm.implied_rate_pct_b, 3),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

/** Decomposition row — two per-leg master stems + current-front
 *  underlying contracts inline so the desk can audit which contracts
 *  each strip slot resolves to today on each market.  Mockup-faithful
 *  (Extended.png lower KPI strip carries SHORT / LONG with underlying
 *  ticker + expiry). */
export function decompositionKPIs(
  data: FuturesCrossMarketSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: `LEG A (${cm.contract_code_a})`,
      value: cm.underlying_contract_code_a ?? '—',
      unit: '',
      tone: 'neutral',
      subtext: cm.expiry_date_a
        ? `expiry ${cm.expiry_date_a}`
        : undefined,
    },
    {
      label: `LEG B (${cm.contract_code_b})`,
      value: cm.underlying_contract_code_b ?? '—',
      unit: '',
      tone: 'neutral',
      subtext: cm.expiry_date_b
        ? `expiry ${cm.expiry_date_b}`
        : undefined,
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread series in
// display bps.  Computed from the sanitised display series so a single
// bad row doesn't blow the envelope out.  Mirrors the sibling calendar-
// spread band-building shape.
// ---------------------------------------------------------------------------

// Sanity bounds on the policy-futures cross-market spread series (bps).
// STIR cross-CB spreads cluster within ~500 bps even during stress (the
// SFR-vs-ER gap during 2022-23 reached ~400 bps); bound at ±800 bps as a
// defensive frontend safety net.
const FUTURES_CROSS_MARKET_SANITY_MIN_BPS = -800;
const FUTURES_CROSS_MARKET_SANITY_MAX_BPS = 800;

/** Sanitise + convert the bespoke cross-market spread time-series rows
 *  for the chart layer.  Input rows are in PERCENT POINTS (wire, signed
 *  A − B); output is in display bps (signed A − B, via ``pctToBps`` —
 *  no sign flip).  Outliers clamp to null. */
export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; spread_value_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => {
    const bps = pctToBps(r.spread_value_pct);
    return {
      date: r.date,
      value:
        bps == null
        || bps < FUTURES_CROSS_MARKET_SANITY_MIN_BPS
        || bps > FUTURES_CROSS_MARKET_SANITY_MAX_BPS
          ? null
          : bps,
    };
  });
}

export function buildReferenceBands(
  data: FuturesCrossMarketSpreadOutput,
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
// Stretch-context builder — built on the wire z + percentile directly
// (no flip — A − B is the desk-canonical orientation).
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: FuturesCrossMarketSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_spread == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_spread);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.z_score_spread,
    bucket,
    cm.curve_family_a,
    cm.curve_family_b,
    cm.spread_label,
  );

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score_spread != null
        ? {
            value: cm.z_score_spread,
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
  curveFamilyA: string,
  curveFamilyB: string,
  spreadLabel: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const a = curveMetaFor(curveFamilyA);
  const b = curveMetaFor(curveFamilyB);
  const cbPair = `${a?.cbShort ?? curveFamilyA} − ${b?.cbShort ?? curveFamilyB}`;
  const policyLine =
    ' Implied-rate cross-CB divergence on the STIR strip — read the move as how the market is repricing the SHAPE of near-term policy divergence between the two central banks, not as a forecast of realised meeting outcomes.';
  if (regime === 'Extreme') {
    return (
      `${cbPair} ${spreadLabel} cross-market spread is extreme ${direction} versus its trailing-year mean — `
      + `${a?.cbShort ?? curveFamilyA}'s implied rate is `
      + `${direction === 'wider' ? 'priced well above' : 'priced well below'} `
      + `${b?.cbShort ?? curveFamilyB}'s at this strip slot.  `
      + `Sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + policyLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${cbPair} ${spreadLabel} cross-market spread is elevated ${direction} versus trailing-year history — `
      + `${a?.cbShort ?? curveFamilyA}'s implied rate is `
      + `${direction === 'wider' ? 'higher' : 'lower'} than ${b?.cbShort ?? curveFamilyB}'s on a normalised basis.`
      + policyLine
    );
  }
  return (
    `${cbPair} ${spreadLabel} cross-market spread is within its trailing-year norm; `
    + 'no extreme widening or tightening stretch.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 + PR10 / P5 contract the wire-
 *  honesty disclosure flows from ``methodology_disclosure`` on the
 *  response (composed at compute() time, sourced from config.yaml +
 *  per-curve_family regime label + explicit mixed-regime call-out) —
 *  NEVER a hardcoded TS literal.  The card's "Disclosure" row consumes
 *  that field verbatim so YAML edits flow to runtime. */
export function buildMethodologyRows(
  data: FuturesCrossMarketSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const aMeta = curveMetaFor(cm.curve_family_a);
  const bMeta = curveMetaFor(cm.curve_family_b);
  const mixed = isMixedRegime(cm.short_rate_regime_a, cm.short_rate_regime_b);
  const inversePricingRule = [
    cm.inverse_priced_a
      ? `leg A inverse-priced — implied_rate_pct = 100 − raw_price (${aMeta?.shortLabel ?? cm.curve_family_a}).`
      : `leg A direct-priced (${aMeta?.shortLabel ?? cm.curve_family_a}).`,
    cm.inverse_priced_b
      ? `leg B inverse-priced — implied_rate_pct = 100 − raw_price (${bMeta?.shortLabel ?? cm.curve_family_b}).`
      : `leg B direct-priced (${bMeta?.shortLabel ?? cm.curve_family_b}).`,
  ].join('  ');
  return [
    {
      label: 'Construction',
      value:
        `WIRE: spread_value_pct = implied_rate_pct(${cm.curve_family_a}) − implied_rate_pct(${cm.curve_family_b}) `
        + `at strip position ${cm.strip_position}, per-leg implied rates in PERCENT (derived from raw_price via the per-strip inverse-pricing flag); `
        + 'wire signed in PERCENT POINTS. DISPLAY: bps via × 100, A − B orientation preserved (no sign flip — A − B is the desk-canonical cross-CB divergence direction).',
    },
    {
      label: 'Sign convention',
      value:
        `POSITIVE = ${aMeta?.cbShort ?? cm.curve_family_a} pricing ABOVE ${bMeta?.cbShort ?? cm.curve_family_b} at this strip slot · `
        + `NEGATIVE = ${aMeta?.cbShort ?? cm.curve_family_a} pricing BELOW ${bMeta?.cbShort ?? cm.curve_family_b}.  Swapping the inputs flips the sign by construction.`,
    },
    {
      label: 'Pair',
      value: `${cm.contract_code_a} / ${cm.contract_code_b} (${cm.spread_label})`,
    },
    {
      label: 'Curve families',
      value: [
        aMeta
          ? `${aMeta.shortLabel} · ${aMeta.longLabel} (${cm.curve_family_a})`
          : cm.curve_family_a,
        bMeta
          ? `${bMeta.shortLabel} · ${bMeta.longLabel} (${cm.curve_family_b})`
          : cm.curve_family_b,
      ].join('  vs  '),
    },
    {
      label: 'Underlying contracts',
      value: [
        cm.underlying_contract_code_a
          ? `${cm.contract_code_a} → ${cm.underlying_contract_code_a}`
          : null,
        cm.underlying_contract_code_b
          ? `${cm.contract_code_b} → ${cm.underlying_contract_code_b}`
          : null,
      ]
        .filter((s): s is string => s != null)
        .join(' · ') || '—',
    },
    {
      label: 'Quote convention',
      value: `100-minus-rate on the underlying STIR contracts. ${inversePricingRule}`,
    },
    {
      label: 'Per-leg short-rate regime',
      value: mixed
        ? `MIXED — ${aMeta?.shortLabel ?? cm.curve_family_a}: ${cm.short_rate_regime_a}; ${bMeta?.shortLabel ?? cm.curve_family_b}: ${cm.short_rate_regime_b}. NO pack-average collapse (catalog guardrail).`
        : `${cm.short_rate_regime_a} (both legs) — compounded daily RFR or unsecured 3M IBOR per market convention.`,
    },
    {
      label: 'Alignment',
      value: 'Intersection of both markets\' trading calendars — snapshot anchored to the most recent date BOTH legs have a value (no stale-on-one-side pairings).',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (price observation on each strip slot)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the cross-market SPREAD series (YAML-locked — no input-layer override on this primitive). Orientation matches the wire (A − B).`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / mid / percentile, PERCENT POINTS on the wire; rendered in bps in the display, same A − B orientation).',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days).`,
    },
    {
      label: 'Observations',
      value: `${cm.observation_count} aligned trading days (intersection of both legs after cleaning).`,
    },
    {
      label: 'Scope guardrail',
      value: 'RAW cross-market implied-rate differential — NOT basis-adjusted (cross-currency basis NOT netted), NOT beta-adjusted (no regression residual).  Basis-/beta-adjusted variants are PR11 planned-extension territory and ship as separate primitives.',
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
    { label: 'Catalog tool-21 (futures_cross_market_spread)' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
