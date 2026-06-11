// ============================================================================
// futuresVolumeOiShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``get_futures_volume_oi_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer (mirror of the bond_futures sibling's
// bondFuturesPriceShared.ts).  This module knows that bond-futures volume
// and open interest live in WHOLE-CONTRACT-COUNT space — NOT notional,
// NOT bps, NOT percent — and that the rolling-generic OI series mixes
// underlying contracts across quarterly rolls; the shared shells do not.
//
// All three surfaces fetch the SAME typed-detail endpoint
// (/api/v1/rates/detail/futures-volume-oi) per rendering_density.md §1.1 —
// the compact view just renders less.  KPI builders + formatting + tone
// logic live here, in ONE place.
//
// The contract registry below is owned per-tool (no cross-module imports
// per the dual-view contract) and mirrors the sibling
// get_futures_price_level_tool CONTRACT_REGISTRY's identity vocabulary,
// minus the price-side fields (quote_units / notation / decimals) that
// have no meaning on a contract-count tape.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailFuturesVolumeOi,
  type FuturesVolumeOiDetailParams,
} from '@/services/ratesApi';
import type { FuturesVolumeOiOutput } from '@/types/rates';
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
// Per-contract identity registry.  Mirrors the playbook universe the
// backend Input docstring enumerates (TD#11 — the rolling-generic stem is
// the canonical disambiguator: TY1 vs UXY1 are both UST_FUT 10Y; US1 vs
// WN1 both UST_FUT 30Y).
// ---------------------------------------------------------------------------

export interface FuturesVoiContractMeta {
  curveFamily: string;
  contractCode: string;
  /** Country flag emoji rendered in the identity row. */
  flag: string;
  /** Tenor label for the rolling-generic (matches the backend wire field). */
  tenor: string;
  /** Short market label for the identity chip (e.g. 'UST', 'Bund'). */
  shortLabel: string;
  /** Long human-facing market name. */
  longLabel: string;
}

const CONTRACT_REGISTRY: Record<string, FuturesVoiContractMeta> = {
  TY1:  { curveFamily: 'UST_FUT', contractCode: 'TY1',  flag: '🇺🇸', tenor: '10Y', shortLabel: 'UST',     longLabel: 'CME 10-Year U.S. Treasury Note Futures' },
  UXY1: { curveFamily: 'UST_FUT', contractCode: 'UXY1', flag: '🇺🇸', tenor: '10Y', shortLabel: 'UST',     longLabel: 'CME Ultra 10-Year U.S. Treasury Note Futures' },
  US1:  { curveFamily: 'UST_FUT', contractCode: 'US1',  flag: '🇺🇸', tenor: '30Y', shortLabel: 'UST',     longLabel: 'CME U.S. Treasury Bond Futures' },
  WN1:  { curveFamily: 'UST_FUT', contractCode: 'WN1',  flag: '🇺🇸', tenor: '30Y', shortLabel: 'UST',     longLabel: 'CME Ultra U.S. Treasury Bond Futures' },
  TU1:  { curveFamily: 'UST_FUT', contractCode: 'TU1',  flag: '🇺🇸', tenor: '2Y',  shortLabel: 'UST',     longLabel: 'CME 2-Year U.S. Treasury Note Futures' },
  FV1:  { curveFamily: 'UST_FUT', contractCode: 'FV1',  flag: '🇺🇸', tenor: '5Y',  shortLabel: 'UST',     longLabel: 'CME 5-Year U.S. Treasury Note Futures' },
  RX1:  { curveFamily: 'DE_FUT',  contractCode: 'RX1',  flag: '🇩🇪', tenor: '10Y', shortLabel: 'Bund',    longLabel: 'Eurex Euro-Bund Futures' },
  UB1:  { curveFamily: 'DE_FUT',  contractCode: 'UB1',  flag: '🇩🇪', tenor: '30Y', shortLabel: 'Buxl',    longLabel: 'Eurex Euro-Buxl Futures' },
  DU1:  { curveFamily: 'DE_FUT',  contractCode: 'DU1',  flag: '🇩🇪', tenor: '2Y',  shortLabel: 'Schatz',  longLabel: 'Eurex Euro-Schatz Futures' },
  OE1:  { curveFamily: 'DE_FUT',  contractCode: 'OE1',  flag: '🇩🇪', tenor: '5Y',  shortLabel: 'Bobl',    longLabel: 'Eurex Euro-Bobl Futures' },
  G1:   { curveFamily: 'UK_FUT',  contractCode: 'G1',   flag: '🇬🇧', tenor: '10Y', shortLabel: 'Gilt',    longLabel: 'ICE Long Gilt Futures' },
  JB1:  { curveFamily: 'JP_FUT',  contractCode: 'JB1',  flag: '🇯🇵', tenor: '10Y', shortLabel: 'JGB',     longLabel: 'OSE 10-Year JGB Futures' },
  OAT1: { curveFamily: 'FR_FUT',  contractCode: 'OAT1', flag: '🇫🇷', tenor: '10Y', shortLabel: 'OAT',     longLabel: 'Eurex Euro-OAT Futures' },
  IK1:  { curveFamily: 'IT_FUT',  contractCode: 'IK1',  flag: '🇮🇹', tenor: '10Y', shortLabel: 'BTP',     longLabel: 'Eurex Long-Term Euro-BTP Futures' },
  BTS1: { curveFamily: 'IT_FUT',  contractCode: 'BTS1', flag: '🇮🇹', tenor: '3Y',  shortLabel: 'BTP-S',   longLabel: 'Eurex Short-Term Euro-BTP Futures' },
  KOA1: { curveFamily: 'ES_FUT',  contractCode: 'KOA1', flag: '🇪🇸', tenor: '10Y', shortLabel: 'Bono',    longLabel: 'MEFF 10-Year Bono Futures' },
  CN1:  { curveFamily: 'CA_FUT',  contractCode: 'CN1',  flag: '🇨🇦', tenor: '10Y', shortLabel: 'CGB',     longLabel: 'MX 10-Year Government of Canada Bond Futures' },
  YM1:  { curveFamily: 'AU_FUT',  contractCode: 'YM1',  flag: '🇦🇺', tenor: '3Y',  shortLabel: 'ASX 3Y',  longLabel: 'ASX 3-Year Treasury Bond Futures' },
  XM1:  { curveFamily: 'AU_FUT',  contractCode: 'XM1',  flag: '🇦🇺', tenor: '10Y', shortLabel: 'ASX 10Y', longLabel: 'ASX 10-Year Treasury Bond Futures' },
};

