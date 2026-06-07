// ============================================================================
// scanInflationLinkersExtremesShared.ts — Per-tool helpers shared between
// BuildExtended.tsx, BuildCompact.tsx, and the Monitor widget for
// ``scan_inflation_linkers_extremes_tool``.
// ----------------------------------------------------------------------------
// FINANCE-AWARE per-tool layer.  SCANNER shape — the wire carries a ranked
// LIST of (curve_family, tenor) extremes ordered by |z| of the 252d-rolling
// REAL-YIELD LEVEL z-score, NOT a single time series.  Mirrors the
// scan_inflation_swaps_extremes scanner sibling but indexed on the linker
// curve-family registry (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB).
// All three surfaces fetch the SAME typed-detail endpoint per
// rendering_density.md §1.1.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailLinkersScanner,
  type LinkersScannerDetailParams,
} from '@/services/ratesApi';
import type {
  ScanInflationLinkersExtremesOutput,
  ScanInflationLinkersExtremesResultRow,
} from '@/types/rates';
import type { MethodologyRow, ReferenceChip } from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Linker curve-family vocabulary.  Mirrors the zcisScannerShared registry
// shape so the scanner family shares an identity vocabulary, but is owned
// per-tool (no cross-module imports) per the dual-view contract.
// ---------------------------------------------------------------------------

export interface LinkerFamilyMeta {
  family: string;        // 'USD_TIPS' | 'GBP_LINKER' | 'EUR_FR_LINKER' | 'CAD_RRB'
  marketShort: string;   // 'USD' | 'GBP' | 'EUR_FR' | 'CAD'
  indexShort: string;    // 'CPI-U' | 'RPI' | 'HICP' | 'CAN CPI'
  flag: string;          // 🇺🇸 / 🇬🇧 / 🇫🇷 / 🇨🇦
}

const FAMILY_REGISTRY: Record<string, LinkerFamilyMeta> = {
  USD_TIPS:       { family: 'USD_TIPS',       marketShort: 'USD',    indexShort: 'CPI-U',    flag: '🇺🇸' },
  GBP_LINKER:     { family: 'GBP_LINKER',     marketShort: 'GBP',    indexShort: 'RPI',      flag: '🇬🇧' },
  EUR_FR_LINKER:  { family: 'EUR_FR_LINKER',  marketShort: 'EUR_FR', indexShort: 'HICP',     flag: '🇫🇷' },
  CAD_RRB:        { family: 'CAD_RRB',        marketShort: 'CAD',    indexShort: 'CAN CPI',  flag: '🇨🇦' },
};

export function linkerFamilyFor(family: string): LinkerFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The full linker curve-family option set — used by the controls strip
 *  and Monitor widget. */
