// ============================================================================
// futuresPackAverageSimpleShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``policy_futures_get_futures_pack_average_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that a STIR pack average
// is the arithmetic mean of four consecutive quarterly contract implied
// rates on ONE policy-futures curve_family (e.g. SOFR_FUT whites =
// mean(SFR1..SFR4), SONIA_FUT reds = mean(SFI5..SFI8)), that the
// underlying STIR contracts quote 100-minus-rate (inverse_priced for the
// V1 universe SFR / ER / SFI), and that the desk-canonical headline
// quantity is the PACK AVERAGE IMPLIED RATE in PERCENT (3-dp) with the
// 1-day change rendered in bps (PERCENT POINTS on the wire * 100).
//
// V1 scope (ADR 0013): SOFR_FUT + SONIA_FUT are the executable curve
// families; EUR_SHORT_RATE_FUT is admitted at the schema layer but the
// compute layer returns a clean controlled-error envelope (IBOR regime,
// missing playbook metadata).  Greens / blues (strip positions 9-12) are
// PR11 planned-extension territory — only whites + reds are exposed at
// the controls layer.
//
// Sign convention: pack_average_implied_rate_pct is the absolute
// implied rate; daily_change_pack_average_implied_rate_pct POSITIVE =
// the pack repriced HIGHER (hawkish implied-policy-path stretch).  The
// 1d change wire value is in PERCENT POINTS (NOT bps); the display
// layer multiplies by 100 — lives in ONE place (``pctToBps`` below).
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
// labels + arithmetic-mean weighting disclosure + refusal of duration-
// weighted variants flow to runtime.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesPackAverage,
  type FuturesPackAverageSimpleDetailParams,
} from '@/services/ratesApi';
import type { FuturesPackAverageSimpleOutput } from '@/types/rates';
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
// Policy-futures curve-family metadata.  The V1 universe is three families
// (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT); mirrors the sibling
// policy_futures_get_futures_cross_market_spread_tool's CURVE_REGISTRY so
// flag + label + stem behave identically across the policy-futures roster.
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
  /** Central-bank short label. */
  cbShort: string;
  /** Master-stem prefix for the strip slots (e.g. 'SFR' → 'SFR1'). */
  stripStemPrefix: string;
  /** True when the V1 compute layer actually executes against this
   *  family.  EUR_SHORT_RATE_FUT is admitted at the schema layer but
   *  returns a controlled-error envelope (IBOR regime, missing
   *  ``delivery_month_type`` playbook metadata per ADR 0013). */
  executableV1: boolean;
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
    executableV1: true,
  },
  EUR_SHORT_RATE_FUT: {
    family: 'EUR_SHORT_RATE_FUT',
    flag: '🇪🇺',
    shortLabel: 'Euribor',
    longLabel: 'ECB Euribor strip',
    regime: 'IBOR',
    cbShort: 'ECB',
    stripStemPrefix: 'ER',
    executableV1: false,
  },
  SONIA_FUT: {
    family: 'SONIA_FUT',
    flag: '🇬🇧',
    shortLabel: 'SONIA',
    longLabel: 'BOE SONIA strip',
    regime: 'RFR',
    cbShort: 'BOE',
    stripStemPrefix: 'SFI',
    executableV1: true,
  },
};

export function curveMetaFor(curveFamily: string): PolicyFuturesCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** Curve-family options for the Monitor widget + extended controls.
 *  All three V1 families exposed — the controls layer honestly shows
 *  the compute-layer refusal envelope when the user picks
 *  EUR_SHORT_RATE_FUT rather than hiding the option. */
export const POLICY_FUTURES_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CURVE_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Pack options — closed Literal on the backend schema.  Whites = strip
 *  positions 1-4 (front year); reds = strip positions 5-8 (second
 *  year).  Greens / blues are PR11 planned-extension territory and NOT
 *  surfaced. */
export const PACK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'whites', label: 'Whites (1-4 · Front year)' },
  { value: 'reds', label: 'Reds (5-8 · Second year)' },
];

