// ============================================================================
// policyFuturesVoiShared.ts — Per-tool helpers shared between
// BuildExtended.tsx and BuildCompact.tsx for
// ``policy_futures_get_volume_open_interest_snapshot_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the bond_futures sibling's
// ``../../get_futures_volume_oi_tool/surfaces/futuresVolumeOiShared.ts`` —
// P3, the two volume/OI modules cannot drift).  This module knows that
// STIR-strip volume and open interest live in WHOLE-CONTRACT-COUNT space —
// NOT notional, NOT bps — and that the strip-slot series (SFR1, SFR2, …)
// mixes underlying contracts across quarterly rolls; the shared shells do
// not.  Keyed by (curve_family, strip_position) per ADR 0013 — the
// strip-slot master stem (contract_code, e.g. 'SFR3') comes back ON the
// wire, it is not an input.
//
// Both Build views fetch the SAME typed-detail endpoint
// (/api/v1/rates/detail/policy-futures-voi-snapshot) per
// rendering_density.md §1.1 — the compact view just renders less.  KPI
// builders + formatting + tone logic live here, in ONE place.
//
// The curve registry below is owned per-tool (no cross-module imports per
// the dual-view contract) and mirrors the strip-snapshot sibling's
// CURVE_REGISTRY identity vocabulary
// (../../policy_futures_get_futures_strip_snapshot_tool/surfaces/
// policyFuturesStripSnapshotShared.ts).
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesVoiSnapshot,
  type PolicyFuturesVoiSnapshotDetailParams,
} from '@/services/ratesApi';
import type { VolumeOpenInterestSnapshotOutput } from '@/types/rates';
import {
  bucketForPercentile,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  type KPIDescriptor,
  type MethodologyRow,
  type ReferenceBand,
  type ReferenceChip,
  type StretchContext,
  type ValueTone,
} from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Curve-family identity registry.  The backend Input is a closed Literal
// (SOFR_FUT | EUR_SHORT_RATE_FUT | SONIA_FUT); an unknown family 422s at
// the Pydantic layer.
// ---------------------------------------------------------------------------

export interface PolicyFuturesVoiCurveMeta {
  family: string;
  flag: string;
  /** Short market label for the identity chip (e.g. 'SOFR'). */
  shortLabel: string;
  /** Long human-facing market name (e.g. 'US Fed SOFR strip'). */
  longLabel: string;
  /** 'RFR' (SOFR / SONIA compounded daily) or 'IBOR' (3M Euribor). */
  regime: 'RFR' | 'IBOR';
  /** Master-stem prefix for the strip slots (e.g. 'SFR' → 'SFR1'). */
  stripStemPrefix: string;
}

const CURVE_REGISTRY: Record<string, PolicyFuturesVoiCurveMeta> = {
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

export function curveMetaFor(
  curveFamily: string,
): PolicyFuturesVoiCurveMeta | null {
  return CURVE_REGISTRY[curveFamily] ?? null;
}

/** Curve-family options for the extended controls strip. */
export const POLICY_VOI_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CURVE_REGISTRY).map((m) => ({
  value: m.family,
  label: `${m.shortLabel} · ${m.longLabel}`,
}));

/** Whites (1-4) / Reds (5-8) segment tag per the V1 universe. */
export function stripSegmentLabel(
  stripPosition: number,
): 'WHITES' | 'REDS' | 'GREENS' {
  if (stripPosition <= 4) return 'WHITES';
  if (stripPosition <= 8) return 'REDS';
  return 'GREENS';
}

/** Strip-position options (1-based).  V1 universe is whites + reds
 *  (1-8); the backend route caps at 12, but exposing slots without
 *  ingested data would just surface controlled-error envelopes. */
export const POLICY_VOI_STRIP_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Array.from({ length: 8 }, (_, i) => {
  const pos = i + 1;
  const seg = stripSegmentLabel(pos).toLowerCase();
  return {
    value: `${pos}`,
    label: pos === 1 ? '1 · front (whites)' : `${pos} · ${seg}`,
  };
});

