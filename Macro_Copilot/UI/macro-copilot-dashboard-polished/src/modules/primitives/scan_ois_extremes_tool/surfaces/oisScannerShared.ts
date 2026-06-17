// ============================================================================
// oisScannerShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``scan_ois_extremes_tool``.
// ----------------------------------------------------------------------------
// OIS analogue of the sovereign twin
// ``scan_extremes_tool/surfaces/scanExtremesShared.ts`` (mirrored
// file-for-file so the two LEVEL-metric scanner modules cannot drift).
// The Build surfaces consume the per-tool typed-detail endpoint
// ``/api/v1/rates/detail/ois-scanner`` via the central
// ``fetchDetailOisScanner`` helper.
//
// SCANNER shape — the wire carries a ranked LIST of (curve_family, tenor)
// extremes ordered by |z| of the 252d-rolling PX_LAST par-swap-RATE z-score
// across the OIS universe, NOT a single time series.  Both Build surfaces
// share the registry + format helpers below; only their density (top-N
// table vs universe scan) differs.
//
// RATE-space honesty: the row's level field is ``current_rate_pct`` — a par
// swap RATE in PERCENT (the OIS market's native quote), NOT a bond yield.
// Daily changes stay in BPS (``daily_change_bps``), same as the sovereign
// twin.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailOisScanner,
  type OisScannerDetailParams,
} from '@/services/ratesApi';
import type {
  ScanOisExtremesOutput,
  ScanOisExtremesResultRow,
} from '@/types/rates';
import type { MethodologyRow, ReferenceChip } from '@/components/shared/build';

// ---------------------------------------------------------------------------
// OIS curve-family registry.  Mirrors the closed ``OIS_CURVE_FAMILY``
// universe sourced from rates_agent/playbooks/ois.yml (USD_SOFR_OIS /
// EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) and the
// per-family vocabulary the sibling ``get_ois_rate_level_tool`` renders.
// Per-tool source-of-truth — no cross-module imports per the dual-view
// contract.
// ---------------------------------------------------------------------------

export interface OisFamilyMeta {
  family: string;       // 'USD_SOFR_OIS' | 'EUR_ESTR_OIS' | ...
  marketShort: string;  // 'USD' | 'EUR' | 'GBP' | ...
  indexShort: string;   // 'SOFR' | 'ESTR' | 'SONIA' | 'TONA' | 'AONIA' | 'CORRA'
  flag: string;         // 🇺🇸 / 🇪🇺 / 🇬🇧 / 🇯🇵 / 🇦🇺 / 🇨🇦
}

const FAMILY_REGISTRY: Record<string, OisFamilyMeta> = {
  USD_SOFR_OIS:  { family: 'USD_SOFR_OIS',  marketShort: 'USD', indexShort: 'SOFR',  flag: '🇺🇸' },
  EUR_ESTR_OIS:  { family: 'EUR_ESTR_OIS',  marketShort: 'EUR', indexShort: 'ESTR',  flag: '🇪🇺' },
  GBP_SONIA_OIS: { family: 'GBP_SONIA_OIS', marketShort: 'GBP', indexShort: 'SONIA', flag: '🇬🇧' },
  JPY_OIS:       { family: 'JPY_OIS',       marketShort: 'JPY', indexShort: 'TONA',  flag: '🇯🇵' },
  AUD_OIS:       { family: 'AUD_OIS',       marketShort: 'AUD', indexShort: 'AONIA', flag: '🇦🇺' },
  CAD_OIS:       { family: 'CAD_OIS',       marketShort: 'CAD', indexShort: 'CORRA', flag: '🇨🇦' },
};

export function oisFamilyFor(family: string): OisFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The full OIS curve-family option set — used by the extended view's
 *  controls strip. */
