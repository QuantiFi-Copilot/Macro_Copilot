// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// ``scan_bond_futures_extremes_tool``.  SCANNER shape (multi-metric).
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 every new
// primitive ships a compact view; this one is a TOP-N RANKED TABLE
// (NOT a sparkline) per the SCANNER guardrail.  Mounted as a node body
// inside multi-tool query DAG visualisations.
//
// Design reference: ./mockups/Compact.png.
//
// The wire is MULTI-METRIC (price LEVEL / 1-day price CHANGE / volume
// LEVEL / open-interest LEVEL).  This compact card surfaces the
// universe-wide extremity by sorting ACROSS metrics by |z|, then renders
// each top row with a SCOPE chip (PRICE / VOL / OI / Δ) so the desk can
// see WHICH metric is stretched at a glance.  Mirrors the mockup's
// PRICE / OI / VOL / PRICE row sequence + the "MOST EXTREME" highlight.
//
// Per rendering_density.md §2.2:
//   - No controls strip
//   - No own modal — uses the shared ``onExpand`` callback
//   - Methodology reachable via footer caveat (full wire disclosure
//     surfaces on hover via ``title=``)
//   - Tone cues on every ranked z-score (|z| ≥ 1.5 amber; |z| ≥ 2.0
//     coral/mint by direction)
// ============================================================================

import { ArrowUpRight, Info, Search, Trophy } from 'lucide-react';
import {
  FreshnessPill,
  toneTextClass,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  BOND_FUTURES_SCANNER_COMPACT_CAVEAT,
  METRIC_REGISTRY,
  formatZScore,
  instrumentFlag,
  narrativeTag,
  nativeUnits,
  nativeValue,
  parseScanSummary,
  sortRowsByAbsZScore,
  useBondFuturesScanner,
  zScoreTone,
} from './bondFuturesScannerShared';