export const LINKER_SCANNER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.marketShort} · ${m.indexShort}`,
  }));

/** Desk-canonical compact-mode caveat — the SHORT form rendered in the
 *  compact card's footer + the Monitor tile.  The FULL multi-line caveat
 *  lives on the wire (response.methodology_disclosure) and is surfaced on
 *  the extended view's methodology card — this short form is the compact
 *  rendering, mirroring the ZCIS scanner pattern.  REAL-YIELD scan
 *  (NOT a breakeven scan) — the index-family + market-structure caveats
 *  are load-bearing per the catalog guardrail. */
export const LINKER_SCANNER_COMPACT_CAVEAT =
  'Z-scores comparable; index families (CPI-U / RPI / HICP / CAN CPI) and market structures differ.';

// ---------------------------------------------------------------------------
// Instrument-label formatter.  Combines curve-family market + tenor + index
// family.  Falls back gracefully on missing registry entries (defensive —
// every wire row should resolve, but a future family addition shouldn't
// blow up the UI).
// ---------------------------------------------------------------------------

export function instrumentLabel(row: ScanInflationLinkersExtremesResultRow): string {
  const meta = linkerFamilyFor(row.curve_family);
  if (meta) return `${meta.marketShort} ${row.tenor} ${meta.indexShort}`;
  return `${row.curve_family} ${row.tenor}`;
}

export function instrumentFlag(row: ScanInflationLinkersExtremesResultRow): string {
  return linkerFamilyFor(row.curve_family)?.flag ?? '';
}

// ---------------------------------------------------------------------------
// Scan-summary parser.  The wire's ``scan_summary`` is a human-readable
// string like "Scanned 24 linker stems (22 scoreable). Stems with |z| >=
// 1.5: 7. Showing top 5 by absolute real-yield z-score. as_of_dates span
// 2026-04-07 to 2026-04-08."  The Extended view's scan-summary card and
// the Compact view's "X Flagged" chip pull structured counts from this
// string — defensive parsing with optional-chained fall-through (the
// string format is owned by compute._build_scan_summary; a future format
// change should still allow the surfaces to degrade gracefully).
// ---------------------------------------------------------------------------

export interface ScanSummary {
  scanned: number | null;
  scoreable: number | null;
  flagged: number | null;
  /** Z-score threshold extracted from the summary (e.g. 1.5). */
  threshold: number | null;
  showing: number | null;
  asOfSpanStart: string | null;
  asOfSpanEnd: string | null;
  raw: string;
}

export function parseScanSummary(summary: string): ScanSummary {
  const scannedMatch = summary.match(/Scanned\s+(\d+)\s+linker\s+stems\s*\((\d+)\s+scoreable\)/i);
  const flaggedMatch = summary.match(/\|z\|\s*>=\s*([\d.]+)\s*:\s*(\d+)/i);
  const showingMatch = summary.match(/Showing\s+top\s+(\d+)/i);
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
    threshold: flaggedMatch ? Number(flaggedMatch[1]) : null,
    flagged: flaggedMatch ? Number(flaggedMatch[2]) : null,
    showing: showingMatch ? Number(showingMatch[1]) : null,
    asOfSpanStart: asOfStart,
    asOfSpanEnd: asOfEnd,
    raw: summary,
  };
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

export interface UseLinkersScannerArgs {
  curveFamilies?: string[];   // e.g. ['USD_TIPS', 'GBP_LINKER']
  topN?: number;
  minAbsZScore?: number;
  asOfDate?: string;
}

export interface UseLinkersScannerResult {
  data: ScanInflationLinkersExtremesOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

/** Single-source hook used by all three surfaces. */
export function useLinkersScanner(args: UseLinkersScannerArgs): UseLinkersScannerResult {
  const [data, setData] = useState<ScanInflationLinkersExtremesOutput | null>(null);
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
    const params: LinkersScannerDetailParams = {
      curve_families: curveFamiliesKey,
      top_n: args.topN,
      min_abs_z_score: args.minAbsZScore,
      as_of_date: args.asOfDate,
    };
    fetchDetailLinkersScanner(params)
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

export function formatRealYieldPct(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return '—';
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

export function formatChangeBps(bps: number | null | undefined): string {
  if (bps == null || Number.isNaN(bps)) return '—';
  const sign = bps >= 0 ? '+' : '';
  return `${sign}${bps.toFixed(1)}`;
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

/** Sovereign-yield sign convention: an upside move in REAL YIELDS is
 *  "negative" tone (red/coral) to mirror the duration-side colouring
 *  used across the rates catalogue. */
export function toneForChangeBps(
  bps: number | null | undefined,
): 'neutral' | 'positive' | 'negative' {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'negative' : 'positive';
}

// ---------------------------------------------------------------------------
// Histogram bin builder — synthesises a coarse-bin distribution of the
// returned top-N rows' z-scores for the Extended view's distribution panel.
// The wire returns only the top-N (not the full universe), so this is the
// histogram OF THE FLAGGED EXTREMES — labelled accordingly so the desk
// reader cannot mistake it for the full-universe distribution.
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
  rows: ReadonlyArray<ScanInflationLinkersExtremesResultRow>,
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
    if (r.z_score_real_yield == null) continue;
    const z = r.z_score_real_yield;
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
  data: ScanInflationLinkersExtremesOutput,
  summary: ScanSummary,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Universe',
      value:
        'USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB (linker pillars across CPI-U / RPI / HICP / CAN CPI)',
    },
    {
      label: 'Ranking',
      value: 'Top-N by |z-score| of the rolling 252d REAL-YIELD LEVEL (single-metric scan)',
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
    { label: 'BLS CPI-U' },
    { label: 'Eurostat HICP' },
    { label: 'UK ONS RPI' },
    { label: 'StatsCan CPI' },
    { label: 'Bloomberg YLD_YTM_MID' },
  ];
}
