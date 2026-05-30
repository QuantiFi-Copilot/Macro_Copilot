// ============================================================================
// zcisScannerShared.ts — Per-tool helpers shared between BuildExtended.tsx,
// BuildCompact.tsx, and the Monitor widget for
// ``scan_inflation_swaps_extremes_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  SCANNER shape — the wire carries a ranked
// LIST of (curve_family, tenor) extremes ordered by |z| of the 252d-rolling
// ZCIS rate LEVEL z-score, NOT a single time series.  This helper module
// owns the row-shape adapters + summary parser + ZCIS family vocabulary the
// three surfaces consume.  All three surfaces fetch the SAME typed-detail
// endpoint per rendering_density.md §1.1 — the compact view just renders
// fewer rows (top-3) than the extended view.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailZcisScanner,
  type ZcisScannerDetailParams,
} from '@/services/ratesApi';
import type {
  ScanInflationSwapsExtremesOutput,
  ScanInflationSwapsExtremesResultRow,
} from '@/types/rates';
import type { MethodologyRow, ReferenceChip } from '@/components/shared/build';

// ---------------------------------------------------------------------------
// ZCIS curve-family vocabulary.  Mirrors the inflationSwapButterflyShared
// registry shape so the inflation_swaps tools share a consistent identity
// vocabulary, but is owned per-tool (no cross-module imports) per the dual-
// view contract.
// ---------------------------------------------------------------------------

export interface ZcisFamilyMeta {
  family: string;        // 'USD_ZCIS' | 'EUR_ZCIS' | 'GBP_ZCIS'
  marketShort: string;   // 'USD' | 'EUR' | 'GBP'
  indexShort: string;    // 'CPI-U' | 'HICPxT' | 'RPI'
  flag: string;          // 🇺🇸 / 🇪🇺 / 🇬🇧
}

const FAMILY_REGISTRY: Record<string, ZcisFamilyMeta> = {
  USD_ZCIS: { family: 'USD_ZCIS', marketShort: 'USD', indexShort: 'CPI-U', flag: '🇺🇸' },
  EUR_ZCIS: { family: 'EUR_ZCIS', marketShort: 'EUR', indexShort: 'HICPxT', flag: '🇪🇺' },
  GBP_ZCIS: { family: 'GBP_ZCIS', marketShort: 'GBP', indexShort: 'RPI', flag: '🇬🇧' },
};

export function zcisFamilyFor(family: string): ZcisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The full ZCIS curve-family option set — used by the controls strip and
 *  Monitor widget. */