export function contractMetaFor(
  contractCode: string,
): FuturesVoiContractMeta | null {
  return CONTRACT_REGISTRY[contractCode] ?? null;
}

/** Contract-code options for the Monitor widget + extended view's
 *  controls strip.  Order matches the catalog enumeration (UST first,
 *  then DE, UK, JP, FR, IT, ES, CA, AU) — same ordering convention as
 *  the price-level sibling. */
export const FUTURES_VOI_CONTRACT_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = Object.values(CONTRACT_REGISTRY).map((m) => ({
  value: m.contractCode,
  label: `${m.contractCode} · ${m.shortLabel} ${m.tenor} (${m.curveFamily})`,
}));

/** Curve-family options (collapsed from CONTRACT_REGISTRY) for the
 *  Monitor widget's first selector — picking a curve narrows the
 *  contract list. */
export const FUTURES_VOI_CURVE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = (() => {
  const seen = new Map<string, string>();
  for (const m of Object.values(CONTRACT_REGISTRY)) {
    if (!seen.has(m.curveFamily)) {
      seen.set(m.curveFamily, m.shortLabel);
    }
  }
  return Array.from(seen.entries()).map(([family, short]) => ({
    value: family,
    label: `${family} · ${short}`,
  }));
})();

/** Desk-canonical caveat for the compact footer + Monitor tile — the
 *  SHORT form of the wire ``methodology_disclosure`` (which carries the
 *  full ADR 0013 / P5 text and is surfaced verbatim on the extended
 *  view's methodology card).  Defined ONCE here so every surface renders
 *  the same string. */
export const FUTURES_VOLUME_OI_COMPACT_CAVEAT =
  'Contracts, not notional; rolling-generic spans contract rolls.';

// ---------------------------------------------------------------------------
// Contract-count formatting.  Whole-contract counts — K / M suffixes for
// readability in dense cells (mirrors the bond-futures scanner sibling's
// formatContracts); thousands-separated full counts for subtext / range
// strips where precision matters.
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

/** Tone for the ΔOI 1d cell — desk narrative is BUILD (mint) vs UNWIND
 *  (coral), mirroring the bond-futures scanner sibling's
 *  ``narrativeTag('open_interest', ...)`` vocabulary.  An OI build is
 *  positioning entering the contract; an unwind is positioning leaving.
 *  Neither is "good"/"bad" — the tone is a direction cue, not a P&L cue. */
export function toneForDeltaOi(n: number | null | undefined): ValueTone {
  if (n == null || Number.isNaN(n) || n === 0) return 'neutral';
  return n > 0 ? 'positive' : 'negative';
}

/** Desk narrative word for the ΔOI direction (BUILD / UNWIND). */
export function deltaOiNarrative(n: number | null | undefined): string | undefined {
  if (n == null || Number.isNaN(n) || n === 0) return undefined;
  return n > 0 ? 'OI build' : 'OI unwind';
}

/** Volume vs its 22d rolling mean, as a ratio string ("1.42×").  The
 *  desk's "is today busy?" read — the 22d window is wire-frozen on the
 *  backend (volume_rolling_mean_22d / volume_rolling_max_22d embed the
 *  window in the field name). */
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
// Data hook — single source consumed by BOTH Build views + the Monitor tile.
// ---------------------------------------------------------------------------

