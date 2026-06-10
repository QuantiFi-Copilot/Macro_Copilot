// ============================================================================
// crossMarketSpreadShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the existing Monitor widgets for
// ``calculate_cross_market_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "cross-market
// sovereign tenor spread" is — same tenor on two DIFFERENT sovereign yield
// curves (e.g. UST 10Y − Bund 10Y, BTP 10Y − Bund 10Y).  The wire ships the
// spread + 1d/1w/1m changes ALREADY IN BPS; per-leg endpoint yields ship in
// PERCENT.  Cross-curve invariant — curve_family_1 != curve_family_2 — is
// enforced at the schema layer; the surfaces snap the second dropdown when
// the user picks a colliding family.
//
// The Build views fetch the SAME typed-detail endpoint
// ``/api/v1/rates/detail/cross-market`` as the parameterised Monitor widget
// (per methodology_exposure.md §5 + rendering_density.md §1.1 — both views
// consume the same bridge; the compact view just renders less of it).  KPI
// builders, formatting, tone logic, and the canonical sovereign-divergence
// caveat live here in ONE place to prevent drift across surfaces.
//
// NB on backend Output shape — the sovereign cross_market_spread Output
// carries ``observation_count`` only via ``time_series.length`` (the wire
// does not ship a separate count field).  Other "trailing" stats
// (``percentile_252d`` / ``high_252d_bps`` / ``low_252d_bps`` /
// ``weekly_change_bps`` / ``monthly_change_bps``) ARE on the wire.  The
// methodology card sources the canonical caveat from this file with a
// ``TODO(PR10)`` marker for when the sovereign Output ships
// ``current_metrics.methodology_label`` (mirrors the sibling sovereign
// curve_spread / yield_levels / butterfly bridges — the sub-domain has not
// caught up to PR10 yet, but its inflation_indexed_bonds / inflation_swaps
// siblings already do).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailCrossMarket,
  type CrossMarketDetailParams,
} from '@/services/ratesApi';
import type { CrossMarketSpreadOutput } from '@/types/rates';
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
// Sovereign curve-family metadata.  Cross-market spreads are TWO-FAMILY
// objects at a SINGLE tenor; the registry keys off curve_family so the
// per-leg identity (UST / Bund / Gilt / JGB / OAT / BTP / Bono / AUS / CAN)
// is consistent across the Build views AND the legacy Monitor widgets'
// CURVE_LABEL map.  Kept in ONE place so a new sovereign curve is a
// one-line addition here, not three.
// ---------------------------------------------------------------------------

export interface SovereignFamilyMeta {
  /** Curve family identifier (e.g. 'UST'). */
  family: string;
  /** Short market label used in identity chips (e.g. 'UST', 'Bund'). */
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

/** All registered sovereign curve families, as the controls-strip
 *  "Curve A" / "Curve B" dropdown options.  Mirrors the existing
 *  CURVE_OPTIONS in @/lib/monitorParamOptions but with a per-tool label
 *  format (short · long) that the Build identity bar reads naturally. */
export const SOVEREIGN_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Tenor grid for the cross-market spread tool — the standard pillars
 *  (2/3/5/7/10/20/30) plus a Treasury-only 1Y.  Surfacing a broad-but-
 *  conservative list keeps the Tenor dropdown family-agnostic; the
 *  backend returns an empty series if a family doesn't carry the
 *  chosen tenor. */
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

/** Short pair label (e.g. "UST-Bund").  Used in identity rows + chart
 *  series labels.  Pulls from the family registry so renaming a family
 *  ripples through the surfaces. */
export function pairShortLabel(cf1: string, cf2: string): string {
  const a = sovereignFamilyFor(cf1)?.shortLabel ?? cf1;
  const b = sovereignFamilyFor(cf2)?.shortLabel ?? cf2;
  return `${a}-${b}`;
}

/** Pair subtitle (e.g. "UST_10Y − DE_BUND_10Y"). */
export function pairSubtitle(cf1: string, cf2: string, tenor: string): string {
  return `${cf1}_${tenor} − ${cf2}_${tenor}`;
}

/** Long descriptive subtitle (e.g. "US Treasuries 10Y minus German Bunds 10Y
 *  — Transatlantic sovereign-rate differential"). */
export function pairLongSubtitle(
  cf1: string,
  cf2: string,
  tenor: string,
): string {
  const a = sovereignFamilyFor(cf1);
  const b = sovereignFamilyFor(cf2);
  if (a && b) {
    return `${tenor} ${a.longLabel} yield minus ${tenor} ${b.longLabel} yield`;
  }
  return `${tenor} ${cf1} yield minus ${tenor} ${cf2} yield`;
}

/** Sign-convention caption rendered next to the SPREAD KPI:
 *    positive → "<cf1_short> Premium" (curve A trades CHEAP to curve B)
 *    negative → "<cf2_short> Premium" (curve B trades CHEAP to curve A)
 *  Reads as "X yields more than Y" — the standard desk shorthand for a
 *  cross-market spread sign.  Mockup-faithful (Compact shows "US Premium"
 *  at +182 bp UST-Bund). */
export function premiumCaption(
  spreadBps: number | null | undefined,
  cf1: string,
  cf2: string,
): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  const a = sovereignFamilyFor(cf1)?.shortLabel ?? cf1;
  const b = sovereignFamilyFor(cf2)?.shortLabel ?? cf2;
  if (spreadBps > 0) return `${a} Premium`;
  if (spreadBps < 0) return `${b} Premium`;
  return 'Parity';
}

