// ============================================================================
// otrOfrSpreadShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for
// ``calculate_otr_ofr_spread_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of breakevenShared.ts).  This
// module knows what "OTR/OFR spread" means — specifically that it's the
// basis-point yield differential between the on-the-run sovereign cash-
// bond and the immediately-prior on-the-run bond ("first off-the-run"
// = OFR), and that the desk reads it as a liquidity-premium PROXY
// (not a direct read) because deviations can reflect bond-specific
// scarcity / squeeze / repo-rate differences too.
//
// SIGN CONVENTION: POSITIVE spread = OTR yield ABOVE OFR = OTR trading
// CHEAP to OFR (inverted-liquidity-premium signature; less common).
// NEGATIVE spread = OTR yield BELOW OFR = OTR trading RICH (the typical
// liquidity-premium signature, freshly auctioned bond commands a
// premium).
//
// WIRE-HONESTY QUIRK: the disclosure prose lives on the TOP-LEVEL Output
// (``data.methodology_note``), NOT on ``current_metrics`` like the more
// recent siblings.  This mirrors the financing-rate pattern.  The
// methodology-card row + compact caveat footer + Monitor widget tooltip
// all read ``data.methodology_note`` directly — NEVER hardcoded prose.
//
// Both BuildExtended.tsx and BuildCompact.tsx fetch the SAME data (per
// rendering_density.md §1.1 — both views consume the same typed-detail
// endpoint; the compact view just renders less).  The headline KPIs +
// formatting + tone logic live here, in ONE place.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOtrOfrSpread,
  type OtrOfrSpreadDetailParams,
} from '@/services/ratesApi';
import type { OtrOfrSpreadOutput } from '@/types/rates';
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
// Slot registry.  An OTR/OFR slot is identified by (country, tenor).
// Used to render concise chips ("US · 10Y") + per-country flag + the
// canonical desk subtitle.  Finance-aware → lives here, not in the
// shared registry.
// ---------------------------------------------------------------------------

export interface OtrOfrCountryMeta {
  /** ISO-3166-alpha-2 country code as stored in macro_data.otr_history. */
  country: string;
  /** Short desk label (e.g. 'UST', 'Bund'). */
  short: string;
  /** Long desk label used in the methodology card. */
  long: string;
  /** Country flag emoji for the compact footer + monitor kicker. */
  flag: string;
}

const COUNTRY_REGISTRY: Record<string, OtrOfrCountryMeta> = {
  US: { country: 'US', short: 'UST', long: 'US Treasury', flag: '🇺🇸' },
  DE: { country: 'DE', short: 'Bund', long: 'German Bund', flag: '🇩🇪' },
  GB: { country: 'GB', short: 'Gilt', long: 'UK Gilt', flag: '🇬🇧' },
  JP: { country: 'JP', short: 'JGB', long: 'Japan JGB', flag: '🇯🇵' },
  FR: { country: 'FR', short: 'OAT', long: 'French OAT', flag: '🇫🇷' },
  IT: { country: 'IT', short: 'BTP', long: 'Italian BTP', flag: '🇮🇹' },
  ES: { country: 'ES', short: 'Bono', long: 'Spanish Bono', flag: '🇪🇸' },
  CA: { country: 'CA', short: 'GoC', long: 'Government of Canada', flag: '🇨🇦' },
  AU: { country: 'AU', short: 'ACGB', long: 'Australian Cmwlth Govt', flag: '🇦🇺' },
};

/** Resolve the country meta.  Returns null for an unknown code. */
export function countryMetaFor(country: string): OtrOfrCountryMeta | null {
  return COUNTRY_REGISTRY[country.toUpperCase()] ?? null;
}

/** Ordered country options exposed on the controls strip + Monitor form.
 *  Mirrors the (country, tenor) universe in
 *  ``rates_agent/sovereign_bonds/tools/otr_ofr_spread/config.yaml``. */
export const COUNTRY_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(COUNTRY_REGISTRY).map((m) => ({
    value: m.country,
    label: `${m.country} · ${m.short}`,
  }));

