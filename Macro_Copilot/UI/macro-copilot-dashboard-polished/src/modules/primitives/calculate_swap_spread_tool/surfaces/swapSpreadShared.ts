// ============================================================================
// swapSpreadShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for
// ``calculate_swap_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  This module knows what a "swap spread" is —
// the cross-domain asset-swap-spread-style differential between a SOVEREIGN
// bond yield and an OIS par-swap rate at the same tenor in the same currency
// (e.g. UST 10Y minus USD_SOFR_OIS 10Y).  Sign convention POSITIVE = treasuries
// trade CHEAP to OIS (locked at the backend).  Computed as the par-leg OIS
// approximation — ``(sovereign_yield − ois_rate) × 100`` in bps — NOT the
// present-value true asset-swap-spread that requires bond-level metadata
// (coupon, accrued interest, day-count, dirty-price).  The error vs true ASW
// is typically 1-3 bps on liquid sovereigns but grows to 10+ bps on
// off-the-run / high-coupon bonds; this honesty caveat is the load-bearing
// disclosure surfaced inline in both views.
//
// All three surfaces (BuildExtended, BuildCompact, Monitor widget) fetch the
// SAME typed-detail endpoint ``/api/v1/rates/detail/swap-spread`` (per
// rendering_density.md §1.1 + §10 — both views consume the same bridge; the
// compact view just renders less of it).  KPI builders + formatting + tone
// logic + the par-leg approximation caveat live here in ONE place to prevent
// drift across surfaces.
//
// NB on backend Output shape — distinct from the linker / sovereign / ZCIS
// cross-market siblings the backend Output does NOT carry
// ``methodology_label`` (matches the OIS sub-domain shape — same as
// calculate_ois_cross_market_spread / calculate_ois_curve_spread).  The
// per-tool disclosure string is sourced from this file with an explicit
// ``// TODO(PR10)`` marker for when the OIS sub-domain catches up.  Mirrors
// the OIS curve_spread / butterfly / cross-market siblings.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailSwapSpread,
  type SwapSpreadDetailParams,
} from '@/services/ratesApi';
import type { SwapSpreadOutput } from '@/types/rates';
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
// Currency-matched sovereign / OIS pair metadata.  A swap spread is only
// desk-meaningful when both legs are in the SAME currency — the backend
// CURVE_FAMILY_TO_CURRENCY closed mapping enforces this.  This per-tool
// registry mirrors the desk-canonical pairings so the UI can render concise
// pair chips ("US · UST/SOFR · Treasuries vs Swaps") and snap to a valid
// counterparty when the user changes a leg.  Keyed by sovereign curve_family
// because the sovereign uniquely determines the canonical OIS counterparty
// (one canonical OIS curve per currency).  Finance-aware → lives in this
// per-tool layer.
// ---------------------------------------------------------------------------

export interface SwapSpreadPairMeta {
  /** Sovereign curve family (e.g. 'UST'). */
  sovereignFamily: string;
  /** Canonical OIS counterparty (e.g. 'USD_SOFR_OIS'). */
  oisFamily: string;
  /** Currency code (e.g. 'USD'). */
  currency: string;
  /** Country label (e.g. 'US'). */
  country: string;
  /** Short sovereign label (e.g. 'UST'). */
  sovereignShort: string;
  /** Short OIS short-rate label (e.g. 'SOFR'). */
  oisShort: string;
  /** Country flag emoji. */
  flag: string;
}

const PAIR_BY_SOVEREIGN: Record<string, SwapSpreadPairMeta> = {
  UST: {
    sovereignFamily: 'UST',
    oisFamily: 'USD_SOFR_OIS',
    currency: 'USD',
    country: 'US',
    sovereignShort: 'UST',
    oisShort: 'SOFR',
    flag: '🇺🇸',
  },
  DE_BUND: {
    sovereignFamily: 'DE_BUND',
    oisFamily: 'EUR_ESTR_OIS',
    currency: 'EUR',
    country: 'Germany',
    sovereignShort: 'BUND',
    oisShort: 'ESTR',
    flag: '🇩🇪',
  },
  FR_OAT: {
    sovereignFamily: 'FR_OAT',
    oisFamily: 'EUR_ESTR_OIS',
    currency: 'EUR',
    country: 'France',
    sovereignShort: 'OAT',
    oisShort: 'ESTR',
    flag: '🇫🇷',
  },
  IT_BTP: {
    sovereignFamily: 'IT_BTP',
    oisFamily: 'EUR_ESTR_OIS',
    currency: 'EUR',
    country: 'Italy',
    sovereignShort: 'BTP',
    oisShort: 'ESTR',
    flag: '🇮🇹',
  },
  ES_BONO: {
    sovereignFamily: 'ES_BONO',
    oisFamily: 'EUR_ESTR_OIS',
    currency: 'EUR',
    country: 'Spain',
    sovereignShort: 'BONO',
    oisShort: 'ESTR',
    flag: '🇪🇸',
  },
  UK_GILT: {
    sovereignFamily: 'UK_GILT',
    oisFamily: 'GBP_SONIA_OIS',
    currency: 'GBP',
    country: 'UK',
    sovereignShort: 'Gilt',
    oisShort: 'SONIA',
    flag: '🇬🇧',
  },
  JGB: {
    sovereignFamily: 'JGB',
    oisFamily: 'JPY_OIS',
    currency: 'JPY',
    country: 'Japan',
    sovereignShort: 'JGB',
    oisShort: 'TONA',
    flag: '🇯🇵',
  },
};

