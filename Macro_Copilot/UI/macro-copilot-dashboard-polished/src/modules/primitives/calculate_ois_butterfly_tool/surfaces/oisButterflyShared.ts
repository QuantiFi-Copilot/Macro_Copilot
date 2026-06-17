// ============================================================================
// oisButterflyShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for ``calculate_ois_butterfly_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "same-curve OIS
// butterfly" is — a 3-point curvature on a SINGLE OIS par-swap curve family.
// Sign convention: POSITIVE = belly CHEAP versus the linearly interpolated
// wings; NEGATIVE = belly RICH.  The wire ships the butterfly + 1d change +
// 252d range + wing spreads ALREADY IN BPS (the OIS sub-domain BPS
// convention).  Per-leg endpoint OIS rates ship in PERCENT (the natural rate
// unit for an OIS par-swap rate).
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch the
// SAME typed-detail endpoint ``/api/v1/rates/detail/ois-butterfly`` (per
// rendering_density.md §1.1 + §10 — both views consume the same bridge; the
// compact view just renders less of it).  KPI builders + formatting + tone
// logic + the risk-neutral-policy-pricing caveat live here in ONE place to
// prevent drift across surfaces.
//
// NB on backend Output shape — distinct from the linker / ZCIS butterfly
// siblings the backend Output does NOT carry ``methodology_label``,
// ``weekly_change_bps`` / ``monthly_change_bps``, ``observation_count``,
// ``short_tenor`` / ``belly_tenor`` / ``long_tenor`` strings, or
// ``short_years`` / ``belly_years`` / ``long_years``.  The per-tenor identity
// is reconstructed from the request params + the per-tool curve_family
// registry below; the methodology card sources the canonical caveat strings
// from this file with an explicit ``// TODO`` marker for when the backend
// ships a wire-honesty ``methodology_label`` (then the methodology card
// switches to consume it — one-line edit).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOisButterfly,
  type OisButterflyDetailParams,
} from '@/services/ratesApi';
import type { OisButterflyOutput } from '@/types/rates';
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
// OIS curve-family metadata.  Same-curve butterflies are single-family
// objects; the registry keys off curve_family because that uniquely
// determines the overnight-index identity (SOFR / ESTR / SONIA / TONA / AONIA
// / CORRA) the surfaces render.  Curve families enumerated mirror the
// backend's closed ``OIS_CURVE_FAMILY`` enum sourced from
// rates_agent/playbooks/ois.yml.
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
 *  nominal-pair concept (distinct from ``calculate_ois_cross_market_spread``
 *  which crosses two families).  One curve, one set of pillars. */
