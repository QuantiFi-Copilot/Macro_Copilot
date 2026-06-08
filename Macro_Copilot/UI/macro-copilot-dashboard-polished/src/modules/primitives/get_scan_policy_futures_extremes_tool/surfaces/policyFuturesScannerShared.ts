// ============================================================================
// policyFuturesScannerShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``get_scan_policy_futures_extremes_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  SCANNER shape — the wire carries a MULTI-
// METRIC ranked LIST of (curve_family, strip_position, contract_code)
// extremes ordered by |z| of the 252d-rolling z-score on each metric
// (implied-rate LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL,
// end-of-day open-interest LEVEL) — NOT a single time series.  Mirrors the
// scan_bond_futures_extremes / scan_inflation_swaps_extremes sibling
// patterns but owns its own STIR-specific curve-family + metric + pack
// vocabulary so the four scanner-shape modules cannot drift.  All three
// surfaces fetch the SAME typed-detail endpoint per rendering_density.md
// §1.1.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailPolicyFuturesScanner,
  type PolicyFuturesScannerDetailParams,
} from '@/services/ratesApi';
import type {
  ScanPolicyFuturesExtremesOutput,
  ScanPolicyFuturesExtremesResultRow,
  ScanPolicyFuturesMetric,
} from '@/types/rates';
import type { MethodologyRow, ReferenceChip } from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Policy-futures (STIR) curve-family vocabulary.  Closed registry mirrored
// from the backend's ``policy_futures_curve_families`` YAML whitelist + the
// mockup's flag / central-bank + RFR/IBOR regime vocabulary.  Owned per-tool
// (no cross-module imports) per the dual-view contract.
// ---------------------------------------------------------------------------

export interface PolicyFuturesFamilyMeta {
  family: string;                  // 'SOFR_FUT' | 'EUR_SHORT_RATE_FUT' | 'SONIA_FUT'
  marketShort: string;             // 'USD' | 'EUR' | 'GBP'
  /** Country / region label used in the extended ranked-detail table. */
  countryLabel: string;            // 'US' | 'Euro Area' | 'UK'
  /** Issuing central bank — surfaced on the index-family caveat card. */
  centralBank: string;             // 'Federal Reserve' | 'European Central Bank' | 'Bank of England'
  /** Short-rate regime — drives the RFR-vs-IBOR per-row disclosure tag
   *  required by ADR 0013. */
  shortRateRegime: 'RFR' | 'IBOR';
  /** Underlying reference rate (SOFR / Euribor 3M / SONIA). */
  referenceRate: string;
  flag: string;                    // 🇺🇸 / 🇪🇺 / 🇬🇧
}

const FAMILY_REGISTRY: Record<string, PolicyFuturesFamilyMeta> = {
  SOFR_FUT: {
    family: 'SOFR_FUT',
    marketShort: 'USD',
    countryLabel: 'US',
    centralBank: 'Federal Reserve',
    shortRateRegime: 'RFR',
    referenceRate: 'SOFR',
    flag: '🇺🇸',
  },
  EUR_SHORT_RATE_FUT: {
    family: 'EUR_SHORT_RATE_FUT',
    marketShort: 'EUR',
    countryLabel: 'Euro Area',
    centralBank: 'European Central Bank',
    shortRateRegime: 'IBOR',
    referenceRate: '3M Euribor',
    flag: '🇪🇺',
  },
  SONIA_FUT: {
    family: 'SONIA_FUT',
    marketShort: 'GBP',
    countryLabel: 'UK',
    centralBank: 'Bank of England',
    shortRateRegime: 'RFR',
    referenceRate: 'SONIA',
    flag: '🇬🇧',
  },
};

export function policyFuturesFamilyFor(
  family: string,
): PolicyFuturesFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** Full curve-family option set — used by the controls strip and Monitor
 *  widget. */