/** Resolve the pair metadata from a sovereign curve_family.  Returns null for
 *  an unknown family (caller renders a neutral fallback). */
export function pairForSovereign(sovereignFamily: string): SwapSpreadPairMeta | null {
  return PAIR_BY_SOVEREIGN[sovereignFamily] ?? null;
}

/** The "Sovereign Leg" dropdown options — single dropdown picks the sovereign
 *  family; the canonical OIS counterparty is uniquely determined by currency.
 *  Keyed by sovereign family because that's the desk-canonical entry point
 *  ("US 10Y swap spread", "BUND 10Y swap spread"). */
export const SWAP_SPREAD_PAIR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(PAIR_BY_SOVEREIGN).map((p) => ({
    value: p.sovereignFamily,
    label: `${p.country} · ${p.sovereignShort} / ${p.oisShort}`,
  }));

/** Tenor sets per pair (intersection of the sovereign + OIS grids).  The
 *  swap-spread is only meaningful at tenors where both legs have data.  The
 *  conservative shared grid is the intersection used in practice. */
export const SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  UST: ['2Y', '5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
  DE_BUND: ['2Y', '5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
  FR_OAT: ['2Y', '5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
  IT_BTP: ['2Y', '5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
  ES_BONO: ['2Y', '5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
  UK_GILT: ['2Y', '5Y', '10Y', '30Y'].map((t) => ({ value: t, label: t })),
  JGB: ['2Y', '5Y', '10Y'].map((t) => ({ value: t, label: t })),
};

/** Desk-canonical compact-view caveat — surfaces the par-leg OIS approximation
 *  honesty disclosure inline.  The full methodology card carries the longer
 *  explanation; this one-liner is the minimum-viable methodology exposure for
 *  the compact view (rendering_density.md §2.2).
 *
 *  TODO(PR10): when the OIS sub-domain backend ships
 *  ``current_metrics.methodology_label`` on the SwapSpreadOutput Pydantic
 *  schema (today the OIS sub-domain Output schemas lack it — siblings in
 *  inflation_indexed_bonds / inflation_swaps already carry it), switch the
 *  compact caveat + methodology card "Disclosure" row to source from the wire.
 *  Same marker as the OIS cross_market_spread / curve_spread / butterfly
 *  registries. */
export const SWAP_SPREAD_COMPACT_CAVEAT =
  'Par-leg OIS approximation. NOT per-bond ASW.';

/** Sign-convention caption based on spread sign — used on the headline KPI
 *  in both views.  POSITIVE bps = treasuries trade CHEAP to OIS (asset-swap-
 *  spread convention); NEGATIVE = treasuries trade RICH to OIS. */
export function signCaption(spreadBps: number | null | undefined): string {
  if (spreadBps == null || Number.isNaN(spreadBps)) return '—';
  if (spreadBps === 0) return 'Parity';
  if (spreadBps > 0) return 'Treasuries Cheap';
  return 'Treasuries Rich';
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseSwapSpreadArgs {
  sovereignCurveFamily: string;
  oisCurveFamily: string;
  tenor: string;
  lookbackDays?: number;
  sovereignFieldName?: string;
  oisFieldName?: string;
}

export interface UseSwapSpreadResult {
  data: SwapSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by both Build views + the Monitor widget.
 *  Fetches the typed-detail endpoint; re-fetches when any input changes. */
export function useSwapSpread(args: UseSwapSpreadArgs): UseSwapSpreadResult {
  const [data, setData] = useState<SwapSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: SwapSpreadDetailParams = {
    sovereign_curve_family: args.sovereignCurveFamily,
    ois_curve_family: args.oisCurveFamily,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    sovereign_field_name: args.sovereignFieldName,
    ois_field_name: args.oisFieldName,
  };

  useEffect(() => {
    if (
      !args.sovereignCurveFamily
      || !args.oisCurveFamily
      || args.sovereignCurveFamily === args.oisCurveFamily
      || !args.tenor
    ) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailSwapSpread(params)
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
    args.sovereignCurveFamily,
    args.oisCurveFamily,
    args.tenor,
    args.lookbackDays,
    args.sovereignFieldName,
    args.oisFieldName,
  ]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Wire-derived helper — observation_count is computed CLIENT-SIDE from
// time_series_spread.rows because the swap-spread Output is leaner than the
// linker / sovereign / ZCIS cross-market siblings (no wire observation_count
// field — same shape as OIS curve_spread / cross_market_spread).
// ---------------------------------------------------------------------------

function spreadValues(data: SwapSpreadOutput): number[] {
  const rows = data.time_series_spread?.rows ?? [];
  return rows
    .map((r) => r.value)
    .filter((v): v is number => v != null && !Number.isNaN(v));
}

/** Total observation count in the displayed window (mockup KPI strip). */
export function observationCount(data: SwapSpreadOutput): number {
  return spreadValues(data).length;
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per rendering_density.md
 *  §2.2 + the shell-standard density (per the Option (c) precedent — Batch 1
 *  fdac7d2, Batch 2 cbd5613 — see THESIS Mockup conformance subsection):
 *    1. SPREAD    — signed bps, primary emphasis, sign-convention caption
 *                   ("Treasuries Cheap" / "Treasuries Rich" / "Parity")
 *    2. 1D CHANGE — signed bps, toneForChange
 *    3. Z-SCORE   — signed value + regime caption (Normal / Elevated / Extreme),
 *                   tone-coloured
 *  THESIS Q3 documents why these vs alternatives (e.g. weekly change,
 *  percentile, per-leg yields).
 *
 *  NB the mockup shows a SECONDARY 6-KPI strip on the compact card (5d /
 *  1m changes, percentile, 252d high / low, observations) — the THESIS
 *  Mockup conformance subsection explicitly accepts the shell-standard
 *  3-KPI density as the Option (c) precedent.  The full strip surfaces on
 *  the extended view. */
export function compactKPIs(
  data: SwapSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
      caption: signCaption(cm.current_spread_bps),
    },
    {
      label: '1D CHANGE',
      value: signedFixed(cm.daily_change_bps, 1),
      unit: 'bp',
      tone: toneForChange(cm.daily_change_bps),
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
 *  canonical bps series (the swap-spread wire Output does not carry it,
 *  matching the OIS cross-market shape). */
export function extendedKPIs(
  data: SwapSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  const obs = observationCount(data);
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      caption: signCaption(cm.current_spread_bps),
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
      label: 'SOVEREIGN YIELD',
      value: signedFixed(cm.sovereign_yield_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'OIS RATE',
      value: signedFixed(cm.ois_rate_pct, 2),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bounds for cross-domain swap spreads (bps).  Across the G7 universe
// sovereign-vs-OIS swap spreads at common tenors cluster within roughly
// ±200 bps; stress excursions to ±300 bps are plausible (e.g. BTP-ESTR at
// peripheral-stress extremes).  Anything well outside is almost certainly a
// generic-ticker roll artifact on one leg.  Mirrors the OIS cross-market /
// curve-spread sanity-bound discipline.
const SWAP_SPREAD_SANITY_MIN_BPS = -500;
const SWAP_SPREAD_SANITY_MAX_BPS = 500;

/** Sanity-bound the canonical spread series rows for the chart layer.  Values
 *  already arrive in bps; clamp outliers to null. */
export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < SWAP_SPREAD_SANITY_MIN_BPS
        || r.value > SWAP_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: SwapSpreadOutput,
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
  data: SwapSpreadOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.current_z_score == null && cm.percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.current_z_score);
  const bucket = bucketForPercentile(cm.percentile_252d);
  const interp = interpretationFor(
    regime,
    cm.current_z_score,
    bucket,
    cm.sovereign_curve_family,
    cm.ois_curve_family,
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
  sovereignFamily: string,
  oisFamily: string,
): string {
  if (z == null) return 'Insufficient data to characterise stretch.';
  const direction = z > 0 ? 'wider' : 'tighter';
  const pair = pairForSovereign(sovereignFamily);
  const pairLabel = pair
    ? `${pair.sovereignShort}-${pair.oisShort}`
    : `${sovereignFamily}-${oisFamily}`;
  const richCheapLine =
    ' Sign convention: POSITIVE = sovereign trades CHEAP to OIS;'
    + ' NEGATIVE = sovereign trades RICH to OIS.  Par-leg OIS approximation'
    + ' — NOT the present-value true ASW (which requires bond-level metadata).';

  if (regime === 'Extreme') {
    return (
      `${pairLabel} swap spread is extreme ${direction} versus its `
      + `trailing-year mean — sits in the ${bucket.toLowerCase()}-end of the `
      + '252d range.' + richCheapLine
    );
  }
  if (regime === 'Elevated') {
    return (
      `${pairLabel} swap spread is elevated ${direction} versus its `
      + 'trailing-year history.' + richCheapLine
    );
  }
  return (
    `${pairLabel} swap spread is within its trailing-year norm; no extreme `
    + 'stretch in either direction.'
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Build the methodology card rows from the wire payload + the request
 *  context.  Per the BUILD_GUIDE Stage-1 contract, the wire-honesty disclosure
 *  should flow from ``current_metrics.methodology_label``; the swap-spread
 *  backend Output currently does NOT carry that field (the OIS sub-domain
 *  hasn't caught up to PR10 yet — siblings in inflation_indexed_bonds /
 *  inflation_swaps already do).  Until the backend ships it, the "Disclosure"
 *  row sources from the per-tool canonical caveat above (one-line edit to
 *  switch when the wire lands the field — same marker as OIS curve_spread /
 *  butterfly / cross-market / rate-level / forward-rate). */
export function buildMethodologyRows(
  data: SwapSpreadOutput,
  effectiveSovereignField: string,
  effectiveOisField: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const pair = pairForSovereign(cm.sovereign_curve_family);
  return [
    {
      label: 'Construction',
      value:
        `spread_bps = (${cm.sovereign_curve_family} ${cm.tenor} yield − `
        + `${cm.ois_curve_family} ${cm.tenor} rate) × 100 — par-leg OIS `
        + 'approximation, MATCHED tenor, no convexity / accrued / day-count '
        + 'adjustment',
    },
    {
      label: 'Sign convention',
      value:
        'sovereign − OIS (locked in code). POSITIVE = sovereign CHEAP to OIS '
        + '(asset-swap-spread convention); NEGATIVE = sovereign RICH to OIS.',
    },
    {
      label: 'Tenor',
      value: `${cm.tenor} (single pillar shared by both legs)`,
    },
    {
      label: 'Sovereign field',
      value: `${effectiveSovereignField} (bond yield-to-maturity)`,
    },
    {
      label: 'OIS field',
      value: `${effectiveOisField} (OIS par swap rate)`,
    },
    {
      label: 'Alignment',
      value:
        'Strict pandas inner-join on trade_date after independent per-leg '
        + 'fetch — no synthetic spread points on holiday-asymmetric dates.',
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
      label: 'Currency match',
      value: pair
        ? `${pair.country} (${pair.sovereignShort} / ${pair.oisShort}) — ${pair.currency} both legs`
        : 'Same-currency invariant enforced at the schema layer',
    },
    {
      label: 'Units',
      value:
        'Spread + changes + 252d range in BPS (OIS sub-domain bps convention); '
        + 'per-leg sovereign yield / OIS rate in PERCENT.',
    },
    {
      label: 'Cross-domain invariant',
      value:
        'sovereign_curve_family != ois_curve_family AND same currency — '
        + 'cross-currency pairings are rejected at the input schema layer.',
    },
    {
      label: 'Disclosure',
      value:
        'Par-leg OIS approximation — (sovereign_yield − ois_rate) × 100 at '
        + 'MATCHED tenor.  Standard desk quick-and-dirty ASW.  NOT the true '
        + 'present-value asset-swap-spread (which requires bond-level '
        + 'metadata: coupon, accrued interest, day-count, dirty-price).  '
        + 'Error vs true ASW typically 1-3 bps for liquid sovereigns but '
        + 'grows to 10+ bps for off-the-run / high-coupon bonds.',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Tuckman 4e Ch.22' },
    { label: 'US TreasuryDirect' },
    { label: 'NY Fed SOFR' },
    { label: 'ECB ESTR' },
    { label: 'BoE SONIA' },
  ];
}