/** Z-score regime caption (Normal / Elevated / Extreme).  Mirror of the
 *  sovereign curve_spread sibling shape; tone is handled separately via
 *  toneForZScore. */
export function zScoreRegimeCaption(
  z: number | null | undefined,
): string {
  return regimeForZScore(z);
}

/** The desk-canonical honesty caveat for cross-market sovereign spreads,
 *  surfaced in the compact view's footer + the extended view's methodology
 *  card.  Sovereign-vs-sovereign differentials at a matched tenor blend
 *  expected-policy-rate divergence, term-premium divergence, credit /
 *  sovereign-risk divergence, and supply-demand technicals — they are NOT
 *  a clean policy-divergence read (use ois_cross_market_spread for that).
 *
 *  TODO(PR10): when the sovereign sub-domain ships
 *  ``current_metrics.methodology_label`` (the inflation_indexed_bonds /
 *  inflation_swaps siblings already carry it), switch the methodology
 *  card's "Disclosure" row + the compact footer to source from the wire.
 *  One-line edit. */
export const CROSS_MARKET_COMPACT_CAVEAT =
  'Sovereign divergence: policy + term premium + credit/supply.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCrossMarketSpreadArgs {
  curveFamily1: string;
  curveFamily2: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseCrossMarketSpreadResult {
  data: CrossMarketSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Refuses an invalid
 *  same-family pair locally so the schema's ``_curves_must_differ``
 *  validator doesn't see traffic on the boundary case. */
export function useCrossMarketSpread(
  args: UseCrossMarketSpreadArgs,
): UseCrossMarketSpreadResult {
  const [data, setData] = useState<CrossMarketSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: CrossMarketDetailParams = {
    curve_family_1: args.curveFamily1,
    curve_family_2: args.curveFamily2,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (
      !args.curveFamily1
      || !args.curveFamily2
      || args.curveFamily1 === args.curveFamily2
      || !args.tenor
    ) {
      setData(null);
      setErrorMessage(
        args.curveFamily1 && args.curveFamily1 === args.curveFamily2
          ? 'Cross-market spread requires two distinct curve families.'
          : null,
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailCrossMarket(params)
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
    args.curveFamily1,
    args.curveFamily2,
    args.tenor,
    args.lookbackDays,
    args.fieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Chart-point helpers.  Prefer the canonical TimeSeries (BPS) when present;
// fall back to the wire-frozen ``time_series`` row shape.  Both surfaces +
// the parameterised monitor widget share these helpers.
// ---------------------------------------------------------------------------

export interface SpreadChartRow {
  date: string;
  value: number | null;
}

export function spreadChartRows(
  data: CrossMarketSpreadOutput,
): SpreadChartRow[] {
  const canonical = data.time_series_spread?.rows;
  if (canonical && canonical.length > 0) {
    return canonical.map((r) => ({ date: r.date, value: r.value }));
  }
  return (data.time_series ?? []).map((r) => ({
    date: r.date,
    value: r.spread_bps ?? null,
  }));
}

// Sanity bounds for cross-market sovereign spreads (bps).  Across the
// playbook universe (BTP-Bund peaked ~570 bps in 2011/2012; UST-JGB
// ~470 bps in 2023) the spread sits within ±800 bps with stress
// excursions to ±1000.  Anything well outside is almost certainly a
// generic-ticker roll artifact on one leg.
const CROSS_MARKET_SPREAD_SANITY_MIN_BPS = -1200;
const CROSS_MARKET_SPREAD_SANITY_MAX_BPS = 1200;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<SpreadChartRow>,
): Array<SpreadChartRow> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < CROSS_MARKET_SPREAD_SANITY_MIN_BPS
        || r.value > CROSS_MARKET_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** Compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (<tenor>) — signed bps, primary emphasis; the cross-
 *                          market value.  "<cf1> Premium" / "<cf2>
 *                          Premium" caption encodes the sign in one
 *                          word.
 *    2. 1D CHANGE (bps)  — signed bps, toneForChange; percent-of-cf1
 *                          subtext when both legs are present.
 *    3. Z-SCORE (252D)   — signed value + Normal / Elevated / Extreme
 *                          caption, toneForZScore.
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: CrossMarketSpreadOutput,
  tenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  // Percent-of-cf1 subtext (mockup: "(+3.29%)" under +5.8bp at cf1=UST
  // yield ~4.27% — cf1 is the numerator-leg yield).
  const cf1Yield = cm.curve_family_1_yield;
  const dailyPctOfCf1 =
    cm.daily_change_bps != null && cf1Yield != null && cf1Yield !== 0
      ? `(${signedFixed(cm.daily_change_bps / (cf1Yield * 100), 2)}%)`
      : undefined;
  return [
    {
      label: `SPREAD (${tenor})`,
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: premiumCaption(
        cm.current_spread_bps,
        cm.curve_family_1,
        cm.curve_family_2,
      ),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
      subtext: dailyPctOfCf1,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreRegimeCaption(cm.current_z_score),
    },
  ];
}

/** Extended view's FULL KPI strip (mockups/Extended.png — bps-scale strip:
 *  SPREAD + 1d/1w/1m changes + z-score + percentile + 252d range +
 *  observation count). */
export function extendedKPIs(
  data: CrossMarketSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const obs = data.time_series?.length ?? 0;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: premiumCaption(
        cm.current_spread_bps,
        cm.curve_family_1,
        cm.curve_family_2,
      ),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: '1W CHANGE',
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
      caption: zScoreRegimeCaption(cm.current_z_score),
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
      value: signedFixed(cm.high_252d_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 1),
      unit: 'bp',
      tone: 'neutral',
    },
    {
      label: 'OBS',
      value: obs > 0 ? String(obs) : '—',
      tone: 'neutral',
    },
  ];
}

/** Two-leg decomposition row — cf1 + cf2 endpoint yields (PERCENT) so the
 *  desk can audit ``cf1 − cf2`` on the same screen.  Mockup
 *  Extended.png shows "UST 10Y = 4.2575%" and "Bund 10Y = 2.4375%". */
export function decompositionKPIs(
  data: CrossMarketSpreadOutput,
  tenor: string,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const aMeta = sovereignFamilyFor(cm.curve_family_1);
  const bMeta = sovereignFamilyFor(cm.curve_family_2);
  const aLabel = aMeta?.shortLabel ?? cm.curve_family_1;
  const bLabel = bMeta?.shortLabel ?? cm.curve_family_2;
  return [
    {
      label: `${aLabel} ${tenor}`,
      value: signedFixed(cm.curve_family_1_yield, 4),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `${bLabel} ${tenor}`,
      value: signedFixed(cm.curve_family_2_yield, 4),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference bands — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

export function buildReferenceBands(
  data: CrossMarketSpreadOutput,
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
  data: CrossMarketSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.curve_family_1,
    cm.curve_family_2,
    cm.tenor,
  );

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
  cf1: string,
  cf2: string,
  tenor: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const pair = pairShortLabel(cf1, cf2);
  const cavLine =
    ' Sovereign divergence mixes expected-policy + term-premium + credit '
    + 'and supply technicals; isolate the policy leg with the OIS '
    + 'cross_market_spread sibling.';
  if (regime === 'Extreme') {
    return (
      `${pair} ${tenor} spread is extreme ${direction} versus its trailing-`
      + `year mean — sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + cavLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${pair} ${tenor} spread is elevated ${direction} versus trailing-year `
      + `history.${cavLine}`
    );
  }
  return (
    `${pair} ${tenor} spread is within its trailing-year norm; no extreme `
    + 'widening or tightening stretch.'
  );
}

// ---------------------------------------------------------------------------
// Methodology card rows + reference chips
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + request context.
 *  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty disclosure should
 *  flow from ``current_metrics.methodology_label``; the sovereign
 *  cross_market_spread backend Output currently does NOT carry that field
 *  (the sub-domain hasn't caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds / inflation_swaps already do).  Until the
 *  backend ships it, the "Disclosure" row sources from the per-tool
 *  canonical caveat below (one-line edit to switch when the wire lands
 *  the field). */
export function buildMethodologyRows(
  data: CrossMarketSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const aMeta = sovereignFamilyFor(cm.curve_family_1);
  const bMeta = sovereignFamilyFor(cm.curve_family_2);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = (${cm.curve_family_1}_${cm.tenor}_yield − `
        + `${cm.curve_family_2}_${cm.tenor}_yield) × 100; same-tenor `
        + 'cross-curve differential in raw yield space (no basis '
        + 'subtraction, no convexity adjustment, no fitted curve)',
    },
    {
      label: 'Sign convention',
      value:
        `cf1 − cf2 (${cm.curve_family_1} minus ${cm.curve_family_2}); `
        + 'POSITIVE = cf1 trades CHEAP to cf2 (cf1 Premium)',
    },
    {
      label: 'Cross-curve invariant',
      value:
        'curve_family_1 != curve_family_2 — enforced at the input + compute '
        + 'layers (Pydantic _curves_must_differ validator).  Same-curve '
        + 'tenor spreads belong to calculate_curve_spread_tool.',
    },
    {
      label: 'Pair',
      value: `${cm.spread_label} · ${cm.tenor}`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (Bloomberg mid-yield, both endpoint series)`,
    },
    {
      label: 'Alignment',
      value:
        'Inner-join on trade_date after independent per-leg fetch + holiday '
        + 'ffill — no synthetic spread points.  NY-Fed trading-day calendar.',
    },
    {
      label: 'Z-score model',
      value:
        `${cm.rolling_window_days}d rolling window on the bps spread series `
        + '(YAML-locked — no input-layer override on this primitive)',
    },
    {
      label: 'Window',
      value: `${effectiveLookbackDays}d displayed history (lookback_days)`,
    },
    {
      label: 'Trailing range',
      value:
        '252 trading days (high / low / percentile, bps).  See planned-'
        + 'extension on configurable trailing window.',
    },
    {
      label: 'Sovereign curves',
      value: aMeta && bMeta
        ? `${aMeta.shortLabel} (${aMeta.longLabel}) vs ${bMeta.shortLabel} (${bMeta.longLabel})`
        : `${cm.curve_family_1} vs ${cm.curve_family_2}`,
    },
    {
      label: 'Units',
      value:
        'Spread + changes + 252d range in BPS; per-leg endpoint yields in '
        + 'PERCENT.',
    },
    {
      label: 'Disclosure',
      value: CROSS_MARKET_COMPACT_CAVEAT,
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
