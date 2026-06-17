// ============================================================================
// forwardBreakevenShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for
// ``calculate_forward_breakeven_simple_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "forward bond-
// implied breakeven" is — a YEAR-WEIGHTED LINEAR forward between two spot
// breakeven pillars on a single same-country nominal/linker pair (e.g.
// UST/USD_TIPS 5Y5Y, FR_OAT/EUR_FR_LINKER 5Y10Y).  Inherits the spot
// breakeven primitive's no-proxy + same-country invariants transitively.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch
// the SAME typed-detail endpoint ``/api/v1/rates/detail/forward-breakeven``
// (per rendering_density.md §1.1 + §10 — both views consume the same
// bridge; the compact view just renders less of it).  KPI builders +
// formatting + tone logic + the inflation-compensation honesty caveat
// live here in ONE place to prevent drift across surfaces.
//
// Rolling-z-score conventions are YAML-locked on this primitive (mirrors
// the sibling breakeven-curve-spread / breakeven-butterfly).  Only
// ``lookback_days`` + ``field_name`` are exposed at the controls layer —
// no "Advanced" panel.  The forward window itself is configured via the
// (nominal_curve_family + linker_curve_family) pair + (start_tenor,
// end_tenor) tenor-pair mode (the canonical desk read: 5Y5Y, 5Y10Y,
// 2Y3Y, 10Y10Y).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailForwardBreakeven,
  type ForwardBreakevenDetailParams,
} from '@/services/ratesApi';
import type { ForwardBreakevenSimpleOutput } from '@/types/rates';
import {
  bucketForPercentile,
  countryCaveatFor,
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
// Country-pair metadata.  A bond-implied breakeven is a same-country pair
// (nominal sovereign + matching linker); a FORWARD breakeven inherits the
// same-country invariant from its two endpoint spot legs.  Keyed by the
// LINKER curve_family because the linker uniquely determines the valid
// nominal counterparty.
// ---------------------------------------------------------------------------

export interface BreakevenPairMeta {
  /** Linker curve_family (e.g. 'USD_TIPS'). */
  linkerFamily: string;
  /** Nominal sovereign curve_family (e.g. 'UST'). */
  nominalFamily: string;
  /** Country label (e.g. 'US'). */
  country: string;
  /** Short nominal label for the pair chip (e.g. 'UST'). */
  nominalShort: string;
  /** Short linker label for the pair chip (e.g. 'TIPS'). */
  linkerShort: string;
  /** Country flag emoji for the identity chip. */
  flag: string;
}

const PAIR_BY_LINKER: Record<string, BreakevenPairMeta> = {
  USD_TIPS: {
    linkerFamily: 'USD_TIPS',
    nominalFamily: 'UST',
    country: 'US',
    nominalShort: 'UST',
    linkerShort: 'TIPS',
    flag: '🇺🇸',
  },
  GBP_LINKER: {
    linkerFamily: 'GBP_LINKER',
    nominalFamily: 'UK_GILT',
    country: 'UK',
    nominalShort: 'Gilt',
    linkerShort: 'Linker',
    flag: '🇬🇧',
  },
  EUR_FR_LINKER: {
    linkerFamily: 'EUR_FR_LINKER',
    nominalFamily: 'FR_OAT',
    country: 'France',
    nominalShort: 'OAT',
    linkerShort: 'OATei',
    flag: '🇫🇷',
  },
  CAD_RRB: {
    linkerFamily: 'CAD_RRB',
    nominalFamily: 'CANADA_GOVT',
    country: 'Canada',
    nominalShort: 'Govt',
    linkerShort: 'RRB',
    flag: '🇨🇦',
  },
};

export function pairForLinker(linkerFamily: string): BreakevenPairMeta | null {
  return PAIR_BY_LINKER[linkerFamily] ?? null;
}

/** "Country Pair" dropdown options — keyed by linker family. */
export const FORWARD_BREAKEVEN_PAIR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(PAIR_BY_LINKER).map((p) => ({
  value: p.linkerFamily,
  label: `${p.country} · ${p.nominalShort} / ${p.linkerShort}`,
}));

// ---------------------------------------------------------------------------
// Forward-pair grid.  Maps canonical desk labels (5Y5Y, 5Y10Y, 2Y3Y, ...)
// to (start_tenor, end_tenor) pairs the backend's tenor-mode input accepts.
// Hand-curated to the V1 breakeven tenor universe (intersection of nominal
// + linker grids per same-country pair).  Per-pair availability is computed
// at the surface layer by intersecting against
// BREAKEVEN_TENOR_OPTIONS_BY_PAIR.
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

export const FORWARD_BREAKEVEN_PAIRS: ReadonlyArray<ForwardPair> = [
  {
    label: '5Y5Y',
    startTenor: '5Y',
    endTenor: '10Y',
    description: '5-year forward starting 5 years out — long-run inflation compensation anchor.',
  },
  {
    label: '5Y10Y',
    startTenor: '5Y',
    endTenor: '15Y',
    description: '10-year forward starting 5 years out — secular inflation-compensation read.',
  },
  {
    label: '10Y10Y',
    startTenor: '10Y',
    endTenor: '20Y',
    description: '10-year forward starting 10 years out — deep secular read.',
  },
  {
    label: '2Y3Y',
    startTenor: '2Y',
    endTenor: '5Y',
    description: '3-year forward starting 2 years out — near-front inflation-compensation read.',
  },
  {
    label: '5Y25Y',
    startTenor: '5Y',
    endTenor: '30Y',
    description: '25-year forward starting 5 years out — very-long-end read.',
  },
];

export const FORWARD_PAIR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  FORWARD_BREAKEVEN_PAIRS.map((p) => ({ value: p.label, label: p.label }));

export function forwardPairFor(label: string): ForwardPair | null {
  return FORWARD_BREAKEVEN_PAIRS.find((p) => p.label === label) ?? null;
}

/** Tenor sets per pair (intersection of the nominal + linker grids).  Used
 *  by the extended view to filter the FORWARD_PAIR_OPTIONS down to the
 *  pairs supported by the selected (nominal_curve_family + linker_curve_family). */
export const BREAKEVEN_TENORS_BY_PAIR: Record<string, ReadonlySet<string>> = {
  USD_TIPS: new Set(['5Y', '10Y', '20Y', '30Y']),
  GBP_LINKER: new Set(['2Y', '5Y', '10Y', '15Y', '20Y', '30Y']),
  EUR_FR_LINKER: new Set(['2Y', '5Y', '10Y', '15Y']),
  CAD_RRB: new Set(['5Y', '10Y', '30Y']),
};

/** Filter the forward-pair options down to pairs where BOTH start_tenor AND
 *  end_tenor are present in the same-country breakeven tenor grid. */
export function forwardPairOptionsForLinker(
  linkerFamily: string,
): ReadonlyArray<{ value: string; label: string }> {
  const tenors = BREAKEVEN_TENORS_BY_PAIR[linkerFamily];
  if (!tenors) return FORWARD_PAIR_OPTIONS;
  return FORWARD_BREAKEVEN_PAIRS
    .filter((p) => tenors.has(p.startTenor) && tenors.has(p.endTenor))
    .map((p) => ({ value: p.label, label: p.label }));
}

/** Desk-canonical honesty caveat for the compact card.  The forward
 *  primitive carries an additional caveat over the spot — the year-
 *  weighted construction compounds the spot mis-pricing across both
 *  pillars + forward leg.  Same for every country (it's a property of
 *  the forward breakeven construction, not the issuer). */
export const FORWARD_BREAKEVEN_COMPACT_CAVEAT =
  'Inflation compensation, not expected inflation.  Compounds × 2 pillars + forward.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseForwardBreakevenArgs {
  nominalCurveFamily: string;
  linkerCurveFamily: string;
  startTenor: string;
  endTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseForwardBreakevenResult {
  data: ForwardBreakevenSimpleOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-
 *  detail endpoint; re-fetches when any input changes. */
export function useForwardBreakeven(
  args: UseForwardBreakevenArgs,
): UseForwardBreakevenResult {
  const [data, setData] = useState<ForwardBreakevenSimpleOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: ForwardBreakevenDetailParams = {
    nominal_curve_family: args.nominalCurveFamily,
    linker_curve_family: args.linkerCurveFamily,
    start_tenor: args.startTenor,
    end_tenor: args.endTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (
      !args.nominalCurveFamily
      || !args.linkerCurveFamily
      || !args.startTenor
      || !args.endTenor
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailForwardBreakeven(params)
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
    args.nominalCurveFamily,
    args.linkerCurveFamily,
    args.startTenor,
    args.endTenor,
    args.lookbackDays,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Forward-pair label derived from start/end tenors (for use as the compact
// secondary chip when the URL carries the (start, end) pair without the
// canonical label).
// ---------------------------------------------------------------------------

export function forwardPairLabel(startTenor: string, endTenor: string): string {
  const match = FORWARD_BREAKEVEN_PAIRS.find(
    (p) => p.startTenor === startTenor && p.endTenor === endTenor,
  );
  if (match) return match.label;
  return `${startTenor}/${endTenor}`;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  docs_revamped/03_standards/rendering_density.md §2.2 + the mockup
 *  design at this module's mockups/Compact.png:
 *
 *    1. FORWARD BE       (signed bps, neutral tone, primary emphasis)
 *    2. 1D CHANGE        (signed bps + secondary % subtext,
 *                         toneForChange — POSITIVE = inflation-compensation
 *                         repricing higher)
 *    3. Z-SCORE (252D)   (signed value + regime caption, toneForZScore)
 *
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: ForwardBreakevenSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const zRegime = regimeForZScore(cm.current_z_score);
  const pairLabel = forwardPairLabel(cm.start_tenor, cm.end_tenor);
  return [
    {
      label: `FORWARD BE (${pairLabel})`,
      value: signedFixed(cm.forward_breakeven_bps, 0),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption:
        cm.forward_breakeven_pct != null
          ? `${unsignedFixed(cm.forward_breakeven_pct, 2)}%`
          : undefined,
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext:
        cm.daily_change_bps != null && cm.forward_breakeven_bps
          ? `(${signedFixed(
              (cm.daily_change_bps / Math.abs(cm.forward_breakeven_bps)) * 100,
              2,
            )}%)`
          : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zRegime,
    },
  ];
}

/** The extended view's FULL KPI strip.  Order matches the mockup design
 *  at this module's mockups/Extended.png (9-cell strip: FORWARD BE,
 *  1D / 5D / 1M CHANGE, Z-SCORE, PERCENTILE, 252D HIGH / LOW,
 *  OBSERVATIONS).  All fields exposed on the backend wire today. */
export function extendedKPIs(
  data: ForwardBreakevenSimpleOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'FORWARD BE',
      value: signedFixed(cm.forward_breakeven_bps, 0),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: '5D CHANGE',
      value: signedFixed(cm.weekly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.weekly_change_bps),
    },
    {
      label: '1M CHANGE',
      value: signedFixed(cm.monthly_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.monthly_change_bps),
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
      value: signedFixed(cm.high_252d_bps, 0),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 0),
      unit: 'bp',
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
// Reference-band builder — ±2σ / ±1.5σ envelope on the forward breakeven (bps).
// ---------------------------------------------------------------------------

// Sanity bound for forward bond-implied breakevens (bps).  Year-weighted
// forwards can amplify pillar outliers; across the playbook universe
// forwards sit roughly [-300, +900] bps with stress spikes (Q4 2022 5Y5Y
// regime).  Anything well outside is almost certainly a generic-ticker
// roll artifact on one of the four underlying series.  Defensive frontend
// safety net; the real fix lives in the data pipeline.
const FORWARD_BREAKEVEN_SANITY_MIN_BPS = -400;
const FORWARD_BREAKEVEN_SANITY_MAX_BPS = 1000;

export function sanitiseForwardBreakevenSeries(
  rows: ReadonlyArray<{ date: string; forward_breakeven_bps: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.forward_breakeven_bps == null
        || Number.isNaN(r.forward_breakeven_bps)
        || r.forward_breakeven_bps < FORWARD_BREAKEVEN_SANITY_MIN_BPS
        || r.forward_breakeven_bps > FORWARD_BREAKEVEN_SANITY_MAX_BPS
        ? null
        : r.forward_breakeven_bps,
  }));
}

export function buildReferenceBands(
  data: ForwardBreakevenSimpleOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseForwardBreakevenSeries(data.time_series ?? []);
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
  data: ForwardBreakevenSimpleOutput,
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
      ? 'consistent with higher long-horizon inflation-compensation pricing'
      : 'consistent with lower long-horizon inflation-compensation pricing';

  if (regime === 'Extreme') {
    return (
      `Forward breakeven is ${regime.toLowerCase()} ${direction} its trailing-year mean.  ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${directionPhrase}.  Remember this is forward inflation compensation ` +
      `(not a clean forward expected-inflation read; carries IRP + liquidity premia ` +
      `at both pillars).`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Forward breakeven is elevated vs. its trailing-year history.  ` +
      `The recent move is ${directionPhrase}.  Forward inflation compensation, ` +
      `not expected inflation.`
    );
  }
  return (
    `Forward breakeven is within its trailing-year norm; ` +
    `no extreme stretch in either direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows — methodology_label sourced from the wire (FM10 / P5).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: ForwardBreakevenSimpleOutput,
  effectiveFieldName: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const linkerCaveat = countryCaveatFor(cm.linker_curve_family);
  const pair = pairForLinker(cm.linker_curve_family);
  return [
    {
      label: 'Series',
      value: `${cm.forward_window_label} (${cm.start_tenor} start, ${cm.end_tenor} end)`,
    },
    {
      label: 'Forward formula',
      value:
        'Year-weighted linear: '
        + 'forward_bps = (BE_long·T_long − BE_short·T_short) / (T_long − T_short)',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid yield-to-maturity, all four legs)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window, ddof 1 (YAML-locked)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, bps)',
    },
    {
      label: 'Composition',
      value:
        cm.start_breakeven_bps != null && cm.end_breakeven_bps != null
          ? `Start BE ${signedFixedWithUnit(cm.start_breakeven_bps, 0, 'bp')} · `
            + `End BE ${signedFixedWithUnit(cm.end_breakeven_bps, 0, 'bp')} · `
            + `Years ${unsignedFixed(cm.start_years, 1)} → ${unsignedFixed(cm.end_years, 1)}`
          : '—',
    },
    {
      label: 'Disclosure',
      value: cm.methodology_label || (
        'Forward inflation COMPENSATION, NOT a clean forward expected-inflation '
        + 'read — both pillars carry inflation risk premium + liquidity premium.'
      ),
    },
    {
      label: 'Same-country pair',
      value: pair ? `${pair.country} (${pair.nominalShort} / ${pair.linkerShort})` : '—',
    },
    {
      label: 'Linker caveat',
      value: linkerCaveat ? linkerCaveat.caveat : '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'US TreasuryDirect' },
    { label: 'UK DMO' },
    { label: 'Agence France Trésor' },
    { label: 'Bank of Canada' },
  ];
}

// ---------------------------------------------------------------------------
// Re-export helpers per-tool wrappers also need (single import line).
// ---------------------------------------------------------------------------

export { signedFixedWithUnit, unsignedFixed };