export const OIS_SCANNER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.marketShort} · ${m.indexShort}`,
  }));

// ---------------------------------------------------------------------------
// Desk-canonical compact caveat — the SHORT form rendered in the compact
// card's footer.  The Pydantic ``OISScannerOutput`` does NOT carry a wire
// ``methodology_disclosure`` field today (same gap as the sovereign twin's
// ``ScannerOutput``; the multi-metric futures scanners DO), so the
// methodology card on the extended view synthesises the disclosure from
// this constant + the YAML's published conventions verbatim.  When the
// backend ships ``methodology_disclosure`` on the OISScannerOutput, this
// constant becomes the compact-only short form and the extended view
// switches to the wire field.
// ---------------------------------------------------------------------------

export const SCAN_OIS_EXTREMES_COMPACT_CAVEAT =
  'OIS prices the expected policy path; 252d window locked.';

// ---------------------------------------------------------------------------
// Row helpers — market/flag formatting for the "MARKET" column.  Falls back
// gracefully on rows whose curve_family is absent from the registry
// (defensive — every shipped row should resolve, but a future family
// addition should not blow up the UI).
// ---------------------------------------------------------------------------

export function instrumentMarketLabel(row: ScanOisExtremesResultRow): string {
  const meta = oisFamilyFor(row.curve_family);
  if (meta) return `${meta.marketShort} (${meta.indexShort})`;
  return row.curve_family;
}

export function instrumentFlag(row: ScanOisExtremesResultRow): string {
  return oisFamilyFor(row.curve_family)?.flag ?? '';
}

export function instrumentIndexShort(row: ScanOisExtremesResultRow): string {
  return oisFamilyFor(row.curve_family)?.indexShort ?? row.curve_family;
}

// ---------------------------------------------------------------------------
// Scan-summary parser.  The wire's ``scan_summary`` is a human-readable
// string like "Scanned 42 OIS instruments.  Found 7 with |z-score| >= 1.5.
// Showing top 10 by absolute z-score."  The Extended view's scan-summary
// card and the Compact view's "X Flagged" chip pull structured counts from
// this string — defensive parsing with optional-chained fall-through.
// (The OIS compute interpolates "OIS instruments" where the sovereign twin
// says "instruments" — the regex tolerates both.)
// ---------------------------------------------------------------------------

export interface ScanSummary {
  scanned: number | null;
  flagged: number | null;
  /** Z-score threshold extracted from the summary (e.g. 1.5). */
  threshold: number | null;
  showing: number | null;
  raw: string;
}

export function parseScanSummary(summary: string): ScanSummary {
  const scannedMatch = summary.match(/Scanned\s+(\d+)\s+(?:OIS\s+)?instruments/i);
  const flaggedMatch = summary.match(/Found\s+(\d+)\s+with\s*\|z-score\|\s*>=?\s*([\d.]+)/i);
  const showingMatch = summary.match(/Showing\s+top\s+(\d+)/i);
  return {
    scanned: scannedMatch ? Number(scannedMatch[1]) : null,
    flagged: flaggedMatch ? Number(flaggedMatch[1]) : null,
    threshold: flaggedMatch ? Number(flaggedMatch[2]) : null,
    showing: showingMatch ? Number(showingMatch[1]) : null,
    raw: summary,
  };
}

// ---------------------------------------------------------------------------
// Data hook — single source used by both Build surfaces.
// ---------------------------------------------------------------------------

export interface UseOisScannerArgs {
  curveFamilies?: string[];   // e.g. ['USD_SOFR_OIS', 'EUR_ESTR_OIS']
  topN?: number;
  minAbsZScore?: number;
  fieldName?: string;
  /** As-of trade date (YYYY-MM-DD).  Undefined/empty → latest live data. */
  asOfDate?: string;
}

export interface UseOisScannerResult {
  data: ScanOisExtremesOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useOisScanner(args: UseOisScannerArgs): UseOisScannerResult {
  const [data, setData] = useState<ScanOisExtremesOutput | null>(null);
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
    const params: OisScannerDetailParams = {
      curve_families: curveFamiliesKey,
      top_n: args.topN,
      min_abs_z_score: args.minAbsZScore,
      field_name: args.fieldName,
      as_of_date: args.asOfDate || undefined,
    };
    fetchDetailOisScanner(params)
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
  }, [curveFamiliesKey, args.topN, args.minAbsZScore, args.fieldName, args.asOfDate]);

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

/** Par swap RATE in percent — the OIS market's native level quote. */
export function formatRatePct(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return '—';
  return `${pct.toFixed(2)}%`;
}

export function formatChangeBps(bps: number | null | undefined): string {
  if (bps == null || Number.isNaN(bps)) return '—';
  const sign = bps >= 0 ? '+' : '';
  return `${sign}${bps.toFixed(1)}`;
}

export function formatPercentile(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return '—';
  return `${pct.toFixed(0)}%`;
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

/** Tone for a daily-change BPS cell — rates convention shared with the
 *  sovereign twin: a rate increase is coral (policy-path repricing higher),
 *  a rate decrease is mint. */
export function changeBpsTone(
  bps: number | null | undefined,
): 'neutral' | 'positive' | 'negative' {
  if (bps == null || Number.isNaN(bps) || bps === 0) return 'neutral';
  return bps > 0 ? 'negative' : 'positive';
}

// ---------------------------------------------------------------------------
// Histogram bin builder — synthesises a coarse-bin distribution of the
// returned top-N rows' z-scores for the Extended view's distribution panel.
// The wire returns only the top-N (not the full universe), so this is the
// histogram OF THE FLAGGED EXTREMES — labelled accordingly in the chart
// kicker so the desk reader cannot mistake it for the full-universe
// distribution.
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
  rows: ReadonlyArray<ScanOisExtremesResultRow>,
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
// Methodology rows + references for the Extended view.  The Pydantic
// ``OISScannerOutput`` does NOT carry a wire ``methodology_disclosure``
// field today (same gap as the sovereign twin), so the rows below
// synthesise the disclosure from the YAML's published conventions verbatim
// — every value is anchored to the substantive backend constant (252d
// window, OIS universe, PX_LAST par swap rate) rather than to a TS literal
// of the caveat itself.  When the backend grows a wire
// ``methodology_disclosure`` the last row switches to that field (mirrors
// the swap_spread / bond-futures scanner precedent noted in detail.py).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: ScanOisExtremesOutput,
  summary: ScanSummary,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Universe',
      value:
        'OIS par-swap curves (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS)',
    },
    {
      label: 'Ranking',
      value:
        'Top-N by |z-score| of the rolling 252d PX_LAST par-swap-rate LEVEL (single-metric scan)',
    },
    {
      label: 'Z-score window',
      value:
        summary.threshold != null
          ? `252 trading days · displaying stems with |z| ≥ ${summary.threshold}`
          : '252 trading days',
    },
    {
      label: 'Caveats',
      value:
        'Rolling, not cross-sectional — a curve at 2σ on its own history may not be an outlier vs peers. OIS prices the risk-neutral expected policy path, not realised outcomes; an extreme rate level is a repricing of expectations, not a realised-rate fact.',
    },
  ];
  return rows;
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Bloomberg PX_LAST' },
    { label: 'macro_data.v_market_data_daily_enriched' },
    { label: 'rates_agent/playbooks/ois.yml' },
  ];
}