/** Build the pack-master-stem label like "SFR1..SFR4" from the wire
 *  ``contract_codes`` (the strip-slot master stems in strip-position
 *  order).  Mockup-faithful — the Compact mockup shows "Whites
 *  (SFR1..SFR4)" in the methodology card. */
export function packStemRange(contractCodes: ReadonlyArray<string>): string {
  if (contractCodes.length === 0) return '—';
  if (contractCodes.length === 1) return contractCodes[0];
  return `${contractCodes[0]}..${contractCodes[contractCodes.length - 1]}`;
}

/** Per-curve_family compact-card caveat.  Surfaces the arithmetic-mean
 *  weighting + the 100-minus-rate underlying-contract convention in one
 *  line — the load-bearing facts a reader must remember.  Mockup-
 *  faithful: the SOFR-whites default reads "Simple mean across 4
 *  contracts; 100-minus-rate convention (price up = rate down)." */
export function compactCaveatFor(curveFamily: string): string {
  const meta = curveMetaFor(curveFamily);
  void meta;
  return 'Simple mean across 4 contracts; 100-minus-rate convention (price up = rate down).';
}

// ---------------------------------------------------------------------------
// Unit conversion — wire PERCENT POINTS → display bps for the 1d change
// only.  The pack-average level itself stays in PERCENT (desk reads STIR
// implied rates in percent on the pack-average axis).  Lives in ONE
// place so the surfaces cannot drift.
// ---------------------------------------------------------------------------