export const OIS_BUTTERFLY_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(FAMILY_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.marketShort} · ${m.indexShort}`,
}));

/** Tenor triplet presets per curve_family — the desk-canonical (short, belly,
 *  long) tuples on the ingested OIS grid.  Surfacing only registered
 *  triplets makes invalid orderings unreachable.  Mockup defaults the USD
 *  canonical triplet to 2s5s10s (SOFR policy-curvature focus). */
export interface OisButterflyTriplet {
  short: string;
  belly: string;
  long: string;
  label: string; // e.g. "2s5s10s"
}

export const OIS_BUTTERFLY_TRIPLETS_BY_CURVE: Record<
  string,
  ReadonlyArray<OisButterflyTriplet>
> = {
  USD_SOFR_OIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '2Y', belly: '10Y', long: '30Y', label: '2s10s30s' },
    { short: '1Y', belly: '2Y', long: '5Y', label: '1s2s5s' },
  ],
  EUR_ESTR_OIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '1Y', belly: '2Y', long: '5Y', label: '1s2s5s' },
  ],
  GBP_SONIA_OIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
    { short: '1Y', belly: '2Y', long: '5Y', label: '1s2s5s' },
  ],
  JPY_OIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
    { short: '5Y', belly: '10Y', long: '30Y', label: '5s10s30s' },
  ],
  AUD_OIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
  ],
  CAD_OIS: [
    { short: '2Y', belly: '5Y', long: '10Y', label: '2s5s10s' },
  ],
};

/** Pure-year hyphen-separated tenor label (e.g. "2-5-10") used in the
 *  identity row per ``mockups/Compact.png`` + ``mockups/Extended.png``.
 *  Sub-year tenors retain their unit (e.g. "3M-2Y-5Y"). */
export function tripletHyphenLabel(
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): string {
  const pureYear =
    shortTenor.endsWith('Y')
    && bellyTenor.endsWith('Y')
    && longTenor.endsWith('Y');
  if (pureYear) {
    const strip = (t: string) => t.replace(/Y$/i, '');
    return `${strip(shortTenor)}-${strip(bellyTenor)}-${strip(longTenor)}`;
  }
  return `${shortTenor}-${bellyTenor}-${longTenor}`;
}

/** The desk-canonical honesty caveat for this tool — surfaced in the compact
 *  footer + the extended methodology card.  OIS curves price the EXPECTED
 *  POLICY PATH under the risk-neutral measure; they are NOT a forecast of
 *  realised central-bank decisions and should not be read as policy
 *  outcomes.  Mockup-faithful one-liner.
 *
 *  TODO(PR10): when the backend ships ``current_metrics.methodology_label``
 *  (today the OIS butterfly Output schema lacks it — siblings in
 *  inflation_indexed_bonds / inflation_swaps already carry it), switch the
 *  methodology card's "Disclosure" row to source from the wire.  Tracked
 *  alongside the OIS sub-domain's PR10 backlog. */
export const OIS_BUTTERFLY_COMPACT_CAVEAT =
  'Risk-neutral policy pricing (policy path, not outcomes).';

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseOisButterflyArgs {
  curveFamily: string;
  shortTenor: string;
  bellyTenor: string;
  longTenor: string;
  lookbackDays?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseOisButterflyResult {
  data: OisButterflyOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views.  Fetches the typed-detail
 *  endpoint; re-fetches when any input changes.  Mirrors the
 *  inflation-swap-butterfly hook shape so the per-tool surfaces look the
 *  same shape file-for-file. */
export function useOisButterfly(
  args: UseOisButterflyArgs,
): UseOisButterflyResult {
  const [data, setData] = useState<OisButterflyOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OisButterflyDetailParams = {
    curve_family: args.curveFamily,
    short_tenor: args.shortTenor,
    belly_tenor: args.bellyTenor,
    long_tenor: args.longTenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (
      !args.curveFamily
      || !args.shortTenor
      || !args.bellyTenor
      || !args.longTenor
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailOisButterfly(params)
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
    args.bellyTenor,
    args.longTenor,
    args.lookbackDays,
    args.fieldName,
    args.asOfDate,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Sign-convention caption.  POSITIVE = belly CHEAP vs wings.  NEGATIVE =
// belly RICH.  Surfaced in the compact KPI caption + the extended fly cell.
// ---------------------------------------------------------------------------

export function bellyRegimeCaption(
  butterflyBps: number | null | undefined,
): string {
  if (butterflyBps == null || Number.isNaN(butterflyBps)) return '—';
  if (butterflyBps > 0) return 'Belly Cheap';
  if (butterflyBps < 0) return 'Belly Rich';
  return 'Flat';
}

/** Z-score caption combining stretch regime + belly direction.  Extreme
 *  positive z = belly extreme-CHEAP; extreme negative z = belly extreme-
 *  RICH.  Mockup-faithful — at z=+1.84 the compact caption is "Elevated
 *  Cheap"; at z=-0.86 it is "Neutral" (Normal regime). */
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
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. FLY (bps)        — signed bps, primary emphasis; subtext shows the
 *                          equivalent percent of belly OIS rate (mockup:
 *                          omitted at +8.4 bp on a ~4% belly).
 *    2. 1D CHANGE        — signed bps, toneForChange; subtext shows the
 *                          equivalent percent of belly OIS rate (mockup:
 *                          "(+0.23%)" caption at +1.9 bp).
 *    3. Z-SCORE (252D)   — signed value + regime+direction caption
 *                          (mockup: "Elevated Cheap" at z=+1.84).
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: OisButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  // Express the bps move as an approximate percent of CURRENT belly OIS rate
  // so the desk gets a sense of relative size — mockup shows "(+0.23%)"
  // subtext for a +1.9bp move when the belly OIS sits ~0.82%.  Returns null
  // when belly is missing or zero.
  const dailyPctOfBelly = bpsRelativeToBelly(
    cm.daily_change_bps,
    cm.belly_tenor_rate,
  );
  return [
    {
      label: 'FLY (bps)',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bps',
      tone: toneForFly(cm.current_butterfly_bps),
      emphasis: 'primary',
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.daily_change_bps),
      subtext:
        dailyPctOfBelly != null
          ? `(${signedFixed(dailyPctOfBelly, 2)}%)`
          : undefined,
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreCaptionForButterfly(cm.current_z_score),
    },
  ];
}

/** Belly-direction tone — POSITIVE bps (belly cheap) leans coral; NEGATIVE
 *  bps (belly rich) leans mint per the mockup's compact KPI colouring.  The
 *  fly cell carries 'neutral' tone when zero or unavailable. */
function toneForFly(bps: number | null | undefined): KPIDescriptor['tone'] {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'negative' : 'positive';
}

/** Compute bps move as percent of belly OIS rate, for the compact subtext.
 *  bps / (belly_pct * 100) → percent.  Returns null when either input is
 *  missing or belly is zero. */
function bpsRelativeToBelly(
  bps: number | null | undefined,
  bellyPct: number | null | undefined,
): number | null {
  if (
    bps == null
    || bellyPct == null
    || !Number.isFinite(bps)
    || !Number.isFinite(bellyPct)
    || bellyPct === 0
  ) {
    return null;
  }
  return bps / (bellyPct * 100);
}

/** The extended view's headline KPI strip (mockups/Extended.png).  Butterfly
 *  + 1d change + range fields are already BPS from the backend — no unit
 *  conversion needed.  NB the OIS butterfly backend Output does NOT carry
 *  ``weekly_change_bps`` / ``monthly_change_bps`` / ``observation_count``
 *  fields (distinct from the linker / ZCIS butterfly siblings), so the strip
 *  is correspondingly leaner — we surface only the fields the wire carries. */
export function extendedKPIs(
  data: OisButterflyOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'BUTTERFLY',
      value: signedFixed(cm.current_butterfly_bps, 1),
      unit: 'bps',
      tone: 'neutral',
      caption: bellyRegimeCaption(cm.current_butterfly_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: 'Z-SCORE (252D)',
      value: signedFixed(cm.current_z_score, 2),
      tone: toneForZScore(cm.current_z_score),
      caption: zScoreCaptionForButterfly(cm.current_z_score),
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
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: '252D LOW',
      value: signedFixed(cm.low_252d_bps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
  ];
}

/** Decomposition row — three endpoint OIS rates (PERCENT) + two wing
 *  spreads (BPS) so the desk can audit the butterfly construction without a
 *  second tool call.  Mirrors the mockup's "OIS DECOMPOSITION" row.  Per-
 *  tenor labels are reconstructed from the request triplet (the backend
 *  Output does NOT carry separate ``short_tenor`` / ``belly_tenor`` /
 *  ``long_tenor`` strings — only the combined ``butterfly_label``). */
export function decompositionKPIs(
  data: OisButterflyOutput,
  shortTenor: string,
  bellyTenor: string,
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
      label: `BELLY OIS (${bellyTenor})`,
      value: signedFixed(cm.belly_tenor_rate, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: `LONG OIS (${longTenor})`,
      value: signedFixed(cm.long_tenor_rate, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'WING SHORT (belly − short)',
      value: signedFixed(cm.wing_short_bps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
    {
      label: 'WING LONG (long − belly)',
      value: signedFixed(cm.wing_long_bps, 1),
      unit: 'bps',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the butterfly (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for OIS butterflies (bps).  Across the playbook universe OIS
// butterflies cluster around zero with stress excursions to ±200 bps or so.
// Anything well outside is almost certainly a generic-ticker roll artifact
// on one leg.  Mirrors the sovereign / linker / ZCIS sanity bounds.
const OIS_BUTTERFLY_SANITY_MIN_BPS = -400;
const OIS_BUTTERFLY_SANITY_MAX_BPS = 400;

/** Sanity-bound the canonical OIS butterfly series rows for the chart layer.
 *  Values already arrive in bps; clamp outliers to null. */
export function sanitiseButterflySeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < OIS_BUTTERFLY_SANITY_MIN_BPS
        || r.value > OIS_BUTTERFLY_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: OisButterflyOutput,
): ReadonlyArray<ReferenceBand> {
  const rawRows = data.time_series_butterfly?.rows ?? [];
  const sanitised = sanitiseButterflySeries(rawRows);
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
  data: OisButterflyOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.curve_family,
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
  curveFamily: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'cheap' : 'rich';
  const meta = oisFamilyFor(curveFamily);
  const family = meta ? `${meta.marketShort} ${meta.indexShort} OIS` : curveFamily;
  const policyLine =
    ' Curvature of the expected policy path under the risk-neutral measure — read the move as how the market is pricing the SHAPE of policy expectations, not as a forecast of realised central-bank decisions.';
  if (regime === 'Extreme') {
    return (
      `${family} butterfly is extreme ${direction} versus its trailing-year mean — `
      + `the belly OIS rate is ${direction === 'cheap' ? 'high' : 'low'} relative to the linearly interpolated wings.  `
      + `Sits in the ${bucket.toLowerCase()}-end of the 252d range.`
      + policyLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${family} butterfly is elevated ${direction} versus trailing-year history — `
      + `belly is ${direction === 'cheap' ? 'cheaper' : 'richer'} than the linearly interpolated wings on a normalised basis.`
      + policyLine
    );
  }
  return (
    `${family} butterfly is within its trailing-year norm; `
    + 'no extreme richness or cheapness in the belly.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty
 *  disclosure should flow from ``current_metrics.methodology_label``; the
 *  OIS butterfly backend Output currently does NOT carry that field (the
 *  sub-domain hasn't caught up to PR10 yet — siblings in
 *  inflation_indexed_bonds + inflation_swaps already do).  Until the
 *  backend ships it, the "Disclosure" row sources from the per-tool canonical
 *  caveat below (one-line edit to switch when the wire lands the field). */
export function buildMethodologyRows(
  data: OisButterflyOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
  shortTenor: string,
  bellyTenor: string,
  longTenor: string,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = oisFamilyFor(cm.curve_family);
  return [
    {
      label: 'Construction',
      value:
        `butterfly_bps = (2 × belly_rate − short_rate − long_rate) × 100, single curve `
        + `(${cm.curve_family}); raw OIS par-rate space — no basis subtraction, no convexity adjustment, no fitted curve`,
    },
    {
      label: 'Sign convention',
      value: 'POSITIVE = belly CHEAP vs linearly-interpolated wings · NEGATIVE = belly RICH',
    },
    {
      label: 'Triplet',
      value: `${shortTenor} / ${bellyTenor} / ${longTenor} (${cm.butterfly_label})`,
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (OIS quoted rate, all three endpoint series)`,
    },
    {
      label: 'Z-score model',
      value: `${cm.rolling_window_days}d rolling window on the bps butterfly series (YAML-locked — no input-layer override on this primitive)`,
    },
    {
      label: 'Trailing range',
      value: '252 trading days (high / low / percentile, bps)',
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
      label: 'Weighting',
      value: 'FIXED simple-butterfly (−1, +2, −1) on (short, belly, long) in raw OIS rate space — a.k.a. "50-50 wings". NOT DV01-neutral / NOT PCA-neutral (separate primitives if/when desk demand justifies)',
    },
    {
      label: 'Same-curve invariant',
      value: 'All three legs share curve_family — cross-curve OIS butterflies are forbidden at the input schema layer (would compose on calculate_ois_cross_market_spread).',
    },
    {
      label: 'Disclosure',
      value: OIS_BUTTERFLY_COMPACT_CAVEAT,
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
