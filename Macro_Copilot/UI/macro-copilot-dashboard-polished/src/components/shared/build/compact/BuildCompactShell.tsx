// ============================================================================
// shared/build/compact/BuildCompactShell.tsx — the compact-card shell.
// ----------------------------------------------------------------------------
// Renders the WHOLE compact card per docs_revamped/03_standards/rendering_density.md §2.2:
//
//   ┌─────────────────────────────────────────────────────┐
//   │ [icon] Tool Display Name  · SNAPSHOT       [↗]      │  header
//   │ <Identity primary · secondary  flag>                │
//   │ ─────────────────────────────────────────────────── │
//   │  KPI1       │   KPI2          │   KPI3              │  3-cell KPI strip
//   │  (value)    │   (value+subtext)│   (value+caption)  │
//   │ ─────────────────────────────────────────────────── │
//   │  <mini chart with ref bands + terminal dot>         │  chart
//   │ ─────────────────────────────────────────────────── │
//   │ (i) <caveat one-liner>  As of <date>  ● Fresh       │  footer
//   └─────────────────────────────────────────────────────┘
//
// Finance-blind — the per-tool wrapper passes BuildCompactShellProps
// descriptors; this component renders.  No domain logic lives here.
// ============================================================================

import { ArrowUpRight, Info } from 'lucide-react';
import type { BuildCompactShellProps } from '../lib/types';
import { toneTextClass } from '../lib/tone';
import { MiniChart } from './MiniChart';
import { FreshnessPill } from '../elements/FreshnessPill';

export function BuildCompactShell({
  toolDisplayName,
  statusPill,
  headerIcon,
  identity,
  kpis,
  chartPoints,
  chartUnit,
  referenceBands,
  caveatText,
  asOf,
  freshness,
  onExpand,
  size = 'small',
  isLoading,
  errorMessage,
  callMeta,
}: BuildCompactShellProps) {
  // Per the spec: exactly 3 KPI cells.  Defensive truncation with a
  // dev-mode warning.
  const kpiCells = kpis.slice(0, 3);
  if (process.env.NODE_ENV !== 'production' && kpis.length !== 3) {
    // eslint-disable-next-line no-console
    console.warn(
      `[BuildCompactShell] expected exactly 3 KPIs for ${toolDisplayName}, got ${kpis.length}.  Truncated to first 3.`,
    );
  }

  const minHeight = size === 'medium' ? 420 : 320;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="build-compact-shell"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          {headerIcon && (
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
              {headerIcon}
            </span>
          )}
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">{toolDisplayName}</span>
              {statusPill && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span className="kicker text-fg-muted">{statusPill}</span>
                </>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-fg-secondary">
              <span className="font-mono">{identity.primary}</span>
              {identity.secondary && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span>{identity.secondary}</span>
                </>
              )}
              {identity.flag && (
                <span className="text-[14px] leading-none">{identity.flag}</span>
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
            aria-label={`Open extended view of ${toolDisplayName}`}
            title="Open extended view"
            className="rounded-md border border-line-subtle bg-surface-overlay p-1.5 text-fg-muted transition-colors hover:border-line-strong hover:text-fg-primary"
          >
            <ArrowUpRight size={14} strokeWidth={1.75} />
          </button>
        )}
      </header>

      <hr className="border-t border-line-subtle" />

      {/* ---------- KPI strip (3 cells) ---------- */}
      {errorMessage ? (
        <CompactError message={errorMessage} />
      ) : isLoading ? (
        <CompactSkeleton />
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            {kpiCells.map((kpi, i) => (
              <div
                key={`${kpi.label}-${i}`}
                className="flex flex-col items-start gap-1 px-1"
              >
                <span className="kicker text-fg-muted">{kpi.label}</span>
                <div className="flex items-baseline gap-1">
                  <span
                    className={`text-[26px] font-medium leading-none ${toneTextClass(kpi.tone)}`}
                  >
                    {kpi.value}
                  </span>
                  {kpi.unit && (
                    <span
                      className={`text-[14px] leading-none ${toneTextClass(kpi.tone)} opacity-80`}
                    >
                      {kpi.unit}
                    </span>
                  )}
                </div>
                {kpi.subtext && (
                  <span className={`text-[11px] ${toneTextClass(kpi.tone)} opacity-75`}>
                    {kpi.subtext}
                  </span>
                )}
                {kpi.caption && (
                  <span className={`text-[11px] ${toneTextClass(kpi.tone)} opacity-90`}>
                    {kpi.caption}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* ---------- Chart ---------- */}
          <div className="min-h-0 flex-1">
            <MiniChart
              points={chartPoints}
              unit={chartUnit}
              referenceBands={referenceBands}
              height={size === 'medium' ? 220 : 160}
            />
          </div>
        </>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary">
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate" title={caveatText}>
            {caveatText}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {asOf && <span>As of {asOf}</span>}
          {freshness && <FreshnessPill freshness={freshness} />}
        </div>
      </footer>
    </article>
  );
}

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
      <div className="h-[160px] w-full animate-pulse rounded bg-line-subtle/40" />
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
