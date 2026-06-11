// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_yield_change_attribution_pca_tool.  SNAPSHOT shape.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 every primitive
// claiming ``custom_build_surface`` ships a compact view; this one is a
// SIGNED-BARS card (NOT a sparkline): the tool's Output is a pure
// snapshot decomposition with no time series on the wire, so the
// canonical headline visual is the per-component contribution bars.
// Mounted as a node body inside multi-tool DAG visualisations.
//
// Shape precedent: the scanner-shape guardrail sanctions table/bars
// shaped compacts (see ../../scan_extremes_tool/surfaces/BuildCompact.tsx).
// The shared ``BuildCompactShell`` is LEVEL-shape oriented (sparkline
// body); this component honours the SEMANTIC contract of §2.2
// (identity / headline KPIs / methodology / expand / tone cues) with a
// bars-shaped body, composing the shared grammar's ``DecompositionBars``
// for the visual and the shared ``FreshnessPill`` / tone helpers for
// chrome so visual treatment matches the rest of the compact catalogue.
// ============================================================================

import { ArrowUpRight, GitBranch, Info } from 'lucide-react';
import {
  FreshnessPill,
  toneTextClass,
  type BuildCompactProps,
} from '@/components/shared/build';
import { DecompositionBars } from '@/components/shared/build/model';
import {
  ATTRIBUTION_COMPACT_CAVEAT,
  compactKPIs,
  contributionEntries,
  defaultWindow,
  parseOptionalNumber,
  useYieldChangeAttributionData,
} from './yieldChangeAttributionShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // The default window (~trailing quarter) is computed at render time —
  // compact cards can be mounted from Ask handoffs that omit the dates.
  const fallbackWindow = defaultWindow();
  const { data, isLoading, errorMessage } = useYieldChangeAttributionData({
    curveFamily: params.curve_family ?? '',
    targetTenor: params.target_tenor ?? '',
    startDate: params.start_date || fallbackWindow.start,
    endDate: params.end_date || fallbackWindow.end,
    pcaLookbackDays: parseOptionalNumber(params.pca_lookback_days),
    nComponents: parseOptionalNumber(params.n_components),
    changeFrequency: params.change_frequency,
    tenorsCsv: params.tenors,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const family = cm?.curve_family ?? params.curve_family ?? '—';
  const tenor = cm?.target_tenor ?? params.target_tenor ?? '—';
  const kpis = data
    ? compactKPIs(data)
    : [
        { label: 'TOTAL Δ', value: '—' },
        { label: 'TOP FACTOR', value: '—' },
        { label: 'RESIDUAL', value: '—' },
      ];

  const minHeight = size === 'medium' ? 420 : 320;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="yield-change-attribution-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <GitBranch size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">Yield-Change Attribution</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SNAPSHOT</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-fg-secondary">
              <span className="font-mono">
                {family} {tenor} ATTRIBUTION
              </span>
              {cm && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span className="text-[11.5px]">
                    {cm.start_date_resolved} → {cm.end_date_resolved}
                  </span>
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
            aria-label="Open extended view of yield-change attribution"
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
      ) : isLoading || !data || !cm ? (
        <CompactSkeleton />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {/* KPI strip (3 cells) */}
          <div className="grid grid-cols-3 gap-3">
            {kpis.map((kpi, i) => (
              <div
                key={`${kpi.label}-${i}`}
                className="flex flex-col items-start gap-1 px-1"
              >
                <span className="kicker text-fg-muted">{kpi.label}</span>
                <div className="flex items-baseline gap-1">
                  <span
                    className={`text-[24px] font-medium leading-none ${toneTextClass(kpi.tone)}`}
                  >
                    {kpi.value}
                  </span>
                  {kpi.unit && (
                    <span
                      className={`text-[13px] leading-none ${toneTextClass(kpi.tone)} opacity-80`}
                    >
                      {kpi.unit}
                    </span>
                  )}
                </div>
                {kpi.caption && (
                  <span className={`text-[11px] ${toneTextClass(kpi.tone)} opacity-90`}>
                    {kpi.caption}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Mini signed bars — the shared grammar component at compact
              scale (sanctioned bars-shaped compact body). */}
          <div className="min-h-0 flex-1">
            <DecompositionBars
              title="Contribution · bps"
              entries={contributionEntries(cm)}
              mode="signed"
              unit="bp"
              decimals={1}
            />
          </div>
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={ATTRIBUTION_COMPACT_CAVEAT}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{ATTRIBUTION_COMPACT_CAVEAT}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {cm?.end_date_resolved && <span>As of {cm.end_date_resolved}</span>}
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
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1.5 px-1">
            <div className="h-2 w-16 animate-pulse rounded bg-line-subtle" />
            <div className="h-7 w-20 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-4 w-full animate-pulse rounded bg-line-subtle/40" />
        ))}
      </div>
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
