// ============================================================================
// crossMarketOisShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for
// ``calculate_ois_cross_market_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "cross-market OIS
// spread" is — a SAME-TENOR difference between two DISTINCT OIS curve
// families (e.g. USD_SOFR_OIS 2Y minus EUR_ESTR_OIS 2Y).  Each curve prices
// its own central-bank's expected policy path under the risk-neutral
// measure; the spread is the canonical G4 read on RELATIVE central-bank
// stance — Fed vs ECB, Fed vs BoE, BoE vs ECB, etc.  Each leg references a
// DIFFERENT overnight rate index (SOFR / ESTR / SONIA / TONA / AONIA /
// CORRA) which are not fungible policy benchmarks; the spread therefore
// captures cross-currency POLICY-PATH divergence, NOT pure rate-level
// arbitrage.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch the
// SAME typed-detail endpoint ``/api/v1/rates/detail/ois-cross-market-spread``
// (per rendering_density.md §1.1 + §10 — both views consume the same bridge;
// the compact view just renders less of it).  KPI builders + formatting +
// tone logic + the policy-path-divergence caveat live here in ONE place to
// prevent drift across surfaces.
//
// NB on backend Output shape — distinct from the linker / sovereign / ZCIS
// cross-market siblings the backend Output does NOT carry
// ``methodology_label`` or ``observation_count`` — instead the per-tool
// disclosure string is sourced from this file with an explicit ``// TODO``
// marker for when the OIS sub-domain catches up to PR10 (then the
// methodology card switches to consume the wire — one-line edit).  Mirrors
// the OIS curve_spread / butterfly siblings.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOisCrossMarketSpread,
  type OisCrossMarketSpreadDetailParams,
} from '@/services/ratesApi';
import type { OisCrossMarketSpreadOutput } from '@/types/rates';
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
// OIS curve-family metadata.  Cross-market OIS spreads are formed from two
// DIFFERENT OIS curve families at the same tenor; this registry lets the
// surfaces render concise pair chips (e.g. "USD-EUR · SOFR-ESTR · Fed vs ECB")
// without re-doing the per-leg metadata lookup.  Curve families enumerated
// mirror the backend's closed ``OIS_CURVE_FAMILY`` enum sourced from
// rates_agent/playbooks/ois.yml — kept in sync with the same-curve OIS sibling
// modules (curve_spread / butterfly / rate_level / forward_rate).
// Finance-aware → lives in this per-tool layer.
// ---------------------------------------------------------------------------

export interface OisFamilyMeta {
  /** Curve family identifier (e.g. 'USD_SOFR_OIS'). */
  family: string;
  /** Market short code used on the wire (e.g. 'USD'). */
  marketShort: string;
  /** Short overnight-index label (e.g. 'SOFR', 'ESTR', 'SONIA'). */
  indexShort: string;
  /** Central-bank short label (e.g. 'Fed', 'ECB', 'BoE'). */
  centralBank: string;
  /** Country flag emoji for the identity chip + caveat footer. */
  flag: string;
}

const FAMILY_REGISTRY: Record<string, OisFamilyMeta> = {
  USD_SOFR_OIS: {
    family: 'USD_SOFR_OIS',
    marketShort: 'USD',
    indexShort: 'SOFR',
    centralBank: 'Fed',
    flag: '🇺🇸',
  },
  EUR_ESTR_OIS: {
    family: 'EUR_ESTR_OIS',
    marketShort: 'EUR',
    indexShort: 'ESTR',
    centralBank: 'ECB',
    flag: '🇪🇺',
  },
  GBP_SONIA_OIS: {
    family: 'GBP_SONIA_OIS',
    marketShort: 'GBP',
    indexShort: 'SONIA',
    centralBank: 'BoE',
    flag: '🇬🇧',
  },
  JPY_OIS: {
    family: 'JPY_OIS',
    marketShort: 'JPY',
    indexShort: 'TONA',
    centralBank: 'BoJ',
    flag: '🇯🇵',
  },
  AUD_OIS: {
    family: 'AUD_OIS',
    marketShort: 'AUD',
    indexShort: 'AONIA',
    centralBank: 'RBA',
    flag: '🇦🇺',
  },
  CAD_OIS: {
    family: 'CAD_OIS',
    marketShort: 'CAD',
    indexShort: 'CORRA',
    centralBank: 'BoC',
    flag: '🇨🇦',
  },
};

