// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// ``scan_inflation_linkers_extremes_tool``.  SCANNER shape.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 every new
// primitive ships a compact view; this one is a TOP-N RANKED TABLE
// (NOT a sparkline) per the SCANNER guardrail.  Mounted as a node body
// inside multi-tool query DAG visualisations.
//
// Design reference: ./mockups/Compact.png (committed alongside this module).
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
  LINKER_SCANNER_COMPACT_CAVEAT,
  formatRealYieldPct,
  formatZScore,
  instrumentFlag,
  instrumentLabel,
  parseScanSummary,
  useLinkersScanner,
  zScoreTone,
} from './scanInflationLinkersExtremesShared';

// Number of rows the compact card surfaces.  Per the mockup the card shows
// up to 5 ranked rows + a "MOST EXTREME" highlight row.  Anything beyond is
// reached via the expand affordance.
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
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = useLinkersScanner({
    curveFamilies,
    topN,
    minAbsZScore: minAbsZ,
    asOfDate,
  });

  const summary = parseScanSummary(data?.scan_summary ?? '');
  const rows = data?.results ?? [];
  const visibleRows = rows.slice(0, COMPACT_TOP_ROWS);
  const totalAvailable = rows.length;
  const most = rows[0];

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
      data-testid="linker-scanner-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <Search size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">LINKER EXTREMES</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SCANNER</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span className="font-mono">
                {summary.threshold != null ? `|z| ≥ ${summary.threshold.toFixed(1)}σ` : '|z| ≥ 1.5σ'}
              </span>
              {summary.flagged != null && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span>{summary.flagged} Flagged</span>
                </>
              )}
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">REAL YIELD</span>
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
            aria-label="Open extended view of linker extremes"
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
          <div className="grid grid-cols-[24px_1fr_auto_auto] items-center gap-3 px-1 text-[10.5px] uppercase tracking-wide text-fg-muted">
            <span>#</span>
            <span>Instrument</span>
            <span className="text-right">Real Yield</span>
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
                const tone = zScoreTone(row.z_score_real_yield);
                return (
                  <div
                    key={`${row.curve_family}-${row.tenor}-${row.rank}`}
                    className="grid grid-cols-[24px_1fr_auto_auto] items-center gap-3 border-t border-line-subtle px-1 py-1.5 text-[12.5px]"
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
                      <span className="truncate">{instrumentLabel(row)}</span>
                    </span>
                    <span className="text-right font-mono tabular-nums text-fg-primary">
                      {formatRealYieldPct(row.real_yield_pct)}
                    </span>
                    <span
                      className={`text-right font-mono tabular-nums ${toneTextClass(tone)}`}
                    >
                      {formatZScore(row.z_score_real_yield)}
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
                <span className="font-mono text-fg-primary">{instrumentLabel(most)}</span>
                <span
                  className={`font-mono tabular-nums ${toneTextClass(zScoreTone(most.z_score_real_yield))}`}
                >
                  ({formatZScore(most.z_score_real_yield)})
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
          title={data?.methodology_disclosure || LINKER_SCANNER_COMPACT_CAVEAT}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{LINKER_SCANNER_COMPACT_CAVEAT}</span>
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
    <div className="flex min-h-[200px] flex-1 flex-col gap-2">
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="grid grid-cols-[24px_1fr_auto_auto] items-center gap-3 border-t border-line-subtle px-1 py-1.5"
        >
          <div className="h-3 w-4 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-32 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-12 animate-pulse rounded bg-line-subtle justify-self-end" />
          <div className="h-3 w-12 animate-pulse rounded bg-line-subtle justify-self-end" />
        </div>
      ))}
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[200px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