export function pctToBps(pct: number | null | undefined): number | null {
  if (pct == null || !Number.isFinite(pct)) return null;
  return pct * 100;
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UsePolicyFuturesPackAverageArgs {
  curveFamily: string;
  pack: string;
  lookbackDays?: number;
  asOfDate?: string;
  fieldName?: string;
}

export interface UsePolicyFuturesPackAverageResult {
  data: FuturesPackAverageSimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-
 *  detail endpoint; re-fetches when any input changes.  Mirrors the
 *  sibling policy_futures cross-market hook shape so the per-tool
 *  surfaces look the same file-for-file. */
export function usePolicyFuturesPackAverage(
  args: UsePolicyFuturesPackAverageArgs,
): UsePolicyFuturesPackAverageResult {
  const [data, setData] = useState<FuturesPackAverageSimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: FuturesPackAverageSimpleDetailParams = {
    curve_family: args.curveFamily,
    pack: args.pack,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.pack) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesPackAverage(params)
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
    args.pack,
    args.lookbackDays,
    args.asOfDate,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *
 *    1. PACK IMPLIED RATE       — signed % (3-dp), primary emphasis,
 *                                 neutral tone (level is the read).
 *    2. 1D CHANGE (bps)         — signed bps + secondary % subtext,
 *                                 toneForChange (POSITIVE = hawkish
 *                                 pack repricing).
 *    3. Z-SCORE (252D)          — wire z + regime caption (Normal /
 *                                 Elevated / Extreme), toneForZScore.
 *
 *  THESIS Q3 documents why these vs alternatives.  Matches the shell-
 *  standard 3-KPI density per the Option-(c) precedent (catalog
 *  design_guardrail #4). */
export function compactKPIs(
  data: FuturesPackAverageSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyBps = pctToBps(cm.daily_change_pack_average_implied_rate_pct);
  return [
    {
      label: 'PACK IMPLIED RATE',
      value: signedFixed(cm.pack_average_implied_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
      subtext: bpsAsPercentSubtext(dailyBps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_pack_average, 2),
      tone: toneForZScore(cm.z_score_pack_average),
      caption: regimeForZScore(cm.z_score_pack_average),
    },
  ];
}

/** The extended view's headline KPI strip (mockups/Extended.png).  Pack
 *  average level + 1d change + z-score + 5d/percentile/range/observations.
 *  Per-leg implied rates surface as a secondary disclosure row via
 *  ``decompositionKPIs``. */
export function extendedKPIs(
  data: FuturesPackAverageSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyBps = pctToBps(cm.daily_change_pack_average_implied_rate_pct);
  return [
    {
      label: 'PACK IMPLIED RATE',
      value: signedFixed(cm.pack_average_implied_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
      subtext: bpsAsPercentSubtext(dailyBps, 3),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_pack_average, 2),
      tone: toneForZScore(cm.z_score_pack_average),
      caption: regimeForZScore(cm.z_score_pack_average),
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
      value: signedFixed(cm.high_252d_pack_average_implied_rate_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_pack_average_implied_rate_pct, 3),
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

/** Decomposition row — four per-leg master stems + current-front
 *  underlying contracts inline + per-leg implied rates so the desk can
 *  audit which contracts each strip slot resolves to today and what
 *  the per-leg implied rates that compose the pack average are.
 *  Mockup-faithful (Extended.png lower KPI strip carries SFR1 / SFR2 /
 *  SFR3 / SFR4 with underlying ticker + per-leg implied rate). */
export function decompositionKPIs(
  data: FuturesPackAverageSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const rows: KPIDescriptor[] = [];
  for (let i = 0; i < cm.contract_codes.length; i += 1) {
    const stem = cm.contract_codes[i];
    const under = cm.underlying_contract_codes[i] ?? '—';
    const rate = cm.implied_rates_pct[i] ?? null;
    const expiry = cm.expiry_dates[i] ?? null;
    rows.push({
      label: stem,
      value: signedFixed(rate, 3),
      unit: '%',
      tone: 'neutral',
      caption: under,
      subtext: expiry ? `expiry ${expiry}` : undefined,
    });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the pack-average
// series in display PERCENT.  Computed from the sanitised display
// series so a single bad row doesn't blow the envelope out.
// ---------------------------------------------------------------------------

// Sanity bounds on the policy-futures pack-average series (PERCENT).
// STIR pack averages across SFR / ER / SFI cluster in [-1.5%, 12%]
// across the universe (BoJ-like pre-2024 anchored near 0%, post-2022 Fed
// cycle peak near 5.5%); bound at ±15% as a defensive frontend safety
// net.  Mirrors the OIS forward-rate sanitisation.
const PACK_AVERAGE_SANITY_MIN_PCT = -1.5;
const PACK_AVERAGE_SANITY_MAX_PCT = 15;

/** Apply the sanity bound to the raw time-series.  Returns a new array
 *  with out-of-bound values nulled.  Caller hands the cleaned array to
 *  both the chart AND the reference-band builder so neither is distorted
 *  by outliers. */
export function sanitisePackSeries(
  rows: ReadonlyArray<{ date: string; pack_average_implied_rate_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.pack_average_implied_rate_pct == null
      || Number.isNaN(r.pack_average_implied_rate_pct)
      || r.pack_average_implied_rate_pct < PACK_AVERAGE_SANITY_MIN_PCT
      || r.pack_average_implied_rate_pct > PACK_AVERAGE_SANITY_MAX_PCT
        ? null
        : r.pack_average_implied_rate_pct,
  }));
}

export function buildReferenceBands(
  data: FuturesPackAverageSimpleOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitisePackSeries(data.time_series ?? []);
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
  data: FuturesPackAverageSimpleOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_pack_average == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_pack_average);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.z_score_pack_average,
    bucket,
    cm.curve_family,
    cm.pack,
  );

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score_pack_average != null
        ? {
            value: cm.z_score_pack_average,
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
  pack: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const meta = curveMetaFor(curveFamily);
  const market = meta?.shortLabel ?? curveFamily;
  const cb = meta?.cbShort ?? curveFamily;
  const direction = z > 0 ? 'above' : 'below';
  const directionPhrase =
    z > 0
      ? 'consistent with the implied policy path repricing higher (hawkish stretch)'
      : 'consistent with the implied policy path repricing lower (dovish stretch)';
  if (regime === 'Extreme') {
    return (
      `${market} ${pack} pack average is extreme ${direction} its trailing-year mean.  `
      + `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range `
      + `and is ${directionPhrase} — read as how the strip is repricing ${cb}'s near-term policy path, not as a forecast of realised meeting outcomes.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `${market} ${pack} pack average is elevated vs. its trailing-year history.  `
      + `The recent move is ${directionPhrase}.`
    );
  }
  return (
    `${market} ${pack} pack average is within its trailing-year norm; `
    + 'no extreme stretch in either direction.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 + PR10 / P5 contract the wire-
 *  honesty disclosure flows from ``methodology_disclosure`` on the
 *  response (composed at compute() time, sourced from config.yaml +
 *  per-curve_family regime label + arithmetic-mean weighting +
 *  inverse-pricing rule + refusal of duration-weighted variants) —
 *  NEVER a hardcoded TS literal.  The card's "Disclosure" row consumes
 *  that field verbatim so YAML edits flow to runtime. */
export function buildMethodologyRows(
  data: FuturesPackAverageSimpleOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = curveMetaFor(cm.curve_family);
  const inversePricingRule = cm.inverse_priced
    ? `Inverse-priced — implied_rate_pct = 100 − raw_price per leg (${meta?.shortLabel ?? cm.curve_family} convention).`
    : `Direct-priced (${meta?.shortLabel ?? cm.curve_family} convention).`;
  return [
    {
      label: 'Construction',
      value:
        'Arithmetic mean of FOUR consecutive quarterly contract implied rates: '
        + `pack_average_implied_rate_pct = mean(implied_rate_pct(${packStemRange(cm.contract_codes)})).  `
        + 'Wire in PERCENT (3-dp); display layer renders the level in PERCENT and the 1d change in bps via × 100.',
    },
    {
      label: 'Sign convention',
      value:
        'POSITIVE 1D change = pack repriced HIGHER (hawkish implied-policy-path stretch); '
        + 'NEGATIVE = lower (dovish).  The level itself is regime-agnostic.',
    },
    {
      label: 'Pack',
      value: cm.pack_label,
    },
    {
      label: 'Strip positions',
      value: `${cm.strip_positions.join(', ')} (${cm.pack} convention; YAML-locked).`,
    },
    {
      label: 'Curve family',
      value: meta
        ? `${meta.shortLabel} · ${meta.longLabel} (${cm.curve_family}) · ${meta.cbShort}`
        : cm.curve_family,
    },
    {
      label: 'Underlying contracts',
      value: cm.contract_codes
        .map((stem, i) => {
          const under = cm.underlying_contract_codes[i];
          return under ? `${stem} → ${under}` : stem;
        })
        .join(' · '),
    },
    {
      label: 'Per-leg implied rates',
      value: cm.contract_codes
        .map(
          (stem, i) => `${stem}: ${unsignedFixed(cm.implied_rates_pct[i] ?? null, 3)}%`,
        )
        .join(' · '),
    },
    {
      label: 'Quote convention',
      value: `100-minus-rate on the underlying STIR contracts.  ${inversePricingRule}`,
    },
    {
      label: 'Short-rate regime',
      value: meta
        ? `${cm.short_rate_regime} — ${meta.regime === 'RFR' ? 'compounded daily risk-free reference rate' : 'unsecured 3M term IBOR'} per ${meta.cbShort} market convention.`
        : `${cm.short_rate_regime} per market convention.`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (price observation on each pack member).`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the pack-average series (YAML-locked — no input-layer override on this primitive).`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / mid / percentile, PERCENT).',
    },
    {
      label: 'Alignment',
      value: 'Intersection of all four legs\' trading calendars — snapshot anchored to the most recent date where ALL FOUR legs have a value (no stale-on-one-side averages).',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days).`,
    },
    {
      label: 'Observations',
      value: `${cm.observation_count} aligned trading days (intersection of the four legs after cleaning).`,
    },
    {
      label: 'Scope guardrail',
      value: 'SIMPLE arithmetic-mean pack average — NOT duration-weighted, NOT meeting-by-meeting, NOT CTD-of-OIS.  Weighted / meeting-keyed variants ship as SEPARATE primitives (PR11 planned-extension territory).',
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
    { label: 'ICE SONIA / Euribor futures contract spec' },
    { label: 'Catalog tool-22 (futures_pack_average_simple)' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
