// ============================================================================
// scanExtremesShared.ts — Per-tool helpers shared between BuildExtended.tsx
// and BuildCompact.tsx for ``scan_extremes_tool``.
// ----------------------------------------------------------------------------
// Migration-mode helper (MIGRATION_RULES.md §4 step 5): the preserved
// ``ScannerWidget`` keeps its existing ``useRatesDataContext`` data source so
// dashboards that mounted the widget keep rendering identically.  The Build
// surfaces consume the per-tool typed-detail endpoint
// ``/api/v1/rates/detail/scanner`` via the central ``fetchDetailScanner``
// helper.  Naming mirrors the reference twin
// ``scan_inflation_swaps_extremes_tool/surfaces/zcisScannerShared.ts`` so
// the SCANNER-shape pattern stays consistent across families.
//
// SCANNER shape — the wire carries a ranked LIST of (curve_family, tenor)
// extremes ordered by |z| of the 252d-rolling YLD_YTM_MID z-score across the
// sovereign-benchmark universe, NOT a single time series.  Both Build
// surfaces share the registry + format helpers below; only their density
// (top-N table vs universe scan) differs.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  fetchDetailScanner,
  type ScanExtremesDetailParams,
} from '@/services/ratesApi';
import type {
  ScanExtremesOutput,
  ScanExtremesResultRow,
} from '@/types/rates';
import type { MethodologyRow, ReferenceChip } from '@/components/shared/build';

// ---------------------------------------------------------------------------
// Sovereign curve-family registry.  Mirrors the legacy ``ScannerWidget``
// CURVE_SHORT map (preserved verbatim so the existing dashboards still resolve
// the same labels) and adds country + flag metadata for the mockup's COUNTRY
// column.  Per-tool source-of-truth — no cross-module imports per the dual-
// view contract.
// ---------------------------------------------------------------------------

export interface SovereignFamilyMeta {
  family: string;        // 'UST' | 'DE_BUND' | ...
  countryCode: string;   // 'US' | 'DE' | 'UK' | ...
  countryName: string;   // 'US' | 'Germany' | 'UK' | ...
  curveShort: string;    // 'UST' | 'Bund' | 'Gilt' | ...
  flag: string;          // 🇺🇸 / 🇩🇪 / 🇬🇧 / ...
}

const FAMILY_REGISTRY: Record<string, SovereignFamilyMeta> = {
  UST:          { family: 'UST',          countryCode: 'US', countryName: 'US',        curveShort: 'UST',  flag: '🇺🇸' },
  DE_BUND:      { family: 'DE_BUND',      countryCode: 'DE', countryName: 'Germany',   curveShort: 'Bund', flag: '🇩🇪' },
  UK_GILT:      { family: 'UK_GILT',      countryCode: 'UK', countryName: 'UK',        curveShort: 'Gilt', flag: '🇬🇧' },
  JP_JGB:       { family: 'JP_JGB',       countryCode: 'JP', countryName: 'Japan',     curveShort: 'JGB',  flag: '🇯🇵' },
  FR_OAT:       { family: 'FR_OAT',       countryCode: 'FR', countryName: 'France',    curveShort: 'OAT',  flag: '🇫🇷' },
  IT_BTP:       { family: 'IT_BTP',       countryCode: 'IT', countryName: 'Italy',     curveShort: 'BTP',  flag: '🇮🇹' },
  ES_BONO:      { family: 'ES_BONO',      countryCode: 'ES', countryName: 'Spain',     curveShort: 'Bono', flag: '🇪🇸' },
  AU_GOVT:      { family: 'AU_GOVT',      countryCode: 'AU', countryName: 'Australia', curveShort: 'AUS',  flag: '🇦🇺' },
  CANADA_GOVT:  { family: 'CANADA_GOVT',  countryCode: 'CA', countryName: 'Canada',    curveShort: 'CAN',  flag: '🇨🇦' },
};

export function sovereignFamilyFor(family: string): SovereignFamilyMeta | null {
  return FAMILY_REGISTRY[family] ?? null;
}

/** The full sovereign curve-family option set — used by the extended view's
 *  controls strip and the Monitor catalog scope dropdown. */
export const SOVEREIGN_SCANNER_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> =
  Object.values(FAMILY_REGISTRY).map((m) => ({
    value: m.family,
    label: `${m.countryName} · ${m.curveShort}`,
  }));

// ---------------------------------------------------------------------------
// Desk-canonical compact caveat — the SHORT form rendered in the compact
// card's footer.  The Pydantic ``ScannerOutput`` does NOT carry a wire
// ``methodology_disclosure`` field today (the sibling scanners DO; see the
// swap_spread / OIS family precedent), so the methodology card on the
// extended view synthesises the disclosure from this constant + the YAML's
// what_it_does / known_caveats verbatim.  When the backend ships
// ``methodology_disclosure`` on the ScannerOutput, this constant becomes the
// compact-only short form and the extended view switches to the wire field.
// ---------------------------------------------------------------------------