/** Resolve the per-family meta.  Returns null for an unknown family (caller
 *  renders a neutral fallback). */
export function oisFamilyFor(family: string): OisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** Ordered list of OIS curve families exposed on the controls strip. */
export const OIS_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.marketShort} · ${m.indexShort}`,
  }));

/** Tenor pillars on the ingested OIS grid shared across families.  Distinct
 *  from per-curve grids the same-curve OIS curve_spread surfaces use — a
 *  cross-market spread requires the SAME tenor on BOTH curves so the universe
 *  is the intersection of the per-curve grids.  This conservative grid (the
 *  short end through 30Y) is the intersection used in practice. */
export const OIS_CROSS_MARKET_TENOR_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = ['3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'].map((t) => ({
  value: t,
  label: t,
}));

/** Short pair label (e.g. "USD-EUR") used in identity rows + chips. */
export function pairShortLabel(legA: string, legB: string): string {
  const a = oisFamilyFor(legA)?.marketShort ?? legA;
  const b = oisFamilyFor(legB)?.marketShort ?? legB;
  return `${a}-${b}`;
}

/** Overnight-index pair label (e.g. "SOFR-ESTR") used in the title row. */
export function indexPairShortLabel(legA: string, legB: string): string {
  const a = oisFamilyFor(legA)?.indexShort ?? legA;
  const b = oisFamilyFor(legB)?.indexShort ?? legB;
  return `${a}-${b}`;
}

/** Central-bank pair caption (e.g. "Fed vs ECB policy-rate differential").
 *  Mirrors the mockup's compact identity subtitle (Compact.png). */
export function pairCentralBankCaption(legA: string, legB: string): string {
  const a = oisFamilyFor(legA);
  const b = oisFamilyFor(legB);
  if (a && b) return `${a.centralBank} vs ${b.centralBank} policy-rate differential`;
  return 'Cross-central-bank policy-rate differential';
}

/** Extended-view identity subtitle: per-leg curve+tenor decomposition (e.g.
 *  "USD_SOFR_OIS 2Y vs EUR_ESTR_OIS 2Y"). */
export function pairSubtitle(
  legA: string,
  legB: string,
  tenor: string,
): string {
  return `${legA} ${tenor} vs ${legB} ${tenor}`;
}

/** Compact-view caveat one-liner (e.g. "Cross-central-bank policy
 *  divergence. Risk-neutral OIS pricing.") — the load-bearing methodology
 *  caveat surfaced inline in the compact footer + the extended view's
 *  methodology card.  OIS curves price the EXPECTED POLICY PATH under the
 *  risk-neutral measure; a cross-market OIS spread is the difference in
 *  those expected paths — RELATIVE central-bank stance, NOT a forecast of
 *  realised central-bank decisions.  Each currency's curve references its
 *  own overnight index family (SOFR / ESTR / SONIA / TONA / AONIA / CORRA);
 *  these are structurally distinct policy benchmarks.  Mockup-faithful
 *  one-liner.
 *
 *  TODO(PR10): when the backend ships
 *  ``current_metrics.methodology_label`` (today the OIS sub-domain Output
 *  schemas lack it — siblings in inflation_indexed_bonds / inflation_swaps
 *  already carry it), switch the methodology card's "Disclosure" row to
 *  source from the wire.  Tracked alongside the OIS sub-domain's PR10
 *  backlog (same marker as OIS curve_spread / butterfly / rate_level /
 *  forward_rate). */
export const OIS_CROSS_MARKET_COMPACT_CAVEAT =
  'Cross-central-bank policy divergence. Risk-neutral OIS pricing.';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseCrossMarketOisArgs {
  curveFamily1: string;
  curveFamily2: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseCrossMarketOisResult {
  data: OisCrossMarketSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the ZCIS
 *  cross-market hook shape so the per-tool surfaces look the same shape
 *  file-for-file. */
export function useCrossMarketOisSpread(
  args: UseCrossMarketOisArgs,
): UseCrossMarketOisResult {
  const [data, setData] = useState<OisCrossMarketSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OisCrossMarketSpreadDetailParams = {
    curve_family_1: args.curveFamily1,
    curve_family_2: args.curveFamily2,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
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
          ? 'Cross-market OIS requires two distinct curve families.'
          : null,
      );
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailOisCrossMarketSpread(params)
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
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Premium-direction caption (e.g. "Fed Premium" / "ECB Premium" / "Parity") —
// mockup compact KPI caption next to the headline SPREAD.  POSITIVE spread =
// curve_family_1's policy path priced ABOVE curve_family_2's → curve_family_1's
// central bank carries a policy premium.
// ---------------------------------------------------------------------------

export function premiumDirectionCaption(
  spreadBps: number | null | undefined,
  legA: string,
  legB: string,
): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  if (spreadBps === 0) return 'Parity';
  const a = oisFamilyFor(legA);
  const b = oisFamilyFor(legB);
  if (spreadBps > 0) return `${a?.centralBank ?? 'Leg A'} Premium`;
  return `${b?.centralBank ?? 'Leg B'} Premium`;
}

// ---------------------------------------------------------------------------
// Wire-derived helper — observation_count is computed CLIENT-SIDE from
// time_series_spread.rows because the OIS cross-market Output is leaner than
// the linker / sovereign / ZCIS cross-market siblings (no wire
// observation_count field — same shape as OIS curve_spread).
// ---------------------------------------------------------------------------

function spreadValues(data: OisCrossMarketSpreadOutput): number[] {
  const rows = data.time_series_spread?.rows ?? [];
  return rows
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
}

/** Total observation count in the displayed window (mockup KPI strip). */
export function observationCount(data: OisCrossMarketSpreadOutput): number {
  return spreadValues(data).length;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (TENOR)   — signed bps, primary emphasis with central-bank
 *                          premium caption ("Fed Premium" / "ECB Premium").
 *                          Subtext shows daily-change %; mockup shows "+175 bp"
 *                          headline.
 *    2. 1D CHANGE        — signed bps, toneForChange.  Subtext: daily change
 *                          as %.
 *    3. Z-SCORE (252D)   — signed value + regime caption (Normal / Elevated /
 *                          Extreme), tone-coloured.
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: OisCrossMarketSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const dailyPct =
    cm.daily_change_bps != null ? cm.daily_change_bps / 100 : null;
  return [
    {
      label: `SPREAD (${cm.tenor})`,
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: premiumDirectionCaption(
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
      subtext:
        dailyPct != null ? `(${signedFixed(dailyPct, 2)}%)` : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: regimeForZScore(cm.current_z_score),
    },
  ];
}

/** Extended view's FULL KPI strip (mockups/Extended.png — bps-scale strip +
 *  per-leg PERCENT decomposition + range / percentile / window /
 *  observation_count).  observation_count is computed CLIENT-SIDE from the
 *  canonical bps series (the OIS cross-market wire Output does not carry it). */
export function extendedKPIs(
  data: OisCrossMarketSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const obs = observationCount(data);
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: premiumDirectionCaption(
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
      label: 'OBSERVATIONS',
      value: obs > 0 ? `${obs}` : '—',
      tone: 'neutral',
    },
    {
      label: 'LEG A',
      value: signedFixed(cm.curve_family_1_rate, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'LEG B',
      value: signedFixed(cm.curve_family_2_rate, 2),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for OIS cross-market spreads (bps).  Across the G4 universe
// cross-currency OIS spreads at common tenors cluster within ±400 bps; stress
// excursions to ±600 bps are plausible (e.g. SOFR-TONA at policy-divergence
// extremes).  Anything well outside is almost certainly a generic-ticker roll
// artifact on one leg.  Mirrors the OIS curve_spread / cross-market ZCIS sanity
// bounds.
const OIS_CROSS_MARKET_SPREAD_SANITY_MIN_BPS = -800;
const OIS_CROSS_MARKET_SPREAD_SANITY_MAX_BPS = 800;

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
        || r.value < OIS_CROSS_MARKET_SPREAD_SANITY_MIN_BPS
        || r.value > OIS_CROSS_MARKET_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: OisCrossMarketSpreadOutput,
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
  data: OisCrossMarketSpreadOutput,
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
  legA: string,
  legB: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const a = oisFamilyFor(legA);
  const b = oisFamilyFor(legB);
  const pair = pairShortLabel(legA, legB);
  const banks = a && b ? `${a.centralBank} vs ${b.centralBank}` : pair;
  const policyLine =
    ' OIS curves price the expected policy path under the risk-neutral measure'
    + ' — the spread captures RELATIVE central-bank stance, NOT a forecast of'
    + ' realised central-bank decisions.';

  if (regime === 'Extreme') {
    return (
      `${pair} OIS spread (${banks}) is extreme ${direction} versus its `
      + `trailing-year mean — sits in the ${bucket.toLowerCase()}-end of the `
      + '252d range.' + policyLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${pair} OIS spread (${banks}) is elevated ${direction} versus its `
      + 'trailing-year history.' + policyLine
    );
  }
  return (
    `${pair} OIS spread (${banks}) is within its trailing-year norm; no `
    + 'extreme stretch in either direction.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty
 *  disclosure should flow from ``current_metrics.methodology_label``; the
 *  OIS cross-market spread backend Output currently does NOT carry that
 *  field (the sub-domain hasn't caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds / inflation_swaps already do).  Until the backend
 *  ships it, the "Disclosure" row sources from the per-tool canonical
 *  caveat above (one-line edit to switch when the wire lands the field —
 *  same marker as OIS curve_spread / butterfly / rate_level / forward_rate). */
export function buildMethodologyRows(
  data: OisCrossMarketSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const a = oisFamilyFor(cm.curve_family_1);
  const b = oisFamilyFor(cm.curve_family_2);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = (${cm.curve_family_1} ${cm.tenor} − ${cm.curve_family_2} `
        + `${cm.tenor}) × 100 — raw OIS par-rate space, no basis subtraction, `
        + 'no convexity adjustment, no fitted curve',
    },
    {
      label: 'Sign convention',
      value:
        'curve_family_1 − curve_family_2 (left minus right). POSITIVE = leg-A'
        + ' policy path priced ABOVE leg-B (premium); NEGATIVE = leg-B premium.',
    },
    {
      label: 'Tenor',
      value: `${cm.tenor} (single pillar shared by both legs)`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (OIS quoted rate, both endpoint series)`,
    },
    {
      label: 'Alignment',
      value:
        'Strict pandas inner-join on trade_date after independent per-leg'
        + ' compute — no synthetic spread points on holiday-asymmetric dates.',
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
      value: '252 trading days (high / low / percentile, bps)',
    },
    {
      label: 'Overnight indices',
      value:
        a && b
          ? `${a.indexShort} (leg A · ${a.centralBank}) vs ${b.indexShort} (leg B · ${b.centralBank})`
          : `${cm.curve_family_1} (leg A) vs ${cm.curve_family_2} (leg B)`,
    },
    {
      label: 'Units',
      value:
        'Spread + changes + 252d range in BPS (OIS sub-domain bps convention);'
        + ' per-leg endpoint rates in PERCENT.',
    },
    {
      label: 'Cross-curve invariant',
      value:
        'curve_family_1 != curve_family_2 — same-curve tenor spreads are forbidden'
        + ' at the input schema layer (would compose on calculate_ois_curve_spread).',
    },
    {
      label: 'Disclosure',
      value: OIS_CROSS_MARKET_COMPACT_CAVEAT,
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
