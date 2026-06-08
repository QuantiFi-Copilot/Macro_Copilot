// ============================================================================
// bondFuturesScannerShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``scan_bond_futures_extremes_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  SCANNER shape — the wire carries a MULTI-
// METRIC ranked LIST of (curve_family, contract_code) extremes ordered by
// |z| of the 252d-rolling z-score on each metric (price LEVEL, 1-day price
// CHANGE, volume LEVEL, end-of-day open-interest LEVEL) — NOT a single time
// series.  Mirrors the scan_inflation_swaps_extremes / linkers scanner
// siblings but owns its own curve-family + metric vocabulary so the three
// scanner-shape modules cannot drift.  All three bond-futures surfaces fetch
// the SAME typed-detail endpoint per rendering_density.md §1.1.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailBondFuturesScanner,
  type BondFuturesScannerDetailParams,
} from '@/services/ratesApi';
import type {
  ScanBondFuturesExtremesOutput,
  ScanBondFuturesExtremesResultRow,
  ScanBondFuturesMetric,
} from '@/types/rates';
import type { MethodologyRow, ReferenceChip } from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Bond-futures curve-family vocabulary.  Closed registry mirrored from the
// backend's ``bond_futures_curve_families`` YAML whitelist + the mockup's
// flag / market vocabulary.  Owned per-tool (no cross-module imports) per
// the dual-view contract.
// ---------------------------------------------------------------------------

export interface BondFuturesFamilyMeta {
  family: string;        // 'UST_FUT' | 'DE_FUT' | ...
  marketShort: string;   // 'US' | 'DE' | 'UK' | ...
  /** Country label used in the extended ranked-detail table's "Country"
   *  column — matches the mockup's flag + label pairing. */
  countryLabel: string;  // 'US' | 'Germany' | 'UK' | 'Japan' | ...
  flag: string;          // 🇺🇸 / 🇩🇪 / 🇬🇧 / 🇯🇵 / 🇫🇷 / 🇮🇹 / 🇪🇸 / 🇨🇦 / 🇦🇺
}

const FAMILY_REGISTRY: Record<string, BondFuturesFamilyMeta> = {
  UST_FUT: { family: 'UST_FUT', marketShort: 'US', countryLabel: 'US',        flag: '🇺🇸' },
  DE_FUT:  { family: 'DE_FUT',  marketShort: 'DE', countryLabel: 'Germany',   flag: '🇩🇪' },
  UK_FUT:  { family: 'UK_FUT',  marketShort: 'UK', countryLabel: 'UK',        flag: '🇬🇧' },
  JP_FUT:  { family: 'JP_FUT',  marketShort: 'JP', countryLabel: 'Japan',     flag: '🇯🇵' },
  FR_FUT:  { family: 'FR_FUT',  marketShort: 'FR', countryLabel: 'France',    flag: '🇫🇷' },
  IT_FUT:  { family: 'IT_FUT',  marketShort: 'IT', countryLabel: 'Italy',     flag: '🇮🇹' },
  ES_FUT:  { family: 'ES_FUT',  marketShort: 'ES', countryLabel: 'Spain',     flag: '🇪🇸' },
  CA_FUT:  { family: 'CA_FUT',  marketShort: 'CA', countryLabel: 'Canada',    flag: '🇨🇦' },
  AU_FUT:  { family: 'AU_FUT',  marketShort: 'AU', countryLabel: 'Australia', flag: '🇦🇺' },
};

export function bondFuturesFamilyFor(family: string): BondFuturesFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** Full curve-family option set — used by the controls strip and Monitor
 *  widget. */
