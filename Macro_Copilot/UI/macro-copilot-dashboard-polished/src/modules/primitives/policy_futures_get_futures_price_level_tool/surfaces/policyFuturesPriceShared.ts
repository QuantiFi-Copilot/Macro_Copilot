// ============================================================================
// policyFuturesPriceShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``policy_futures_get_futures_price_level_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows that a STIR strip-
// position price is inverse-priced (100 − rate) for SFR / ER / SFI in
// the V1 universe, that the desk-recognised level is the implied rate in
// PERCENT, and that the z-score lives on the IMPLIED-RATE axis (not the
// raw-price axis — inverse-priced raw prices would flip the sign of
// every extreme reading).  The shared shells do not.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  KPI builders +
// formatting + tone live here in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesPrice,
  type PolicyFuturesPriceDetailParams,
} from '@/services/ratesApi';
import type { PolicyFuturesPriceLevelOutput } from '@/types/rates';
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
// Curve-family metadata.  Policy-futures families are disjoint from the
// linker / sovereign registries — flag + label live here in the per-tool
// layer rather than the shared countryCaveats registry (which is linker-
// specific).
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
  /** Underlying short-rate object expressed verbatim — 'RFR' (SOFR /
   *  SONIA compounded daily) or 'IBOR' (3M Euribor unsecured term).  */
  regime: 'RFR' | 'IBOR';
  /** Master-stem prefix for the strip slots (e.g. 'SFR' → 'SFR1', 'SFR2'). */
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
 *  e.g. (SOFR_FUT, 1) → 'SFR1'.  Falls back to a generic
 *  '<family>#<position>' shape when the family is unknown. */
export function stripStemLabel(
  curveFamily: string,
  stripPosition: number,
): string {
  const meta = curveMetaFor(curveFamily);
  if (!meta) return `${curveFamily}#${stripPosition}`;
  return `${meta.stripStemPrefix}${stripPosition}`;
}

/** Whites (1-4) vs Reds (5-8) classification per the V1 universe.  Surfaced
 *  as a status tag on the compact identity row so the desk sees the strip
 *  segment at a glance. */
export function stripSegmentLabel(stripPosition: number): 'WHITES' | 'REDS' | 'GREENS' {
  if (stripPosition <= 4) return 'WHITES';
  if (stripPosition <= 8) return 'REDS';
  return 'GREENS';
}

/** Pack label per the standard SOFR / Euribor / SONIA naming
 *  (Whites = front quarterly pack; Reds = second pack; Greens = third). */
export function stripPackLabel(
  curveFamily: string,
  stripPosition: number,
): string {
  const meta = curveMetaFor(curveFamily);
  const segment = stripSegmentLabel(stripPosition);
  const market = meta?.shortLabel ?? curveFamily;
  if (segment === 'WHITES') return `${market} Whites Pack`;
  if (segment === 'REDS') return `${market} Reds Pack`;
  return `${market} Greens Pack`;
}

/** Strip-position ordinal label, e.g. 1 → '1st Quarterly'.  Used in the
 *  extended identity subtitle. */
export function stripPositionOrdinal(stripPosition: number): string {
  const tail =
    stripPosition === 1
      ? 'st'
      : stripPosition === 2
        ? 'nd'
        : stripPosition === 3
          ? 'rd'
          : 'th';
  return `${stripPosition}${tail} Quarterly`;
}