/** Integer-Y tenor pillars matching sovereign_cash_bonds.yml + the
 *  resolver's slot identity convention (ADR 0007). */
export const TENOR_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  ['2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t }));

/** Identity subtitle (e.g. "UST 10Y · current benchmark vs prior
 *  benchmark").  Mockup copy uses "Current benchmark CUSIP vs prior
 *  benchmark CUSIP"; we keep that wording on the compact view. */
export function identitySubtitle(country: string): string {
  const meta = countryMetaFor(country);
  if (!meta) return 'Current benchmark vs prior benchmark';
  return `${meta.long} · current benchmark vs prior benchmark`;
}

/** Best-effort identifier for an OTR/OFR bond — returns CUSIP when
 *  present (US sovereigns), ISIN otherwise, em-dash on honest absence.
 *  Used in the Extended identity row + the per-leg decomposition. */
export function bondIdentifier(
  cusip: string | null,
  isin: string | null,
): string {
  return cusip ?? isin ?? '—';
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseOtrOfrSpreadArgs {
  country: string;
  tenor: string;
  lookbackDays?: number;
  fieldName?: string;
}

export interface UseOtrOfrSpreadResult {
  data: OtrOfrSpreadOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces.  Fetches the typed-
 *  detail endpoint; re-fetches when any input changes. */
export function useOtrOfrSpreadData(args: UseOtrOfrSpreadArgs): UseOtrOfrSpreadResult {
  const [data, setData] = useState<OtrOfrSpreadOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: OtrOfrSpreadDetailParams = {
    country: args.country,
    tenor: args.tenor,
    lookback_days: args.lookbackDays,
    field_name: args.fieldName,
  };

  useEffect(() => {
    if (!args.country || !args.tenor) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailOtrOfrSpread(params)
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
  }, [args.country, args.tenor, args.lookbackDays, args.fieldName]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders
// ---------------------------------------------------------------------------

/** The compact view's THREE shell-standard headline KPIs per
 *  rendering_density.md §2.2 + mockups/Compact.png:
 *    1. SPREAD (BPS)        (signed bps, neutral, primary emphasis)
 *    2. 1D CHANGE (BPS)     (signed bps, toneForChange)
 *    3. Z-SCORE (252D)      (signed value + regime caption, toneForZScore)
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: OtrOfrSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
      unit: 'bp',
      tone: 'neutral',
      emphasis: 'primary',
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

/** The extended view's FULL KPI strip (mockups/Extended.png — bps-scale
 *  strip + per-leg yields for the OTR/OFR decomposition). */
export function extendedKPIs(
  data: OtrOfrSpreadOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'SPREAD',
      value: signedFixed(cm.current_spread_bps, 1),
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
      value: `${cm.observation_count}`,
      tone: 'neutral',
    },
    {
      label: 'OTR YIELD',
      value: signedFixed(cm.otr_yield_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
    {
      label: 'OFR YIELD',
      value: signedFixed(cm.ofr_yield_pct, 3),
      unit: '%',
      tone: 'neutral',
    },
  ];
}

// ---------------------------------------------------------------------------
// Reference-band builder — ±2σ / ±1.5σ envelope on the spread (bps).
// ---------------------------------------------------------------------------

// Sanity bound for sovereign OTR/OFR spreads (bps).  Across G10 sovereigns
// these sit roughly [-50, +50] bps with stress / squeeze spikes; anything
// well outside is almost certainly a data artifact on one leg.  Defensive
// frontend safety net (mirror of breakevenShared's sanity bounds); the
// real fix lives in the data pipeline (docs/technical_debt.md TD #27).
const OTR_OFR_SPREAD_SANITY_MIN_BPS = -200;
const OTR_OFR_SPREAD_SANITY_MAX_BPS = 200;

export function sanitiseSpreadSeries(
  rows: ReadonlyArray<{ date: string; value: number | null }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value:
      r.value == null
        || Number.isNaN(r.value)
        || r.value < OTR_OFR_SPREAD_SANITY_MIN_BPS
        || r.value > OTR_OFR_SPREAD_SANITY_MAX_BPS
        ? null
        : r.value,
  }));
}

export function buildReferenceBands(
  data: OtrOfrSpreadOutput,
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
  data: OtrOfrSpreadOutput,
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
  const sign = z > 0 ? 'positive' : 'negative';
  const directionPhrase =
    z > 0
      ? 'OTR is trading CHEAP to OFR (inverted-liquidity signature; less common)'
      : 'OTR is trading RICH to OFR (typical liquidity-premium signature)';

  if (regime === 'Extreme') {
    return (
      `Spread is ${regime.toLowerCase()} ${sign} vs its trailing-year mean.  `
      + `Current observation sits in the ${bucket.toLowerCase()}-end of the `
      + `252d range — ${directionPhrase}.  Remember this is a liquidity-`
      + `premium PROXY: deviations can also reflect bond-specific scarcity, `
      + `squeeze dynamics, or repo-rate differences between the two legs.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Spread is elevated vs its trailing-year history — ${directionPhrase}.  `
      + `Liquidity-premium PROXY, not a direct read.`
    );
  }
  return (
    `Spread is within its trailing-year norm; no extreme stretch in either `
    + `direction.`
  );
}

// ---------------------------------------------------------------------------
// Methodology rows + references
// ---------------------------------------------------------------------------

/** Methodology rows for the Extended view.  The Disclosure row reads
 *  ``data.methodology_note`` verbatim — TD #27 forward-only + detection-
 *  date prose threaded from the resolver, NEVER hardcoded here. */
export function buildMethodologyRows(
  data: OtrOfrSpreadOutput,
  effectiveFieldName: string,
  effectiveLookbackDays: number,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = countryMetaFor(cm.country);
  return [
    {
      label: 'Construction',
      value: 'spread_bps = (otr_yield − ofr_yield) × 100',
    },
    {
      label: 'Sign convention',
      value:
        'POSITIVE = OTR yield ABOVE OFR (OTR cheap to OFR — inverted-liquidity '
        + 'signature; less common).  NEGATIVE = OTR rich (typical liquidity-'
        + 'premium signature, freshly auctioned bond commands a premium).',
    },
    {
      label: 'Liquidity-premium PROXY',
      value:
        'The OTR/OFR spread is a liquidity-premium PROXY; deviations can '
        + 'also reflect bond-specific scarcity, squeeze dynamics, or repo-'
        + 'rate differences between the two legs — NOT a clean liquidity-'
        + 'premium read.',
    },
    {
      label: 'OFR definition',
      value:
        'First-off-the-run = the bond from the SCD2 otr_history window '
        + 'IMMEDIATELY PRIOR to the current OTR window (SQL LAG over '
        + 'effective_from).',
    },
    {
      label: 'Field',
      value: `${effectiveFieldName} (mid yield-to-maturity, both legs)`,
    },
    {
      label: 'Z-score model',
      value:
        '252-trading-day rolling window; min periods 60; sample std (ddof=1).  '
        + 'YAML-locked conventions — no input-layer overrides.',
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
      label: 'Slot',
      value: meta
        ? `${meta.long} · ${cm.tenor}`
        : `${cm.country} · ${cm.tenor}`,
    },
    {
      label: 'OTR bond',
      value: bondIdentifier(cm.otr_cusip, cm.otr_isin),
    },
    {
      label: 'OFR bond',
      value: bondIdentifier(cm.ofr_cusip, cm.ofr_isin),
    },
    {
      label: 'Disclosure',
      value: data.methodology_note,
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0003 cash-bond substrate' },
    { label: 'ADR 0005 sovereign_cash_bonds.yml' },
    { label: 'ADR 0007 otr-resolver' },
    { label: 'TD #27 forward-only + detection-date' },
    { label: 'Bloomberg YLD_YTM_MID' },
  ];
}