export const POLICY_FUTURES_SCANNER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.countryLabel} (${m.family})`,
  }));

/** Desk-canonical compact-mode caveat — short form rendered in the compact
 *  card's footer + the Monitor tile.  ADR 0013 V1 monitors-only scope:
 *  CTD-of-futures-of-OIS, term-premium decomposition, meeting-by-meeting
 *  policy-path extraction, and basis-vs-OIS reads are Phase-4 work — this
 *  caveat surfaces that load-bearing limit + the RFR-vs-IBOR
 *  heterogeneity on every surface.  The FULL multi-line caveat lives on
 *  the wire (response.methodology_disclosure) and is surfaced on the
 *  extended view's methodology card. */
export const POLICY_FUTURES_SCANNER_COMPACT_CAVEAT =
  'Fed · ECB · BoE meetings ahead. RFR (SOFR / SONIA) vs IBOR (Euribor).';

// ---------------------------------------------------------------------------
// Metric vocabulary.  The four metrics are CLOSED ENUM on the wire; this
// layer owns the human label, the short SCOPE chip the mockup shows, and
// the per-metric narrative qualifier.
// ---------------------------------------------------------------------------

export interface MetricMeta {
  metric: ScanPolicyFuturesMetric;
  /** Short SCOPE tag the mockup renders in the per-row chip (IR / Δ /
   *  VOL / OI). */
  scopeShort: string;
  /** Long label for the controls strip + methodology rows. */
  label: string;
  /** "IR z" / "Δ z" / "VOL z" / "OI z" — used in column headers. */
  shortDesc: string;
}

export const METRIC_REGISTRY: Record<ScanPolicyFuturesMetric, MetricMeta> = {
  implied_rate_level: {
    metric: 'implied_rate_level',
    scopeShort: 'IR',
    label: 'Implied-rate level',
    shortDesc: 'IR z',
  },
  implied_rate_change: {
    metric: 'implied_rate_change',
    scopeShort: 'Δ',
    label: '1-day implied-rate change (bps)',
    shortDesc: 'Δ z',
  },
  volume_level: {
    metric: 'volume_level',
    scopeShort: 'VOL',
    label: 'Volume level',
    shortDesc: 'Vol z',
  },
  open_interest_level: {
    metric: 'open_interest_level',
    scopeShort: 'OI',
    label: 'Open-interest level',
    shortDesc: 'OI z',
  },
};

export const METRIC_ORDER: ReadonlyArray<ScanPolicyFuturesMetric> = [
  'implied_rate_level',
  'implied_rate_change',
  'volume_level',
  'open_interest_level',
];

// ---------------------------------------------------------------------------
// Pack vocabulary — STIR strip-position conventions.  Positions 1–4 are
// "WHITES" (front year), 5–8 are "REDS" (second year).  The desk reads the
// pack alongside the stem (e.g. "SFR3 WHITES" vs "SFR6 REDS") to position
// the stem in calendar time.  Rendered as a chip in the compact + extended
// tables per the mockup.
// ---------------------------------------------------------------------------

export type StripPack = 'WHITES' | 'REDS';

export function stripPack(
  row: Pick<ScanPolicyFuturesExtremesResultRow, 'strip_position'>,
): StripPack {
  return row.strip_position <= 4 ? 'WHITES' : 'REDS';
}

// ---------------------------------------------------------------------------
// Per-row narrative qualifier — maps a (metric, signal) pair onto the
// desk-canonical phrase the mockup renders next to the value.  "Rate up" /
// "Rate down" for IR level (rates moving away from / toward zero); "Up
// move" / "Down move" for the 1-day Δ; "Surge" / "Quiet" for volume;
// "BUILD" / "UNWIND" for open interest.
// ---------------------------------------------------------------------------

export function narrativeTag(
  metric: ScanPolicyFuturesMetric,
  signal: 'EXTREME_HIGH' | 'EXTREME_LOW',
): string {
  switch (metric) {
    case 'implied_rate_level':
      return signal === 'EXTREME_HIGH' ? 'Rate stretched high' : 'Rate stretched low';
    case 'implied_rate_change':
      return signal === 'EXTREME_HIGH' ? 'Up move' : 'Down move';
    case 'volume_level':
      return signal === 'EXTREME_HIGH' ? 'Volume surge' : 'Quiet';
    case 'open_interest_level':
      return signal === 'EXTREME_HIGH' ? 'OI build' : 'OI unwind';
    default:
      return '';
  }
}

// ---------------------------------------------------------------------------
// Native-value formatter — picks the right snapshot field per metric.
//   - IR LEVEL → implied_rate_pct as "%"
//   - IR CHANGE → daily_change_implied_rate_bps as bps
//   - VOLUME / OI → current_volume / current_open_interest as K/M contracts
// ---------------------------------------------------------------------------

export function nativeValue(row: ScanPolicyFuturesExtremesResultRow): string {
  switch (row.metric) {
    case 'implied_rate_level':
      return formatRatePct(row.implied_rate_pct);
    case 'implied_rate_change':
      return formatBps(row.daily_change_implied_rate_bps);
    case 'volume_level':
      return formatContracts(row.current_volume);
    case 'open_interest_level':
      return formatContracts(row.current_open_interest);
    default:
      return '—';
  }
}

/** Per-row units qualifier for the extended table's UNITS column. */
export function nativeUnits(row: ScanPolicyFuturesExtremesResultRow): string {
  switch (row.metric) {
    case 'implied_rate_level':
      return 'Implied rate';
    case 'implied_rate_change':
      return 'bps';
    case 'volume_level':
    case 'open_interest_level':
      return 'contracts';
    default:
      return '';
  }
}

/** Per-row 1-day Δ display value — surfaces alongside the native value on
 *  the compact + extended tables.  IR-level + IR-change rows show the
 *  rate Δ in bps; OI rows show the contract-count Δ; volume rows show
 *  "—" because the backend does not expose a 1-day volume Δ field. */
export function dailyChangeDisplay(
  row: ScanPolicyFuturesExtremesResultRow,
): string {
  switch (row.metric) {
    case 'implied_rate_level':
    case 'implied_rate_change':
      return formatBpsSigned(row.daily_change_implied_rate_bps);
    case 'open_interest_level':
      return formatContractsSigned(row.delta_open_interest_1d);
    case 'volume_level':
      return '—';
    default:
      return '—';
  }
}

// ---------------------------------------------------------------------------
// Scan-summary parser.  The wire's ``scan_summary`` is a human-readable
// string (per compute._build_scan_summary).  Defensive parsing so the
// surfaces degrade gracefully on a future format change.
// ---------------------------------------------------------------------------

export interface ScanSummary {
  scanned: number | null;
  scoreable: number | null;
  /** Sum of flagged across all four metrics. */
  flaggedTotal: number | null;
  perMetricFlagged: Partial<Record<ScanPolicyFuturesMetric, number>>;
  /** Z-score threshold extracted from the summary (e.g. 1.5). */
  threshold: number | null;
  showingTotalRows: number | null;
  asOfSpanStart: string | null;
  asOfSpanEnd: string | null;
  raw: string;
}

export function parseScanSummary(summary: string): ScanSummary {
  const scannedMatch = summary.match(
    /Scanned\s+(\d+)\s+policy-futures\s+stems\s*\((\d+)\s+scoreable\)/i,
  );
  const thresholdMatch = summary.match(/\|z\|\s*>=\s*([\d.]+)/i);
  const perMetric: Partial<Record<ScanPolicyFuturesMetric, number>> = {};
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
  const asOfSingleMatch = summary.match(/as_of\s+(\d{4}-\d{2}-\d{2})\.?/i);
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
  rows: ReadonlyArray<ScanPolicyFuturesExtremesResultRow>,
): ReadonlyArray<ScanPolicyFuturesExtremesResultRow> {
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

export interface UsePolicyFuturesScannerArgs {
  curveFamilies?: string[];
  topN?: number;
  minAbsZScore?: number;
  metrics?: ScanPolicyFuturesMetric[];
  asOfDate?: string;
}

export interface UsePolicyFuturesScannerResult {
  data: ScanPolicyFuturesExtremesOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function usePolicyFuturesScanner(
  args: UsePolicyFuturesScannerArgs,
): UsePolicyFuturesScannerResult {
  const [data, setData] = useState<ScanPolicyFuturesExtremesOutput | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const curveFamiliesKey =
    args.curveFamilies && args.curveFamilies.length > 0
      ? args.curveFamilies.join(',')
      : undefined;
  const metricsKey =
    args.metrics && args.metrics.length > 0 ? args.metrics.join(',') : undefined;

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);
    const params: PolicyFuturesScannerDetailParams = {
      curve_families: curveFamiliesKey,
      top_n: args.topN,
      min_abs_z_score: args.minAbsZScore,
      metrics: metricsKey,
      as_of_date: args.asOfDate,
    };
    fetchDetailPolicyFuturesScanner(params)
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
  }, [curveFamiliesKey, args.topN, args.minAbsZScore, metricsKey, args.asOfDate]);

  return { data, isLoading, errorMessage };
}

// ---------------------------------------------------------------------------
// Format helpers — local to this tool because the STIR row-level
// formatting differs from the sibling scanners (rate in % with 2dp,
// bps Δ at 1dp, volume / OI in K/M contracts).
// ---------------------------------------------------------------------------

export function formatZScore(z: number | null | undefined): string {
  if (z == null || Number.isNaN(z)) return '—';
  const sign = z >= 0 ? '+' : '';
  return `${sign}${z.toFixed(1)}σ`;
}

export function formatRatePct(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return '—';
  return `${p.toFixed(2)}%`;
}

export function formatBps(b: number | null | undefined): string {
  if (b == null || Number.isNaN(b)) return '—';
  return `${b.toFixed(1)} bp`;
}

export function formatBpsSigned(b: number | null | undefined): string {
  if (b == null || Number.isNaN(b)) return '—';
  const sign = b >= 0 ? '+' : '';
  return `${sign}${b.toFixed(1)} bp`;
}

export function formatContracts(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toFixed(0);
}

export function formatContractsSigned(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = n >= 0 ? '+' : '−';
  const body = formatContracts(Math.abs(n));
  return `${sign}${body}`;
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
// is (curve_family, strip_position) — equivalently the master rolling-
// generic ``contract_code`` (SFR1 / ER1 / SFI3 / ...).
// ---------------------------------------------------------------------------

export function instrumentFlag(
  row: ScanPolicyFuturesExtremesResultRow,
): string {
  return policyFuturesFamilyFor(row.curve_family)?.flag ?? '';
}

export function instrumentCentralBank(
  row: ScanPolicyFuturesExtremesResultRow,
): string {
  return policyFuturesFamilyFor(row.curve_family)?.centralBank ?? row.curve_family;
}

export function instrumentCountry(
  row: ScanPolicyFuturesExtremesResultRow,
): string {
  return policyFuturesFamilyFor(row.curve_family)?.countryLabel ?? row.curve_family;
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
  rows: ReadonlyArray<ScanPolicyFuturesExtremesResultRow>,
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
  data: ScanPolicyFuturesExtremesOutput,
  summary: ScanSummary,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Universe',
      value:
        'SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT × strip positions 1..8 (rolling-generic STIR stems)',
    },
    {
      label: 'Metrics',
      value:
        'Implied-rate LEVEL · 1-day implied-rate CHANGE (bps) · Volume LEVEL · Open-interest LEVEL (each independently ranked)',
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
      label: 'Short-rate regime',
      value:
        'SOFR_FUT · SONIA_FUT = RFR (compounded daily risk-free rate); EUR_SHORT_RATE_FUT = IBOR (unsecured 3M term Euribor)',
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