export const ZCIS_SCANNER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.marketShort} · ${m.indexShort}`,
  }));

/** Desk-canonical compact-mode caveat — the SHORT form rendered in the
 *  compact card's footer + the Monitor tile.  The FULL multi-line caveat
 *  lives on the wire (response.methodology_disclosure) and is surfaced on
 *  the extended view's methodology card — this short form is its compact
 *  rendering, mirroring the inflation_swap_butterfly tool's pattern. */
export const ZCIS_SCANNER_COMPACT_CAVEAT =
  'Z-scores comparable; raw CPI-U / HICPxT / RPI rates are not.';

// ---------------------------------------------------------------------------
// Instrument-label formatter.  Mockup shows e.g. "USD 5Y CPI-U" — combines
// curve family market + tenor + underlying index family.  Falls back
// gracefully on missing registry entries (defensive — every wire row should
// resolve, but a future family addition shouldn't blow up the UI).
// ---------------------------------------------------------------------------

export function instrumentLabel(row: ScanInflationSwapsExtremesResultRow): string {
  const meta = zcisFamilyFor(row.curve_family);
  if (meta) return `${meta.marketShort} ${row.tenor} ${meta.indexShort}`;
  return `${row.curve_family} ${row.tenor}`;
}

export function instrumentFlag(row: ScanInflationSwapsExtremesResultRow): string {
  return zcisFamilyFor(row.curve_family)?.flag ?? '';
}

// ---------------------------------------------------------------------------
// Scan-summary parser.  The wire's ``scan_summary`` is a human-readable
// string like "Scanned 21 ZCIS stems (21 scoreable). Stems with |z| >= 1.5:
// 7. Showing top 5 by absolute ZCIS-rate z-score. as_of 2026-04-08."  The
// Extended view's scan-summary card and the Compact view's "X Flagged" chip
// pull structured counts from this string — defensive parsing with optional-
// chained fall-through (the string format is owned by compute._build_scan
// _summary; a future format change should still allow the surfaces to
// degrade gracefully to the raw string).
// ---------------------------------------------------------------------------

export interface ScanSummary {
  scanned: number | null;
  scoreable: number | null;
  flagged: number | null;
  /** Z-score threshold extracted from the summary (e.g. 1.5). */
  threshold: number | null;
  showing: number | null;
  asOfDate: string | null;
  raw: string;
}

export function parseScanSummary(summary: string): ScanSummary {
  const scannedMatch = summary.match(/Scanned\s+(\d+)\s+ZCIS\s+stems\s*\((\d+)\s+scoreable\)/i);
  const flaggedMatch = summary.match(/\|z\|\s*>=\s*([\d.]+)\s*:\s*(\d+)/i);
  const showingMatch = summary.match(/Showing\s+top\s+(\d+)/i);
  const asOfMatch = summary.match(/as_of\s+(\d{4}-\d{2}-\d{2})/i);
  return {
    scanned: scannedMatch ? Number(scannedMatch[1]) : null,
    scoreable: scannedMatch ? Number(scannedMatch[2]) : null,
    threshold: flaggedMatch ? Number(flaggedMatch[1]) : null,
    flagged: flaggedMatch ? Number(flaggedMatch[2]) : null,
    showing: showingMatch ? Number(showingMatch[1]) : null,
    asOfDate: asOfMatch ? asOfMatch[1] : null,
    raw: summary,
  };
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseZcisScannerArgs {
  curveFamilies?: string[];   // e.g. ['USD_ZCIS', 'EUR_ZCIS']
  topN?: number;
  minAbsZScore?: number;
  asOfDate?: string;
}

export interface UseZcisScannerResult {
  data: ScanInflationSwapsExtremesOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces. */
export function useZcisScanner(args: UseZcisScannerArgs): UseZcisScannerResult {
  const [data, setData] = useState<ScanInflationSwapsExtremesOutput | null>(null);
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
    const params: ZcisScannerDetailParams = {
      curve_families: curveFamiliesKey,
      top_n: args.topN,
      min_abs_z_score: args.minAbsZScore,
      as_of_date: args.asOfDate,
    };
    fetchDetailZcisScanner(params)
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
// Format helpers — local to this tool because the scanner's row-level
// formatting differs from the single-tool primitives (e.g. "+2.7σ" with
// fixed 1-decimal precision rather than "2.7" with no unit).
// ---------------------------------------------------------------------------

export function formatZScore(z: number | null | undefined): string {
  if (z == null || Number.isNaN(z)) return '—';
  const sign = z >= 0 ? '+' : '';
  return `${sign}${z.toFixed(1)}σ`;
}

export function formatRatePct(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return '—';
  return `${pct.toFixed(2)}%`;
}

export function formatChangeBps(bps: number | null | undefined): string {
  if (bps == null || Number.isNaN(bps)) return '—';
  const sign = bps >= 0 ? '+' : '';
  return `${sign}${bps.toFixed(1)}`;
}

/** Tone bucket for a row's z-score per rendering_density.md §2.2 (|z| ≥ 1.5
 *  → elevated/amber; |z| ≥ 2.0 → coral up / mint down).  Returns one of the
 *  closed ValueTone variants used by ``toneTextClass`` (re-exported from the
 *  shared build barrel). */
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
// Histogram bin builder — synthesises a coarse-bin distribution of the
// returned top-N rows' z-scores for the Extended view's distribution panel.
// The wire returns only the top-N (not the full universe), so this is the
// histogram OF THE FLAGGED EXTREMES — labelled accordingly in the chart
// kicker so the desk reader cannot mistake it for the full-universe
// distribution.  When the response was generated with a low threshold and N
// is comparable to the scanned count, the histogram approaches the universe
// shape; with the default top_n=5 it is sparse by design.
// ---------------------------------------------------------------------------

export interface ZScoreBin {
  /** Bin lower edge (z). */
  from: number;
  /** Bin upper edge (z). */
  to: number;
  /** Number of rows in this bin. */
  count: number;
  /** True when |z| ≥ 2.0 — used for tone-cueing the bin bar. */
  extreme: boolean;
  /** True when 1.5 ≤ |z| < 2.0 — amber tone. */
  elevated: boolean;
}

const HISTOGRAM_EDGES: ReadonlyArray<number> = [
  -3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0,
];

export function buildHistogramBins(
  rows: ReadonlyArray<ScanInflationSwapsExtremesResultRow>,
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
    if (r.z_score_zcis_rate == null) continue;
    const z = r.z_score_zcis_rate;
    for (const b of bins) {
      // Half-open [from, to); last bin is closed [from, to] to catch ±3.
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
// WIRE'S methodology_disclosure field (P5 / PR10: wire-honesty disclosure
// flows from compute() — NEVER hardcoded as a TS literal).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: ScanInflationSwapsExtremesOutput,
  summary: ScanSummary,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Universe',
      value: 'USD_ZCIS / EUR_ZCIS / GBP_ZCIS (21 ZCIS pillars across CPI-U / HICPxT / RPI)',
    },
    {
      label: 'Ranking',
      value: 'Top-N by |z-score| of the rolling 252d ZCIS rate LEVEL (single-metric scan)',
    },
    {
      label: 'Z-score window',
      value: summary.threshold != null
        ? `252 trading days · displaying stems with |z| ≥ ${summary.threshold}`
        : '252 trading days',
    },
    {
      label: 'Anchor',
      value: summary.asOfDate
        ? `Most-recent shared trading day across the universe (${summary.asOfDate})`
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
    { label: 'BLS CPI-U' },
    { label: 'Eurostat HICP' },
    { label: 'UK ONS RPI' },
    { label: 'Bloomberg ZCIS' },
    { label: 'Tuckman 4e Ch.22' },
  ];
}