// Number of rows the compact card surfaces.  Per the mockup the card
// shows 4 ranked rows + a "MOST EXTREME" highlight row.  Anything beyond
// is reached via the expand affordance.
const COMPACT_TOP_ROWS = 4;

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilies = parseCurveFamiliesParam(params.curve_families);
  const topN = parseOptionalNumber(params.top_n);
  const minAbsZ = parseOptionalNumber(params.min_abs_z_score);
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = useBondFuturesScanner({
    curveFamilies,
    topN,
    minAbsZScore: minAbsZ,
    asOfDate,
  });

  const summary = parseScanSummary(data?.scan_summary ?? '');
  const allRows = data?.results ?? [];
  // The wire orders rows per-metric; the compact view wants the
  // universe-wide top-N by |z| (across all four metrics).
  const sortedRows = sortRowsByAbsZScore(allRows);
  const visibleRows = sortedRows.slice(0, COMPACT_TOP_ROWS);
  const totalAvailable = sortedRows.length;
  const most = sortedRows[0];

  const minHeight = size === 'medium' ? 420 : 360;

  const anchorLabel = summary.asOfSpanStart && summary.asOfSpanEnd
    ? summary.asOfSpanStart === summary.asOfSpanEnd
      ? summary.asOfSpanStart
      : `${summary.asOfSpanStart} → ${summary.asOfSpanEnd}`
    : null;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="bond-futures-scanner-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <Search size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">BOND FUTURES EXTREMES</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SCANNER</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span className="font-mono">
                {summary.threshold != null ? `|z| ≥ ${summary.threshold.toFixed(1)}σ` : '|z| ≥ 1.5σ'}
              </span>
              {summary.flaggedTotal != null && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span>{summary.flaggedTotal} Flagged</span>
                </>
              )}
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">P+Δ+V+O</span>
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
            aria-label="Open extended view of bond futures extremes"
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
          {/* Column headings */}
          <div className="grid grid-cols-[24px_1fr_auto_auto_auto] items-center gap-3 px-1 text-[10.5px] uppercase tracking-wide text-fg-muted">
            <span>#</span>
            <span>Contract</span>
            <span>Scope</span>
            <span className="text-right">Value (native)</span>
            <span className="text-right">Z-Score (252d)</span>
          </div>

          {/* Ranked rows */}
          <div className="flex flex-col">
            {visibleRows.length === 0 ? (
              <div className="px-1 py-3 text-[12px] text-fg-secondary">
                No stems passed the threshold for this scan.
              </div>
            ) : (
              visibleRows.map((row, idx) => {
                const tone = zScoreTone(row.z_score);
                const metricMeta = METRIC_REGISTRY[row.metric];
                const narrative = narrativeTag(row.metric, row.signal);
                return (
                  <div
                    key={`${row.curve_family}-${row.contract_code}-${row.metric}-${row.rank}`}
                    className="grid grid-cols-[24px_1fr_auto_auto_auto] items-center gap-3 border-t border-line-subtle px-1 py-1.5 text-[12.5px]"
                  >
                    <span
                      className={`font-mono text-[12px] ${toneTextClass(tone)}`}
                    >
                      {idx + 1}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5 text-fg-primary">
                      {instrumentFlag(row) && (
                        <span className="text-[14px] leading-none">
                          {instrumentFlag(row)}
                        </span>
                      )}
                      <span className="truncate font-mono">{row.contract_code}</span>
                    </span>
                    <span className="inline-flex items-center rounded-sm bg-surface-overlay px-1.5 py-[1px] font-mono text-[10px] uppercase tracking-wide text-fg-secondary">
                      {metricMeta.scopeShort}
                    </span>
                    <span className="flex flex-col items-end leading-tight">
                      <span className="text-right font-mono tabular-nums text-fg-primary">
                        {nativeValue(row)} {nativeUnits(row) === 'contracts' ? 'contracts' : ''}
                      </span>
                      {narrative && (
                        <span className="text-[10px] text-fg-muted">{narrative}</span>
                      )}
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

          {/* MOST EXTREME highlight row */}
          {most && (
            <div className="mt-auto flex items-center justify-between gap-2 rounded-md border border-line-subtle bg-surface-overlay px-2.5 py-2">
              <div className="flex items-center gap-2 text-[11px] text-fg-muted">
                <Trophy size={12} strokeWidth={1.75} className="text-amber-300" aria-hidden />
                <span className="kicker">Most Extreme</span>
              </div>
              <div className="flex items-center gap-2 text-[12px]">
                {instrumentFlag(most) && (
                  <span className="text-[13px] leading-none">{instrumentFlag(most)}</span>
                )}
                <span className="font-mono text-fg-primary">{most.contract_code}</span>
                <span className="inline-flex items-center rounded-sm bg-bg-elevated px-1 py-[1px] font-mono text-[9.5px] uppercase tracking-wide text-fg-secondary">
                  {METRIC_REGISTRY[most.metric].scopeShort}
                </span>
                <span
                  className={`font-mono tabular-nums ${toneTextClass(zScoreTone(most.z_score))}`}
                >
                  {formatZScore(most.z_score)}
                </span>
                <span className="text-[11px] text-fg-secondary">
                  {narrativeTag(most.metric, most.signal)}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={data?.methodology_disclosure || BOND_FUTURES_SCANNER_COMPACT_CAVEAT}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{BOND_FUTURES_SCANNER_COMPACT_CAVEAT}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {onExpand && totalAvailable > visibleRows.length && (
            <button
              type="button"
              onClick={onExpand}
              className="text-ice-300 underline-offset-2 hover:underline"
            >
              View all {totalAvailable}
            </button>
          )}
          {anchorLabel && <span>As of {anchorLabel}</span>}
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
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="grid grid-cols-[24px_1fr_auto_auto_auto] items-center gap-3 border-t border-line-subtle px-1 py-1.5"
        >
          <div className="h-3 w-4 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-24 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-10 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-16 animate-pulse rounded bg-line-subtle justify-self-end" />
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