/** Strip-position options for the Monitor widget + extended controls.  Covers
 *  the V1 universe of 1..8 (whites + reds). */
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
export const POLICY_FUTURES_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(CURVE_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.shortLabel} · ${m.longLabel}`,
  }));

/** Desk-canonical caveat string for the compact view footer.  Mirrors the
 *  per-strip read convention the methodology disclosure surfaces in full. */
export const POLICY_FUTURES_PRICE_COMPACT_CAVEAT =
  '100-minus-rate; UP price = DOWN implied rate (dovish). Rolling-generic strip read.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UsePolicyFuturesPriceArgs {
  curveFamily: string;
  stripPosition: number;
  lookbackDays?: number;
  asOfDate?: string;
  fieldName?: string;
}

export interface UsePolicyFuturesPriceResult {
  data: PolicyFuturesPriceLevelOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Errors are stringified
 *  and surfaced through the shell's ``errorMessage`` slot. */
export function usePolicyFuturesPrice(
  args: UsePolicyFuturesPriceArgs,
): UsePolicyFuturesPriceResult {
  const [data, setData] = useState<PolicyFuturesPriceLevelOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: PolicyFuturesPriceDetailParams = {
    curve_family: args.curveFamily,
    strip_position: args.stripPosition,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.stripPosition) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesPrice(params)
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
    args.stripPosition,
    args.lookbackDays,
    args.asOfDate,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Implied-rate <-> bps subtext utility
// ---------------------------------------------------------------------------

/** Convert a PERCENT-points change (the wire's
 *  ``daily_change_implied_rate_pct``) into a bps display string.
 *  Returns a signed value with the 'bp' unit appended.  Used directly
 *  by the KPI cell; surrounded helpers compute the matching raw-price
 *  subtext line. */
export function impliedRateChangeBps(
  changePct: number | null | undefined,
): number | null {
  if (changePct == null || !Number.isFinite(changePct)) return null;
  return changePct * 100;
}

/** Convert a raw-price change into a percent-of-price subtext string,
 *  e.g. ``-0.045`` raw-price change on a 95.75 reference price → ``"(-0.047%)"``.
 *  Reference price defaults to the snapshot's current ``raw_price``. */
export function rawPriceChangeSubtext(
  changeRaw: number | null | undefined,
  referencePrice: number | null | undefined,
  decimals: number = 2,
): string | undefined {
  if (
    changeRaw == null ||
    referencePrice == null ||
    !Number.isFinite(changeRaw) ||
    !Number.isFinite(referencePrice) ||
    referencePrice === 0
  ) {
    return undefined;
  }
  const pct = (changeRaw / referencePrice) * 100;
  const sign = pct >= 0 ? '+' : '';
  return `(${sign}${pct.toFixed(decimals)}%)`;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *
 *    1. IMPLIED RATE (PERCENT)   — desk-recognised level (primary emphasis,
 *                                  neutral tone, raw-price subtext)
 *    2. 1D CHANGE (BPS)          — implied-rate change in bps, toneForChange
 *                                  (positive bps = tightening = coral)
 *    3. Z-SCORE (252D)           — z of the implied-rate level, toneForZScore
 *
 *  Per the Option-(c) precedent the compact view uses the shell-standard
 *  3-KPI density.  The mockup's higher density (5D / 1M / percentile /
 *  high / low / observations) is exposed in the extended view; the
 *  compact view's expand affordance opens the full canvas. */
export function compactKPIs(
  data: PolicyFuturesPriceLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyBps = impliedRateChangeBps(cm.daily_change_implied_rate_pct);
  return [
    {
      label: 'IMPLIED RATE (PERCENT)',
      value: signedFixed(cm.implied_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
      emphasis: 'primary',
      subtext: `Price ${cm.raw_price.toFixed(2)}`,
    },
    {
      label: '1D CHANGE (BPS)',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      // Positive bps = implied rate UP = tightening → coral (negative tone).
      // toneForChange maps positive→negative-color (yield up = tightening)
      // by convention; matches the SOFR / Euribor / SONIA reading desk's
      // tone semantics.
      tone: toneForChange(dailyBps),
      subtext: rawPriceChangeSubtext(
        cm.daily_change_raw_price,
        cm.raw_price,
        2,
      ),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_implied_rate, 2),
      tone: toneForZScore(cm.z_score_implied_rate),
      caption: regimeForZScore(cm.z_score_implied_rate),
    },
  ];
}

/** The extended view's FULL KPI strip — 9 cells matching
 *  mockups/Extended.png ordering. */
export function extendedKPIs(
  data: PolicyFuturesPriceLevelOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyBps = impliedRateChangeBps(cm.daily_change_implied_rate_pct);
  return [
    {
      label: 'IMPLIED RATE (PERCENT)',
      value: signedFixed(cm.implied_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
      subtext: `Price ${cm.raw_price.toFixed(2)}`,
    },
    {
      label: 'PRICE',
      value: cm.raw_price.toFixed(2),
      tone: 'neutral',
    },
    {
      label: '1D CHANGE (BPS)',
      value: signedFixed(dailyBps, 1),
      unit: 'bp',
      tone: toneForChange(dailyBps),
      subtext: rawPriceChangeSubtext(
        cm.daily_change_raw_price,
        cm.raw_price,
        2,
      ),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.z_score_implied_rate, 2),
      tone: toneForZScore(cm.z_score_implied_rate),
      caption: regimeForZScore(cm.z_score_implied_rate),
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
      label: '252D HIGH (RATE)',
      value: signedFixed(cm.high_252d_implied_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D LOW (RATE)',
      value: signedFixed(cm.low_252d_implied_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: '252D MID (RATE)',
      value: signedFixed(cm.mid_252d_implied_rate_pct, 2),
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
// Reference-band builder — ±2σ / ±1.5σ envelope on the IMPLIED-RATE
// series.  Computed from the sanitised series so a single bad row
// doesn't blow the envelope out.
// ---------------------------------------------------------------------------

// Sanity bounds on the policy-futures implied-rate series (PERCENT).  Across
// the V1 universe rates have traded ~[-0.5%, +6%]; this is a defensive
// frontend safety net mirroring the pattern in realYieldShared.
const IMPLIED_RATE_SANITY_MIN = -2;
const IMPLIED_RATE_SANITY_MAX = 10;

/** Apply the sanity bound + return the implied-rate series (in PERCENT)
 *  for charting.  Caller hands the cleaned array to BOTH the chart AND
 *  buildReferenceBands so neither sees out-of-bound observations. */
export function sanitiseImpliedRateSeries(
  rows: ReadonlyArray<{ date: string; implied_rate_pct: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.implied_rate_pct == null
        || Number.isNaN(r.implied_rate_pct)
        || r.implied_rate_pct < IMPLIED_RATE_SANITY_MIN
        || r.implied_rate_pct > IMPLIED_RATE_SANITY_MAX
        ? null
        : r.implied_rate_pct,
  }));
}

export function buildReferenceBands(
  data: PolicyFuturesPriceLevelOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseImpliedRateSeries(data.time_series ?? []);
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
  data: PolicyFuturesPriceLevelOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.z_score_implied_rate == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.z_score_implied_rate);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(regime, cm.z_score_implied_rate, bucket);

  return {
    percentile:
      cm.percentile_252d != null
        ? { value: cm.percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.z_score_implied_rate != null
        ? {
            value: cm.z_score_implied_rate,
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
  const policyTilt =
    z > 0
      ? 'consistent with tighter near-term policy pricing'
      : 'consistent with easier near-term policy pricing';

  if (regime === 'Extreme') {
    return (
      `Implied policy rate is ${regime.toLowerCase()} ${direction} its trailing-year mean. ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${policyTilt}.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Implied policy rate is elevated vs. its trailing-year history. ` +
      `Recent move is ${policyTilt}.`
    );
  }
  return (
    `Implied policy rate is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + reference chips
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: PolicyFuturesPriceLevelOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = curveMetaFor(cm.curve_family);
  const rule = cm.inverse_priced
    ? 'Inverse-priced — implied_rate_pct = 100 − raw_price.'
    : 'Direct-priced — implied_rate_pct = raw_price.';
  return [
    {
      label: 'Strip slot',
      value: `${stripStemLabel(cm.curve_family, cm.strip_position)} (master stem) · strip position ${cm.strip_position}${meta ? ` · ${meta.longLabel}` : ''}`,
    },
    {
      label: 'Underlying contract',
      value:
        cm.underlying_contract_code != null
          ? `${cm.underlying_contract_code}${cm.security_name ? ` · ${cm.security_name}` : ''}${cm.expiry_date ? ` · expires ${cm.expiry_date}` : ''}`
          : '—',
    },
    {
      label: 'Quote convention',
      value: `${cm.quote_units} · ${rule}`,
    },
    {
      label: 'Short-rate regime',
      value: cm.short_rate_regime === 'RFR'
        ? 'RFR — compounded daily risk-free rate (SOFR / SONIA)'
        : cm.short_rate_regime === 'IBOR'
          ? 'IBOR — unsecured 3M term IBOR (Euribor)'
          : cm.short_rate_regime,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (price observation on the strip slot)`,
    },
    {
      label: 'Z-score model',
      value: '252d rolling window on the IMPLIED-RATE series (YAML-locked)',
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / mid / percentile, IMPLIED-RATE axis)',
    },
    {
      label: 'Contract spec',
      value:
        cm.contract_size != null || cm.tick_size != null || cm.tick_value != null
          ? [
              cm.contract_size != null ? `size ${cm.contract_size}` : null,
              cm.tick_size != null ? `tick ${cm.tick_size}` : null,
              cm.tick_value != null ? `tick val ${cm.tick_value}` : null,
            ]
              .filter((s): s is string => s != null)
              .join(' · ')
          : '—',
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
    { label: 'Catalog v2.1 PR14' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (so they have ONE import
// line from this helper file).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
