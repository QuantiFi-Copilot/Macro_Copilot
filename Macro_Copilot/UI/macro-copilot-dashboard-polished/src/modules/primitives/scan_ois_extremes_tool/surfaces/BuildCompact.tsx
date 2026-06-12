// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for ``scan_ois_extremes_tool``.
// SCANNER shape (OIS par-swap-rate universe).
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 every primitive
// claiming ``custom_build_surface`` ships a compact view; this one is a
// TOP-N RANKED TABLE (NOT a sparkline) per the SCANNER-shape guardrail.
// Mounted as a node body inside multi-tool DAG visualisations.
//
// Mirror of the sovereign twin
// ``../../scan_extremes_tool/surfaces/BuildCompact.tsx`` (P3 — the two
// LEVEL-metric scanner modules cannot drift).  RATE-space differences:
// the level column is ``current_rate_pct`` (par swap RATE, percent) and
// the change column is ``daily_change_bps`` — both rendered per the OIS
// market's native quote conventions from ./oisScannerShared.ts.
//
// The shared ``BuildCompactShell`` is LEVEL-shape oriented (3 KPI cells +
// sparkline + methodology footer); the scanner's canonical headline data is
// RANKED ROWS, not KPIs.  This component honours the SEMANTIC contract of
// rendering_density.md §2.2 (identity / headline data / methodology / expand
// / tone cues) with a scanner-shaped layout, reusing the shared
// ``FreshnessPill`` and ``toneTextClass`` helpers so visual treatment matches
// the rest of the compact catalogue.
// ============================================================================

import { ArrowUpRight, Info, Search } from 'lucide-react';
import {
  FreshnessPill,
  toneTextClass,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  SCAN_OIS_EXTREMES_COMPACT_CAVEAT,
  changeBpsTone,
  formatChangeBps,
  formatRatePct,
  formatZScore,
  instrumentFlag,
  instrumentMarketLabel,
  parseScanSummary,
  useOisScanner,
  zScoreTone,
} from './oisScannerShared';