export const BOND_FUTURES_SCANNER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.countryLabel} (${m.family})`,
  }));

/** Desk-canonical compact-mode caveat — short form rendered in the compact
 *  card's footer + the Monitor tile.  The FULL multi-line caveat lives on
 *  the wire (response.methodology_disclosure) and is surfaced on the
 *  extended view's methodology card.  ADR 0013 V1 monitors-only scope:
 *  CTD identification, gross/net basis, implied repo, and DV01-weighted
 *  inter-commodity spreads are Phase-4 work — this caveat surfaces that
 *  load-bearing limit on every surface. */
export const BOND_FUTURES_SCANNER_COMPACT_CAVEAT =
  'V1 monitors-only. CTD analytics not in scan.';

// ---------------------------------------------------------------------------
// Metric vocabulary.  The four metrics are CLOSED ENUM on the wire; this
// layer owns the human label, the short "SCOPE" tag the mockup shows, the
// value-formatter and the direction-of-extremity narrative.
// ---------------------------------------------------------------------------

export interface MetricMeta {
  metric: ScanBondFuturesMetric;
  /** Short SCOPE tag the mockup renders in the per-row chip (PRICE / VOL /
   *  OI / Δ). */
  scopeShort: string;
  /** Long label for the controls strip + methodology rows. */
  label: string;
  /** "PRICE z" / "Δ z" / "VOLUME z" / "OI z" — used in column headers. */
  shortDesc: string;
}

export const METRIC_REGISTRY: Record<ScanBondFuturesMetric, MetricMeta> = {
  price:         { metric: 'price',         scopeShort: 'PRICE', label: 'Price level',           shortDesc: 'Price z' },
  price_change:  { metric: 'price_change',  scopeShort: 'Δ',     label: '1-day price change',    shortDesc: 'Δ z' },
  volume:        { metric: 'volume',        scopeShort: 'VOL',   label: 'Volume level',          shortDesc: 'Vol z' },
  open_interest: { metric: 'open_interest', scopeShort: 'OI',    label: 'Open-interest level',   shortDesc: 'OI z' },
};

export const METRIC_ORDER: ReadonlyArray<ScanBondFuturesMetric> = [
  'price',
  'price_change',
  'volume',
  'open_interest',
];

// ---------------------------------------------------------------------------
// Per-row formatter helpers.  The "value" the desk reads off each row is
// metric-dependent (price for PRICE / Δ rows, volume count for VOL rows, OI
// count for OI rows).  The wire ships ALL snapshot values on every row so
// the consumer can read context without a second call; this layer picks the
// right field for the row's metric.
// ---------------------------------------------------------------------------

/** Map a (signal, metric) pair to the desk-canonical narrative tag the
 *  mockup shows ("Bond rally" / "Bond selloff" for price, "BUILD" /
 *  "UNWIND" for OI, "Surge" / "Quiet" for volume, "Up move" / "Down move"
 *  for the 1-day price change). */
export function narrativeTag(
  metric: ScanBondFuturesMetric,
  signal: 'EXTREME_HIGH' | 'EXTREME_LOW',
): string {
  switch (metric) {
    case 'price':
      return signal === 'EXTREME_HIGH' ? 'Bond rally' : 'Bond selloff';
    case 'price_change':
      return signal === 'EXTREME_HIGH' ? 'Up move' : 'Down move';
    case 'volume':
      return signal === 'EXTREME_HIGH' ? 'Volume surge' : 'Quiet';
    case 'open_interest':
      return signal === 'EXTREME_HIGH' ? 'OI build' : 'OI unwind';
    default:
      return '';
  }
}

/** Render the per-row "native value" — picks the right snapshot field by
 *  metric.  Prices kept in native quote-units (NOT *100), volume/OI in
 *  whole contracts (formatted with K / M suffixes for readability). */
export function nativeValue(row: ScanBondFuturesExtremesResultRow): string {
  switch (row.metric) {
    case 'price':
      return formatPriceNative(row.current_price);
    case 'price_change':
      return formatPriceDelta(row.daily_price_change);
    case 'volume':
      return formatContracts(row.current_volume);
    case 'open_interest':
      return formatContracts(row.current_open_interest);
    default:
      return '—';
  }
}

/** Per-row "units" qualifier the extended table's UNITS column renders. */
export function nativeUnits(row: ScanBondFuturesExtremesResultRow): string {
  switch (row.metric) {
    case 'price':
    case 'price_change':
      // Bond-futures prices are in contract-native quote_units (32nds for
      // TY1 / US1 / RX1, decimal for JB1, etc.).  Wire-honest "native"
      // label — the precise tick convention is per-contract on the
      // backend; we display 'native' here and surface the full caveat on
      // the methodology card.
      return 'native';
    case 'volume':
    case 'open_interest':
      return 'contracts';
    default:
      return '';
  }
}

// ---------------------------------------------------------------------------
// Scan-summary parser.  The wire's ``scan_summary`` is a human-readable
// string like "Scanned 19 bond-futures stems (17 scoreable). Stems with
// |z| >= 1.5 per metric: price=4, price_change=3, volume=2, open_interest=5.
// Showing top 5 per metric (14 rows). as_of 2026-05-20." OR "as_of dates
// span 2026-05-20 to 2026-05-22." for multi-stem anchors.  Defensive
// parsing so the surfaces degrade gracefully on a future format change.
// ---------------------------------------------------------------------------

export interface ScanSummary {
  scanned: number | null;
  scoreable: number | null;
  /** Sum of flagged across all four metrics. */
  flaggedTotal: number | null;
  perMetricFlagged: Partial<Record<ScanBondFuturesMetric, number>>;
  /** Z-score threshold extracted from the summary (e.g. 1.5). */
  threshold: number | null;
  showingTotalRows: number | null;
  asOfSpanStart: string | null;
  asOfSpanEnd: string | null;
  raw: string;
}

export function parseScanSummary(summary: string): ScanSummary {
  const scannedMatch = summary.match(
    /Scanned\s+(\d+)\s+bond-futures\s+stems\s*\((\d+)\s+scoreable\)/i,
  );
  const thresholdMatch = summary.match(/\|z\|\s*>=\s*([\d.]+)/i);
  // per-metric flagged counts: "price=4, price_change=3, volume=2,
  // open_interest=5" (defensive — falls back to per-key regex).
  const perMetric: Partial<Record<ScanBondFuturesMetric, number>> = {};
  for (const metric of METRIC_ORDER) {
    const re = new RegExp(`${metric}\\s*=\\s*(\\d+)`, 'i');
    const m = summary.match(re);
    if (m) perMetric[metric] = Number(m[1]);
  }
  const flaggedTotal = Object.values(perMetric).reduce(
    (acc, v) => acc + (typeof v === 'number' ? v : 0),
    0,
  );
  const showingMatch = summary.match(/\((\d+)\s+rows\)/i);
  const asOfSingleMatch = summary.match(/as_of\s+(\d{4}-\d{2}-\d{2})\./i);
  const asOfSpanMatch = summary.match(
    /as_of\s+dates\s+span\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})/i,
  );
  let asOfStart: string | null = null;
  let asOfEnd: string | null = null;
  if (asOfSpanMatch) {
    asOfStart = asOfSpanMatch[1];
    asOfEnd = asOfSpanMatch[2];
  } else if (asOfSingleMatch) {
    asOfStart = asOfSingleMatch[1];
    asOfEnd = asOfSingleMatch[1];
  }
  return {
    scanned: scannedMatch ? Number(scannedMatch[1]) : null,
    scoreable: scannedMatch ? Number(scannedMatch[2]) : null,
    flaggedTotal: Object.keys(perMetric).length > 0 ? flaggedTotal : null,
    perMetricFlagged: perMetric,
    threshold: thresholdMatch ? Number(thresholdMatch[1]) : null,
    showingTotalRows: showingMatch ? Number(showingMatch[1]) : null,
    asOfSpanStart: asOfStart,
    asOfSpanEnd: asOfEnd,
    raw: summary,
  };
}

// ---------------------------------------------------------------------------
// Top-N selector helpers — the compact view + Monitor tile show the
// "overall most extreme" rows REGARDLESS of metric.  The wire pre-orders
// the response by (metric, rank), but the compact view ranks across the
// full payload by absolute z-score so the desk reads the universe-wide
// extremity, not a single-metric top.
// ---------------------------------------------------------------------------

export function sortRowsByAbsZScore(
  rows: ReadonlyArray<ScanBondFuturesExtremesResultRow>,
): ReadonlyArray<ScanBondFuturesExtremesResultRow> {
  const copy = [...rows];
  copy.sort((a, b) => {
    const az = Math.abs(a.z_score ?? 0);
    const bz = Math.abs(b.z_score ?? 0);
    if (az !== bz) return bz - az;
    return (a.contract_code ?? '').localeCompare(b.contract_code ?? '');
  });
  return copy;
}

// ---------------------------------------------------------------------------
// Data hook — single-source fetcher shared by all three surfaces.
// ---------------------------------------------------------------------------

export interface UseBondFuturesScannerArgs {
  curveFamilies?: string[];   // e.g. ['UST_FUT', 'DE_FUT']
  topN?: number;
  minAbsZScore?: number;
  asOfDate?: string;
}

export interface UseBondFuturesScannerResult {
  data: ScanBondFuturesExtremesOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useBondFuturesScanner(
  args: UseBondFuturesScannerArgs,
): UseBondFuturesScannerResult {
  const [data, setData] = useState<ScanBondFuturesExtremesOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const curveFamiliesKey =
    args.curveFamilies && args.curveFamilies.length > 0
      ? args.curveFamilies.join(',')
      : undefined;

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    const params: BondFuturesScannerDetailParams = {
      curve_families: curveFamiliesKey,
      top_n: args.topN,
      min_abs_z_score: args.minAbsZScore,
      as_of_date: args.asOfDate,
    };
    fetchDetailBondFuturesScanner(params)
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
  }, [curveFamiliesKey, args.topN, args.minAbsZScore, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Format helpers — local to this tool because the bond-futures row-level
// formatting differs from the inflation scanner siblings (PRICE in native
// quote-units, volume / OI in contract counts with K / M suffixes).
// ---------------------------------------------------------------------------

export function formatZScore(z: number | null | undefined): string {
  if (z == null || Number.isNaN(z)) return '—';
  const sign = z >= 0 ? '+' : '';
  return `${sign}${z.toFixed(1)}σ`;
}

/** Bond-futures native-quote-units price — printed at 2 dp.  The 32nds
 *  tick convention (TY1 / US1 / RX1) is per-contract on the backend; the
 *  wire ships the rounded float (6 decimals) and the methodology card
 *  surfaces the per-contract tick caveat. */
export function formatPriceNative(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return '—';
  return p.toFixed(2);
}

/** 1-day raw price change (signed; 2 dp).  Same native-quote-units space
 *  as the price; not bps. */
export function formatPriceDelta(d: number | null | undefined): string {
  if (d == null || Number.isNaN(d)) return '—';
  const sign = d >= 0 ? '+' : '';
  return `${sign}${d.toFixed(2)}`;
}

/** Volume / OI count formatter — K / M suffixes for readability per the
 *  mockup (e.g. "1.2M contracts", "84K contracts"). */
export function formatContracts(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toFixed(0);
}

/** Tone bucket for a row's z-score per rendering_density.md §2.2
 *  (|z| ≥ 1.5 → elevated/amber; |z| ≥ 2.0 → coral up / mint down). */
export function zScoreTone(
  z: number | null | undefined,
): 'neutral' | 'positive' | 'negative' | 'elevated' | 'extreme-up' | 'extreme-down' {
  if (z == null || Number.isNaN(z)) return 'neutral';
  const abs = Math.abs(z);
  if (abs >= 2.0) return z > 0 ? 'extreme-up' : 'extreme-down';
  if (abs >= 1.5) return 'elevated';
  return z > 0 ? 'negative' : 'positive';
}

// ---------------------------------------------------------------------------
// Per-row identity helpers — the canonical disambiguator on this primitive
// is the CONTRACT_CODE stem (TY1 / UXY1 / RX1 / ...), NOT (curve_family,
// tenor) alone (per the backend's TD#11 note — TY1 vs UXY1 disambiguate).
// ---------------------------------------------------------------------------

export function instrumentFlag(row: ScanBondFuturesExtremesResultRow): string {
  return bondFuturesFamilyFor(row.curve_family)?.flag ?? '';
}

export function instrumentCountry(
  row: ScanBondFuturesExtremesResultRow,
): string {
  return bondFuturesFamilyFor(row.curve_family)?.countryLabel ?? row.curve_family;
}

export function instrumentContract(
  row: ScanBondFuturesExtremesResultRow,
): string {
  return row.contract_code;
}

// ---------------------------------------------------------------------------
// Histogram bin builder — synthesises a coarse-bin distribution of the
// returned multi-metric rows' z-scores for the Extended view's distribution
// panel.  The wire returns only the top-N per metric (not the full
// universe), so this is the histogram OF THE FLAGGED EXTREMES across all
// four metrics — labelled accordingly so the desk reader cannot mistake it
// for the full-universe distribution.
// ---------------------------------------------------------------------------

export interface ZScoreBin {
  from: number;
  to: number;
  count: number;
  extreme: boolean;
  elevated: boolean;
}

const HISTOGRAM_EDGES: ReadonlyArray<number> = [
  -3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0,
];

export function buildHistogramBins(
  rows: ReadonlyArray<ScanBondFuturesExtremesResultRow>,
): ReadonlyArray<ZScoreBin> {
  const bins: ZScoreBin[] = [];
  for (let i = 0; i < HISTOGRAM_EDGES.length - 1; i += 1) {
    const from = HISTOGRAM_EDGES[i];
    const to = HISTOGRAM_EDGES[i + 1];
    const mid = (from + to) / 2;
    const absMid = Math.abs(mid);
    bins.push({
      from,
      to,
      count: 0,
      extreme: absMid >= 2.0,
      elevated: absMid >= 1.5 && absMid < 2.0,
    });
  }
  for (const r of rows) {
    if (r.z_score == null) continue;
    const z = r.z_score;
    for (const b of bins) {
      const isLast = b.to === HISTOGRAM_EDGES[HISTOGRAM_EDGES.length - 1];
      if (z >= b.from && (isLast ? z <= b.to : z < b.to)) {
        b.count += 1;
        break;
      }
    }
  }
  return bins;
}

// ---------------------------------------------------------------------------
// Methodology rows + references for the Extended view.  Sourced from the
// WIRE's methodology_disclosure field (P5 / PR10: wire-honesty disclosure
// flows from compute() — NEVER hardcoded as a TS literal).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: ScanBondFuturesExtremesOutput,
  summary: ScanSummary,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Universe',
      value:
        'UST_FUT / DE_FUT / UK_FUT / JP_FUT / FR_FUT / IT_FUT / ES_FUT / CA_FUT / AU_FUT (rolling-generic front-month stems)',
    },
    {
      label: 'Metrics',
      value:
        'Price LEVEL · 1-day price CHANGE · Volume LEVEL · Open-interest LEVEL (each independently ranked)',
    },
    {
      label: 'Z-score window',
      value:
        summary.threshold != null
          ? `252 trading days · displaying stems with |z| ≥ ${summary.threshold}`
          : '252 trading days',
    },
    {
      label: 'Anchor',
      value:
        summary.asOfSpanStart && summary.asOfSpanEnd
          ? summary.asOfSpanStart === summary.asOfSpanEnd
            ? `Most-recent shared trading day (${summary.asOfSpanStart})`
            : `Per-stem latest trading day · span ${summary.asOfSpanStart} → ${summary.asOfSpanEnd}`
          : 'Most-recent shared trading day across the universe',
    },
    {
      label: 'Disclosure',
      value: data.methodology_disclosure || '—',
    },
  ];
  return rows;
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'ADR 0013' },
    { label: 'Bloomberg PX_LAST' },
    { label: 'Bloomberg PX_VOLUME' },
    { label: 'Bloomberg OPEN_INT' },
    { label: 'TimescaleDB' },
  ];
}