/** Desk-canonical caveat for the compact footer — SHORT form of the wire
 *  ``methodology_disclosure`` (which carries the full ADR 0013 / P5 text
 *  and is surfaced verbatim on the extended view's methodology card).
 *  Same string as the bond-futures sibling — the honesty point is
 *  identical on both tapes. */
export const POLICY_VOI_COMPACT_CAVEAT =
  'Contracts, not notional; strip-slot series spans contract rolls.';

// ---------------------------------------------------------------------------
// Contract-count formatting.  Mirrors the bond-futures sibling exactly —
// whole-contract counts, K / M abbreviations, signed deltas, exact
// thousands-separated subtexts.
// ---------------------------------------------------------------------------

export function formatContracts(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toFixed(0);
}

/** Signed contract-count change (ΔOI 1d is a raw whole-contract
 *  subtraction on the wire — NOT bps, NOT *100). */
export function formatDeltaContracts(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = n >= 0 ? '+' : '-';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

/** Full thousands-separated count — subtext under abbreviated cells so
 *  the exact whole-contract figure stays auditable. */
export function formatContractsExact(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return Math.round(n).toLocaleString('en-US');
}

/** Tone for the ΔOI 1d cell — BUILD (mint) vs UNWIND (coral).  Direction
 *  cue, not a P&L cue (mirrors the bond-futures sibling). */
export function toneForDeltaOi(n: number | null | undefined): ValueTone {
  if (n == null || Number.isNaN(n) || n === 0) return 'neutral';
  return n > 0 ? 'positive' : 'negative';
}

/** Desk narrative word for the ΔOI direction (BUILD / UNWIND). */
export function deltaOiNarrative(
  n: number | null | undefined,
): string | undefined {
  if (n == null || Number.isNaN(n) || n === 0) return undefined;
  return n > 0 ? 'OI build' : 'OI unwind';
}

/** Volume vs its 22d rolling mean, as a ratio string ("1.42×") — the
 *  desk's "is today busy?" read; the 22d window is wire-frozen. */
export function volumeVsMeanRatio(
  current: number | null | undefined,
  mean22d: number | null | undefined,
): string {
  if (
    current == null ||
    mean22d == null ||
    Number.isNaN(current) ||
    Number.isNaN(mean22d) ||
    mean22d === 0
  ) {
    return '—';
  }
  return `${(current / mean22d).toFixed(2)}×`;
}

// ---------------------------------------------------------------------------
// Data hook — single source consumed by BOTH Build views.
// ---------------------------------------------------------------------------

export interface UsePolicyVoiArgs {
  curveFamily: string;
  stripPosition: number;
  lookbackDays?: number;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UsePolicyVoiResult {
  data: VolumeOpenInterestSnapshotOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function usePolicyVoiSnapshot(
  args: UsePolicyVoiArgs,
): UsePolicyVoiResult {
  const [data, setData] = useState<VolumeOpenInterestSnapshotOutput | null>(
    null,
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: PolicyFuturesVoiSnapshotDetailParams = {
    curve_family: args.curveFamily,
    strip_position: args.stripPosition,
    lookback_days: args.lookbackDays,
    as_of_date: args.asOfDate || undefined,
  };

  useEffect(() => {
    if (!args.curveFamily || !Number.isFinite(args.stripPosition)) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailPolicyFuturesVoiSnapshot(params)
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
  }, [args.curveFamily, args.stripPosition, args.lookbackDays, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders — compact (3 shell-standard cells) + extended
// (10 cells on the CONTRACT-COUNT axis).  Cell-for-cell mirror of the
// bond-futures sibling (P3) — the wire schema is the SAME Pydantic class.
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *    1. OPEN INTEREST      — the positioning level (contracts).
 *    2. ΔOI 1D             — the build/unwind read.
 *    3. OI Z-SCORE (252D)  — "is positioning stretched?".
 *  THESIS Q3 documents why these vs alternatives. */
export function compactKPIs(
  data: VolumeOpenInterestSnapshotOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'OPEN INTEREST',
      value: formatContracts(cm.current_open_interest),
      unit: 'contracts',
      tone: 'neutral',
      emphasis: 'primary',
      subtext: formatContractsExact(cm.current_open_interest),
    },
    {
      label: 'ΔOI 1D',
      value: formatDeltaContracts(cm.delta_open_interest_1d),
      unit: 'contracts',
      tone: toneForDeltaOi(cm.delta_open_interest_1d),
      caption: deltaOiNarrative(cm.delta_open_interest_1d),
    },
    {
      label: 'OI Z-SCORE (252D)',
      value: signedFixed(cm.oi_z_score, 2),
      unit: 'σ',
      tone: toneForZScore(cm.oi_z_score),
      caption: regimeForZScore(cm.oi_z_score),
    },
  ];
}

/** The extended view's FULL KPI strip — 10 cells on the contract-count
 *  axis: the OI block (level, Δ, z, percentile, 252d range) followed by
 *  the volume 22d-context block and the observation count. */
export function extendedKPIs(
  data: VolumeOpenInterestSnapshotOutput,
): ReadonlyArray<KPIDescriptor> {
  const cm = data.current_metrics;
  return [
    {
      label: 'OPEN INTEREST',
      value: formatContracts(cm.current_open_interest),
      unit: 'contracts',
      tone: 'neutral',
      subtext: formatContractsExact(cm.current_open_interest),
    },
    {
      label: 'ΔOI 1D',
      value: formatDeltaContracts(cm.delta_open_interest_1d),
      unit: 'contracts',
      tone: toneForDeltaOi(cm.delta_open_interest_1d),
      caption: deltaOiNarrative(cm.delta_open_interest_1d),
    },
    {
      label: 'OI Z-SCORE (252D)',
      value: signedFixed(cm.oi_z_score, 2),
      unit: 'σ',
      tone: toneForZScore(cm.oi_z_score),
      caption: regimeForZScore(cm.oi_z_score),
    },
    {
      label: 'OI PERCENTILE (252D)',
      value:
        cm.oi_percentile_252d != null
          ? `${Math.round(cm.oi_percentile_252d)}`
          : '—',
      unit: 'th',
      caption: bucketForPercentile(cm.oi_percentile_252d),
    },
    {
      label: '252D OI HIGH',
      value: formatContracts(cm.oi_high_252d),
      unit: 'contracts',
      tone: 'neutral',
    },
    {
      label: '252D OI LOW',
      value: formatContracts(cm.oi_low_252d),
      unit: 'contracts',
      tone: 'neutral',
    },
    {
      label: 'VOLUME',
      value: formatContracts(cm.current_volume),
      unit: 'contracts',
      tone: 'neutral',
      subtext: formatContractsExact(cm.current_volume),
    },
    {
      label: 'VOL VS 22D MEAN',
      value: volumeVsMeanRatio(cm.current_volume, cm.volume_rolling_mean_22d),
      tone: 'neutral',
      caption:
        cm.volume_rolling_mean_22d != null
          ? `mean ${formatContracts(cm.volume_rolling_mean_22d)}`
          : undefined,
    },
    {
      label: 'VOL 22D MAX',
      value: formatContracts(cm.volume_rolling_max_22d),
      unit: 'contracts',
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
// Chart series + reference-band builders.  MAIN chart = the OPEN-INTEREST
// series (the positioning signal); volume context lives in the KPI strip
// (the shared MainChart is single-series).  Mirrors the sibling.
// ---------------------------------------------------------------------------

/** Sanity bounds on contract counts — defensive frontend envelope.  STIR
 *  strips are the most liquid futures on earth (SOFR front OI peaks in
 *  the low millions); anything beyond 100M contracts is a data artifact
 *  and is null-rendered.  Same envelope as the bond-futures sibling. */
const CONTRACTS_SANITY_MIN = 0;
const CONTRACTS_SANITY_MAX = 100_000_000;

function sanitiseCount(v: number | null | undefined): number | null {
  return v == null ||
    Number.isNaN(v) ||
    v < CONTRACTS_SANITY_MIN ||
    v > CONTRACTS_SANITY_MAX
    ? null
    : v;
}

export function sanitiseOiSeries(
  rows: ReadonlyArray<{ date: string; volume: number; open_interest: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({
    date: r.date,
    value: sanitiseCount(r.open_interest),
  }));
}

export function buildReferenceBands(
  data: VolumeOpenInterestSnapshotOutput,
): ReadonlyArray<ReferenceBand> {
  const sanitised = sanitiseOiSeries(data.time_series ?? []);
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
// Stretch-context + methodology builders for the extended view.
// ---------------------------------------------------------------------------

export function buildStretchContext(
  data: VolumeOpenInterestSnapshotOutput,
): StretchContext | undefined {
  const cm = data.current_metrics;
  if (cm.oi_z_score == null && cm.oi_percentile_252d == null) return undefined;

  const regime = regimeForZScore(cm.oi_z_score);
  const bucket = bucketForPercentile(cm.oi_percentile_252d);
  const interp = interpretationFor(regime, cm.oi_z_score, bucket);

  return {
    percentile:
      cm.oi_percentile_252d != null
        ? { value: cm.oi_percentile_252d, bucket }
        : undefined,
    zScoreRegime:
      cm.oi_z_score != null
        ? {
            value: cm.oi_z_score,
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
  if (z == null) return 'Insufficient data to characterise positioning stretch.';
  const direction = z > 0 ? 'above' : 'below';
  const tilt =
    z > 0
      ? 'consistent with positioning building into the slot'
      : 'consistent with positioning unwinding out of the slot';

  if (regime === 'Extreme') {
    return (
      `Open interest is ${regime.toLowerCase()} ${direction} its trailing-year mean. ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${tilt}. Remember this is the strip-SLOT OI tape — the series ` +
      `mixes underlying contracts across quarterly rolls, so roll-window moves are ` +
      `mechanical, not positioning.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Open interest is elevated vs. its trailing-year history. ` +
      `The recent move is ${tilt}. Strip-slot tape — interpret near contract ` +
      `rolls with care.`
    );
  }
  return (
    `Open interest is within its trailing-year norm; ` +
    `no extreme positioning stretch in either direction.`
  );
}

export function buildMethodologyRows(
  data: VolumeOpenInterestSnapshotOutput,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = curveMetaFor(cm.curve_family);
  const seg = stripSegmentLabel(cm.strip_position).toLowerCase();
  return [
    {
      label: 'Strip slot',
      value: `${cm.contract_code} · position ${cm.strip_position} (${seg})${meta ? ` · ${meta.longLabel}` : ''}`,
    },
    {
      label: 'Front underlying',
      value:
        cm.underlying_contract_code != null
          ? `${cm.underlying_contract_code}${cm.security_name ? ` · ${cm.security_name}` : ''}${cm.expiry_date ? ` · expires ${cm.expiry_date}` : ''}`
          : '—',
    },
    {
      label: 'Units',
      value:
        'Whole contracts (NOT notional). Multiply by contract size to convert to notional.',
    },
    {
      label: 'Contract size',
      value: cm.contract_size != null ? `${cm.contract_size}` : '—',
    },
    {
      label: 'Fields',
      value:
        'PX_VOLUME + OPEN_INT (YAML-owned conventions — not input-layer knobs)',
    },
    {
      label: 'OI z-score model',
      value: '252d rolling window on the OI LEVEL (YAML-locked)',
    },
    {
      label: 'Volume context',
      value:
        '22d rolling mean / max (wire-frozen short window — volume is far more volatile intra-month than OI)',
    },
    {
      label: 'Trailing range',
      value: '252 trading days (OI high / low / percentile)',
    },
    {
      // P5 wire-honesty: the full ADR 0013 caveat + the explicit OI
      // z-score lookback flow from compute()'s methodology_disclosure —
      // NEVER hardcoded as a TS literal.
      label: 'Disclosure',
      value: data.methodology_disclosure || '—',
    },
  ];
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0013 (volume/OI conventions)' },
    { label: 'Strip-slot master stems (SFR/ER/SFI + position)' },
    { label: 'Bloomberg PX_VOLUME' },
    { label: 'Bloomberg OPEN_INT' },
    { label: 'TimescaleDB' },
  ];
}