export interface UseFuturesVolumeOiArgs {
  curveFamily: string;
  contractCode: string;
  lookbackDays?: number;
}

export interface UseFuturesVolumeOiResult {
  data: FuturesVolumeOiOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useFuturesVolumeOi(
  args: UseFuturesVolumeOiArgs,
): UseFuturesVolumeOiResult {
  const [data, setData] = useState<FuturesVolumeOiOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const params: FuturesVolumeOiDetailParams = {
    curve_family: args.curveFamily,
    contract_code: args.contractCode,
    lookback_days: args.lookbackDays,
  };

  useEffect(() => {
    if (!args.curveFamily || !args.contractCode) {
      setData(null);
      setErrorMessage(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    fetchDetailFuturesVolumeOi(params)
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
  }, [args.curveFamily, args.contractCode, args.lookbackDays]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// KPI descriptor builders — compact (3 shell-standard cells per the
// Option-(c) precedent) + extended (10 cells on the CONTRACT-COUNT axis).
// ---------------------------------------------------------------------------

/** The compact view's THREE canonical headline KPIs per
 *  rendering_density.md §2.2:
 *
 *    1. OPEN INTEREST      — the positioning level (contracts; K/M
 *                            abbreviated with the exact count as subtext).
 *    2. ΔOI 1D             — the build/unwind read (raw contract delta;
 *                            mint build / coral unwind).
 *    3. OI Z-SCORE (252D)  — "is positioning stretched?" (toneForZScore +
 *                            regime caption).
 *
 *  THESIS Q3 documents why these vs alternatives (volume level, volume
 *  vs 22d mean, OI percentile). */
export function compactKPIs(
  data: FuturesVolumeOiOutput,
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
 *  the volume 22d-context block (level, vs-mean ratio, 22d max) and the
 *  observation count. */
export function extendedKPIs(
  data: FuturesVolumeOiOutput,
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
// Chart series + reference-band builders.  The MAIN chart is the OPEN-
// INTEREST series (the positioning signal); volume renders in a secondary
// MiniChart strip on the extended view (the shared MainChart is single-
// series — composing two panels is the honest way to show both tapes).
// ---------------------------------------------------------------------------

/** Sanity bounds on contract counts — defensive frontend envelope.  A
 *  whole-contract count is non-negative; the most liquid stems (TY1 /
 *  SFR-adjacent universes) peak in single-digit millions, so anything
 *  beyond 100M contracts is a data artifact and is null-rendered. */
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
  return rows.map((r) => ({ date: r.date, value: sanitiseCount(r.open_interest) }));
}

export function sanitiseVolumeSeries(
  rows: ReadonlyArray<{ date: string; volume: number; open_interest: number }>,
): Array<{ date: string; value: number | null }> {
  return rows.map((r) => ({ date: r.date, value: sanitiseCount(r.volume) }));
}

export function buildReferenceBands(
  data: FuturesVolumeOiOutput,
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
  data: FuturesVolumeOiOutput,
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
      ? 'consistent with positioning building into the contract'
      : 'consistent with positioning unwinding out of the contract';

  if (regime === 'Extreme') {
    return (
      `Open interest is ${regime.toLowerCase()} ${direction} its trailing-year mean. ` +
      `The current observation sits in the ${bucket.toLowerCase()}-end of the 252d range ` +
      `and is ${tilt}. Remember this is the rolling-generic OI tape — the series ` +
      `mixes underlying contracts across quarterly rolls, so roll-window moves are ` +
      `mechanical, not positioning.`
    );
  }
  if (regime === 'Elevated') {
    return (
      `Open interest is elevated vs. its trailing-year history. ` +
      `The recent move is ${tilt}. Rolling-generic tape — interpret near contract ` +
      `rolls with care.`
    );
  }
  return (
    `Open interest is within its trailing-year norm; ` +
    `no extreme positioning stretch in either direction.`
  );
}

export function buildMethodologyRows(
  data: FuturesVolumeOiOutput,
): ReadonlyArray<MethodologyRow> {
  const cm = data.current_metrics;
  const meta = contractMetaFor(cm.contract_code);
  return [
    {
      label: 'Contract',
      value: `${cm.contract_code} · ${cm.tenor}${meta ? ` · ${meta.longLabel}` : ''}`,
    },
    {
      label: 'Front underlying',
      value:
        cm.security_name != null
          ? `${cm.security_name}${cm.expiry_date ? ` · expires ${cm.expiry_date}` : ''}`
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
    { label: 'ADR 0013 (bond_futures V1 monitors-only)' },
    { label: 'TD#11 (rolling-generic stem disambiguator)' },
    { label: 'Bloomberg PX_VOLUME' },
    { label: 'Bloomberg OPEN_INT' },
    { label: 'TimescaleDB' },
  ];
}