export const SCAN_EXTREMES_COMPACT_CAVEAT =
  'Cross-instrument z-scores are comparable; 252d window locked.';

// ---------------------------------------------------------------------------
// Row helpers — country/flag formatting for the mockup-faithful "COUNTRY"
// column.  Falls back gracefully on rows whose curve_family is absent from
// the registry (defensive — every shipped row should resolve, but a future
// family addition should not blow up the UI).
// ---------------------------------------------------------------------------

export function instrumentCountryLabel(row: ScanExtremesResultRow): string {
  const meta = sovereignFamilyFor(row.curve_family);
  if (meta) return `${meta.countryName} (${meta.countryCode})`;
  return row.curve_family;
}

export function instrumentFlag(row: ScanExtremesResultRow): string {
  return sovereignFamilyFor(row.curve_family)?.flag ?? '';
}

export function instrumentCurveShort(row: ScanExtremesResultRow): string {
  return sovereignFamilyFor(row.curve_family)?.curveShort ?? row.curve_family;
}

// ---------------------------------------------------------------------------
// Scan-summary parser.  The wire's ``scan_summary`` is a human-readable
// string like "Scanned 36 instruments.  Found 6 with |z-score| >= 1.5.
// Showing top 5 by absolute z-score."  The Extended view's scan-summary card
// and the Compact view's "X Flagged" chip pull structured counts from this
// string — defensive parsing with optional-chained fall-through.
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
  const scannedMatch = summary.match(/Scanned\s+(\d+)\s+instruments/i);
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

export interface UseScanExtremesArgs {
  curveFamilies?: string[];   // e.g. ['UST', 'DE_BUND']
  topN?: number;
  minAbsZScore?: number;
  fieldName?: string;
}

export interface UseScanExtremesResult {
  data: ScanExtremesOutput | null;
  isLoading: boolean;
  errorMessage: string | null;
}

export function useScanExtremes(args: UseScanExtremesArgs): UseScanExtremesResult {
  const [data, setData] = useState<ScanExtremesOutput | null>(null);
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
    const params: ScanExtremesDetailParams = {
      curve_families: curveFamiliesKey,
      top_n: args.topN,
      min_abs_z_score: args.minAbsZScore,
      field_name: args.fieldName,
    };
    fetchDetailScanner(params)
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
  }, [curveFamiliesKey, args.topN, args.minAbsZScore, args.fieldName]);

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

export function formatYieldPct(pct: number | null | undefined): string {
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

/** Tone for a daily-change BPS cell — sovereign-yield convention: a yield
 *  increase is coral (selling pressure), a yield decrease is mint. */
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
  rows: ReadonlyArray<ScanExtremesResultRow>,
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
// ``ScannerOutput`` does NOT carry a wire ``methodology_disclosure`` field
// today (unlike its sibling scanners), so the rows below synthesise the
// disclosure from the YAML's published conventions verbatim — every value
// is anchored to the substantive backend constant (252d window, 60-period
// min, sovereign-benchmark universe) rather than to a TS literal of the
// caveat itself.  When the backend grows a wire ``methodology_disclosure``
// the last row switches to that field (mirrors the swap_spread / OIS
// precedent noted in detail.py).
// ---------------------------------------------------------------------------

export function buildMethodologyRows(
  data: ScanExtremesOutput,
  summary: ScanSummary,
): ReadonlyArray<MethodologyRow> {
  const rows: MethodologyRow[] = [
    {
      label: 'Universe',
      value:
        'Sovereign benchmark curves (UST / DE_BUND / UK_GILT / FR_OAT / IT_BTP / ES_BONO / JP_JGB / AU_GOVT / CANADA_GOVT)',
    },
    {
      label: 'Ranking',
      value:
        'Top-N by |z-score| of the rolling 252d YLD_YTM_MID LEVEL (single-metric scan)',
    },
    {
      label: 'Z-score window',
      value:
        summary.threshold != null
          ? `252 trading days · min_periods 60 · displaying stems with |z| ≥ ${summary.threshold}`
          : '252 trading days · min_periods 60',
    },
    {
      label: 'Caveats',
      value:
        'Rolling, not cross-sectional — a curve at 2σ on its own history may not be an outlier vs peers. Forward-fill capped at 5 trading days; sparse curves are dropped from the universe.',
    },
  ];
  return rows;
}

export function buildReferenceChips(): ReadonlyArray<ReferenceChip> {
  return [
    { label: 'Bloomberg YLD_YTM_MID' },
    { label: 'macro_data.v_market_data_daily_enriched' },
    { label: 'Tuckman 4e Ch.6' },
  ];
}