// Number of rows the compact card surfaces.  Mirrors the sovereign twin:
// the card shows the top-5 ranked rows; anything beyond is reached via the
// expand affordance / "View all N" link.
const COMPACT_TOP_ROWS = 5;

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilies = parseCurveFamiliesParam(params.curve_families);
  const topN = parseOptionalNumber(params.top_n);
  const minAbsZ = parseOptionalNumber(params.min_abs_z_score);
  const fieldName = params.field_name || undefined;

  const { data, isLoading, errorMessage } = useOisScanner({
    curveFamilies,
    topN,
    minAbsZScore: minAbsZ,
    fieldName,
  });

  const summary = parseScanSummary(data?.scan_summary ?? '');
  const rows = data?.results ?? [];
  const visibleRows = rows.slice(0, COMPACT_TOP_ROWS);
  const totalAvailable = summary.flagged ?? rows.length;
  const asOfDate = rows[0]?.as_of_date ?? null;

  const minHeight = size === 'medium' ? 420 : 340;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="ois-scanner-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <Search size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">OIS EXTREMES</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SNAPSHOT</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span className="font-mono">
                {summary.threshold != null ? `|z| ≥ ${summary.threshold.toFixed(1)}σ` : '|z| ≥ 1.5σ'}
              </span>
              {totalAvailable > 0 && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span>{totalAvailable} Flagged</span>
                </>
              )}
              {callMeta && (
                <span className="ml-1 kicker text-fg-faint">
                  · call {callMeta.n} of {callMeta.m}
                </span>
              )}
            </div>
          </div>
        </div>
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            aria-label="Open extended view of OIS rate extremes"
            title="Open extended view"
            className="rounded-md border border-line-subtle bg-surface-overlay p-1.5 text-fg-muted transition-colors hover:border-line-strong hover:text-fg-primary"
          >
            <ArrowUpRight size={14} strokeWidth={1.75} />
          </button>
        )}
      </header>

      <hr className="border-t border-line-subtle" />

      {/* ---------- Body ---------- */}
      {errorMessage ? (
        <CompactError message={errorMessage} />
      ) : isLoading ? (
        <CompactSkeleton />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          {/* Column headings — RATE % is the par-swap-rate LEVEL (the OIS
              market's native percent quote); Δ stays in BPS, same as the
              sovereign twin. */}
          <div className="grid grid-cols-[20px_1.4fr_0.5fr_auto_auto_auto] items-center gap-2 px-1 text-[10.5px] uppercase tracking-wide text-fg-muted">
            <span>#</span>
            <span>Market</span>
            <span>Tenor</span>
            <span className="text-right">Rate %</span>
            <span className="text-right">Δ (bp)</span>
            <span className="text-right">Z-Score (252d)</span>
          </div>

          {/* Ranked rows */}
          <div className="flex flex-col">
            {visibleRows.length === 0 ? (
              <div className="px-1 py-3 text-[12px] text-fg-secondary">
                No stems passed the threshold for this scan.
              </div>
            ) : (
              visibleRows.map((row) => {
                const tone = zScoreTone(row.z_score);
                return (
                  <div
                    key={`${row.curve_family}-${row.tenor}-${row.rank}`}
                    className="grid grid-cols-[20px_1.4fr_0.5fr_auto_auto_auto] items-center gap-2 border-t border-line-subtle px-1 py-1.5 text-[12.5px]"
                  >
                    <span
                      className={`font-mono text-[12px] ${toneTextClass(tone)}`}
                    >
                      {row.rank}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5 text-fg-primary">
                      {instrumentFlag(row) && (
                        <span className="text-[14px] leading-none">
                          {instrumentFlag(row)}
                        </span>
                      )}
                      <span className="truncate">{instrumentMarketLabel(row)}</span>
                    </span>
                    <span className="font-mono text-[12px] text-fg-secondary">
                      {row.tenor}
                    </span>
                    <span className="text-right font-mono tabular-nums text-fg-primary">
                      {formatRatePct(row.current_rate_pct)}
                    </span>
                    <span
                      className={`text-right font-mono tabular-nums ${toneTextClass(changeBpsTone(row.daily_change_bps))}`}
                    >
                      {formatChangeBps(row.daily_change_bps)}
                    </span>
                    <span
                      className={`text-right font-mono tabular-nums ${toneTextClass(tone)}`}
                    >
                      {formatZScore(row.z_score)}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={SCAN_OIS_EXTREMES_COMPACT_CAVEAT}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{SCAN_OIS_EXTREMES_COMPACT_CAVEAT}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {onExpand && totalAvailable > visibleRows.length && (
            <button
              type="button"
              onClick={onExpand}
              className="text-ice-300 underline-offset-2 hover:underline"
            >
              View all {totalAvailable} →
            </button>
          )}
          {asOfDate && <span>As of {asOfDate}</span>}
          <FreshnessPill freshness="fresh" />
        </div>
      </footer>
    </article>
  );
};

export default BuildCompact;

// ---------------------------------------------------------------------------
// Param utilities — convert DAG-node string params into typed scanner inputs.
// ---------------------------------------------------------------------------

function parseCurveFamiliesParam(raw: string | undefined): string[] | undefined {
  if (!raw) return undefined;
  if (raw === '__ALL__') return undefined;
  const parts = raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return parts.length > 0 ? parts : undefined;
}

function parseOptionalNumber(raw: string | undefined): number | undefined {
  if (raw == null || raw === '') return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function CompactSkeleton() {
  return (
    <div className="flex min-h-[180px] flex-1 flex-col gap-2">
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="grid grid-cols-[20px_1.4fr_0.5fr_auto_auto_auto] items-center gap-2 border-t border-line-subtle px-1 py-1.5"
        >
          <div className="h-3 w-4 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-24 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-8 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-12 animate-pulse rounded bg-line-subtle justify-self-end" />
          <div className="h-3 w-10 animate-pulse rounded bg-line-subtle justify-self-end" />
          <div className="h-3 w-12 animate-pulse rounded bg-line-subtle justify-self-end" />
        </div>
      ))}
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[180px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
