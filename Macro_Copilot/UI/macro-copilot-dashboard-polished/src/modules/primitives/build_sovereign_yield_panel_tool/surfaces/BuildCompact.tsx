// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// ``build_sovereign_yield_panel_tool``.  PANEL-CONTRACT shape.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 every primitive claiming
// ``custom_build_surface`` ships a compact view; this one is a TABLE-SHAPED
// CONTRACT CARD (identity → 3 contract KPIs → column-chip strip → caveat
// footer), NOT a sparkline — the wire carries panel METADATA only, no
// series to draw (FP9 forbids fabricating one client-side).  The shared
// ``BuildCompactShell`` is LEVEL-shape oriented (KPIs + sparkline); this
// component honours the SEMANTIC contract of §2.2 (identity / headline
// data / methodology / expand / tone cues) with a contract-shaped layout —
// the same guardrail the scanner-compact precedent
// (../../scan_extremes_tool/surfaces/BuildCompact.tsx) established for
// non-series shapes.  Reuses the shared FreshnessPill so visual treatment
// matches the compact catalogue.
// ============================================================================

import { ArrowUpRight, Boxes, Info } from 'lucide-react';
import {
  FreshnessPill,
  type BuildCompactProps,
  type KPIDescriptor,
} from '@/components/shared/build';
import {
  DEFAULT_LEG_FAMILIES_CSV,
  DEFAULT_LEG_TENORS_CSV,
  compactCaveat,
  compactKPIs,
  defaultWindow,
  useSovereignYieldPanel,
} from './sovereignYieldPanelShared';

// Number of column-key chips the compact card surfaces; anything beyond
// folds into a '+K more' chip resolved via the expand affordance.  FOUR
// keeps the strip one row deep at the §2.2 small envelope (~400×280) —
// the chip strip is a wrong-leg-list tripwire, not a roster.
const COMPACT_CHIP_COUNT = 4;

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // Same render-time defaults as the extended view so a sparse DAG node
  // still renders a real contract (FM7 — render-time, not module-eval).
  const fallbackWindow = defaultWindow();
  const { data, isLoading, errorMessage } = useSovereignYieldPanel({
    legCurveFamiliesCsv: params.leg_curve_families || DEFAULT_LEG_FAMILIES_CSV,
    legTenorsCsv: params.leg_tenors || DEFAULT_LEG_TENORS_CSV,
    startDate: params.start_date || fallbackWindow.start,
    endDate: params.end_date || fallbackWindow.end,
    fieldName: params.field_name || undefined,
    missingDataPolicy: params.missing_data_policy || undefined,
  });

  const kpis: ReadonlyArray<KPIDescriptor> = data
    ? compactKPIs(data)
    : [
        { label: 'COLUMNS', value: '—' },
        { label: 'ROWS', value: '—' },
        { label: 'DATE RANGE', value: '—' },
      ];
  const columnKeys = data?.columns ?? [];
  const visibleChips = columnKeys.slice(0, COMPACT_CHIP_COUNT);
  const hiddenCount = Math.max(0, columnKeys.length - visibleChips.length);
  const caveat = compactCaveat(data);

  const minHeight = size === 'medium' ? 420 : 340;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="sovereign-yield-panel-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <Boxes size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">SOVEREIGN YIELD PANEL</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">CONTRACT</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span className="truncate">
                Panel metadata — cells stay workflow-side
              </span>
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
            aria-label="Open extended view of the sovereign yield panel contract"
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
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {/* 3 contract KPIs */}
          <div className="grid grid-cols-3 gap-3">
            {kpis.map((kpi) => (
              <div key={kpi.label} className="flex flex-col gap-1">
                <span className="kicker text-fg-muted">{kpi.label}</span>
                <span
                  className={`font-mono tabular-nums leading-none text-fg-primary ${
                    kpi.emphasis === 'primary' ? 'text-[22px]' : 'text-[18px]'
                  }`}
                >
                  {kpi.value}
                </span>
                {kpi.subtext && (
                  <span className="font-mono text-[11px] text-fg-secondary">
                    {kpi.subtext}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Column-chip strip */}
          <div className="flex flex-col gap-1.5">
            <span className="kicker text-fg-faint">COLUMNS</span>
            <div className="flex flex-wrap gap-1.5">
              {visibleChips.length === 0 ? (
                <span className="text-[12px] text-fg-secondary">
                  No columns resolved.
                </span>
              ) : (
                <>
                  {visibleChips.map((c) => (
                    <span
                      key={c}
                      className="rounded-md border border-line-subtle bg-surface-overlay px-2 py-0.5 font-mono text-[11px] text-fg-secondary"
                    >
                      {c}
                    </span>
                  ))}
                  {hiddenCount > 0 && (
                    <button
                      type="button"
                      onClick={onExpand}
                      disabled={!onExpand}
                      className="rounded-md border border-line-subtle bg-surface-overlay px-2 py-0.5 font-mono text-[11px] text-ice-300 disabled:cursor-default"
                    >
                      +{hiddenCount} more
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={caveat}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{caveat}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {data && <span>As of {data.as_of_end}</span>}
          <FreshnessPill freshness="fresh" />
        </div>
      </footer>
    </article>
  );
};

export default BuildCompact;

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function CompactSkeleton() {
  return (
    <div className="flex min-h-[160px] flex-1 flex-col gap-3">
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2.5 w-14 animate-pulse rounded bg-line-subtle" />
            <div className="h-5 w-16 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-5 w-16 animate-pulse rounded bg-line-subtle" />
        ))}
      </div>
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[160px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
