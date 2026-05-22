// ============================================================================
// ResultCanvas — the computed-output zone of a research card
// ----------------------------------------------------------------------------
// Renders the terminal artifact of a workflow turn (or, in degraded
// supervisor mode, a placeholder hint that the underlying data lives
// in the linked workspace).
//
// Three concrete rendering branches today:
//
//   1. EVENT-RELATIVE Series — the canonical event_study terminal.
//      x-axis = days from event, y-axis = bps (or whatever units).
//      Rendered as a horizontal bar chart with a center-axis baseline,
//      a positive (mint) / negative (coral) sign convention, and a
//      summary stat strip above (mean / std / min / max / n_finite).
//
//   2. CALENDAR Series — head + tail rows + summary stats.  The wire
//      doesn't ship the full series for performance, so we render a
//      "first → last" pair plus the four-stat panel.  V2: stream the
//      full series and render a Recharts line plot.
//
//   3. SeriesSet / EventSet / other — keys + counts as a meta strip.
//      Encourages the user to "Open in Build" for the full picture.
//
// When no terminal artifact exists (supervisor turn, errored workflow,
// or pre-result streaming), the canvas renders nothing — the card
// gracefully omits the zone instead of a placeholder, since an empty
// chart would be informationally misleading.
// ============================================================================

import { useState } from 'react';
import { LayoutPanelTop, Table2 } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';
import type { WorkflowTerminalArtifact } from '@/types/workflows';
import { cn } from '@/utils/cn';

type Props = {
  message: CopilotMessage;
};

export function ResultCanvas({ message }: Props) {
  const artifact = message.workflow?.result?.terminal_artifact;
  const ok = message.workflow?.result?.ok ?? true;

  if (!artifact || !ok) return null;
  return (
    <div className="px-5 pb-4">
      <ArtifactRenderer artifact={artifact} />
    </div>
  );
}

// ----------------------------------------------------------------------------

function ArtifactRenderer({ artifact }: { artifact: WorkflowTerminalArtifact }) {
  if (artifact.type === 'Series') {
    if (artifact.index_kind === 'event_relative_offset') {
      return <EventRelativeChart artifact={artifact} />;
    }
    return <CalendarSeriesPanel artifact={artifact} />;
  }
  if (artifact.type === 'SeriesSet') return <SeriesSetPanel artifact={artifact} />;
  if (artifact.type === 'EventSet') return <EventSetPanel artifact={artifact} />;
  return <UnknownArtifactPanel artifact={artifact} />;
}

// ----------------------------------------------------------------------------
// Wrapper — frames every artifact render with a consistent canvas
// chrome: 1px hairline, 12px radius, top-right action cluster, kicker
// + header strip.

function CanvasFrame({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: React.ReactNode;
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  return (
    <div className="overflow-hidden rounded-[10px] border border-line-soft bg-white/[0.012]">
      <div className="flex items-center justify-between gap-3 border-b border-line-subtle px-4 py-2.5">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="kicker text-fg-muted">{title}</span>
          {meta && (
            <span className="mono truncate text-[10.5px] text-fg-faint">
              {meta}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <CanvasIconButton
            label="Chart view"
            active={view === 'chart'}
            onClick={() => setView('chart')}
          >
            <LayoutPanelTop size={11} />
          </CanvasIconButton>
          <CanvasIconButton
            label="Table view"
            active={view === 'table'}
            onClick={() => setView('table')}
          >
            <Table2 size={11} />
          </CanvasIconButton>
        </div>
      </div>
      <div className="px-4 py-4">
        {/* The current view is exposed via context — but for V1 we just
            inline a render switcher.  Children read `data-canvas-view`
            on their parent to decide chart vs table. */}
        <div data-canvas-view={view}>{children}</div>
      </div>
    </div>
  );
}

function CanvasIconButton({
  children,
  active,
  onClick,
  label,
}: {
  children: React.ReactNode;
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className={cn(
        'flex h-6 w-6 items-center justify-center rounded transition-colors',
        active
          ? 'bg-white/[0.05] text-fg-primary'
          : 'text-fg-muted hover:bg-white/[0.03] hover:text-fg-secondary',
      )}
    >
      {children}
    </button>
  );
}

// ----------------------------------------------------------------------------
// Event-relative Series — the workhorse chart for event_study results.

function EventRelativeChart({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  const rows = resolveOffsetRows(artifact);
  const stats = artifact.summary_stats;
  const units = artifact.units ?? '';

  const finite = rows
    .map((r) => r.value)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const absMax = finite.length > 0 ? Math.max(...finite.map(Math.abs)) : 1;
  const denom = absMax === 0 ? 1 : absMax;

  return (
    <CanvasFrame
      title="Mean abnormal move by horizon"
      meta={`${rows.length} horizons · ${units || '—'}`}
    >
      {stats && (
        <div className="mb-3 grid grid-cols-4 gap-2">
          <StatCell label="MEAN" value={stats.mean} />
          <StatCell label="STD" value={stats.std} />
          <StatCell label="MIN" value={stats.min} tone="neg" />
          <StatCell label="MAX" value={stats.max} tone="pos" />
        </div>
      )}
      <div className="space-y-1.5">
        {rows.map((r) => {
          const v = typeof r.value === 'number' ? r.value : 0;
          const pct = (Math.abs(v) / denom) * 100;
          const positive = v >= 0;
          return (
            <div key={r.offset} className="flex items-center gap-3 text-[11.5px]">
              <span className="mono w-12 shrink-0 text-fg-muted">
                Day {r.offset >= 0 ? `+${r.offset}` : r.offset}
              </span>
              <div className="relative flex h-4 flex-1 items-center">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line-subtle" />
                <div
                  className={cn(
                    'absolute h-2.5 rounded-[3px]',
                    positive
                      ? 'left-1/2 bg-mint-400/55 ring-1 ring-mint-400/30'
                      : 'right-1/2 bg-coral-400/55 ring-1 ring-coral-400/30',
                  )}
                  style={{ width: `${pct / 2}%` }}
                />
              </div>
              <span
                className={cn(
                  'mono w-16 shrink-0 text-right text-[11.5px]',
                  v > 0
                    ? 'text-mint-300'
                    : v < 0
                      ? 'text-coral-300'
                      : 'text-fg-muted',
                )}
              >
                {formatNumber(r.value)}
              </span>
            </div>
          );
        })}
      </div>
    </CanvasFrame>
  );
}

function resolveOffsetRows(
  artifact: WorkflowTerminalArtifact,
): Array<{ offset: number; value: number | null }> {
  if (artifact.offset_rows && artifact.offset_rows.length > 0) {
    return artifact.offset_rows;
  }
  // Older backends: fall back to first / last only.
  const xs: Array<{ offset: number; value: number | null }> = [];
  if (artifact.first_row?.offset != null) {
    xs.push({
      offset: artifact.first_row.offset,
      value: artifact.first_row.value,
    });
  }
  if (artifact.last_row?.offset != null) {
    xs.push({
      offset: artifact.last_row.offset,
      value: artifact.last_row.value,
    });
  }
  return xs;
}

// ----------------------------------------------------------------------------

function CalendarSeriesPanel({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  const stats = artifact.summary_stats;
  return (
    <CanvasFrame
      title={artifact.series_key ? `Series · ${artifact.series_key}` : 'Series'}
      meta={`${artifact.n_rows} rows · ${artifact.units ?? '—'}`}
    >
      {stats && (
        <div className="mb-3 grid grid-cols-4 gap-2">
          <StatCell label="MEAN" value={stats.mean} />
          <StatCell label="STD" value={stats.std} />
          <StatCell label="MIN" value={stats.min} tone="neg" />
          <StatCell label="MAX" value={stats.max} tone="pos" />
        </div>
      )}
      {(artifact.first_row || artifact.last_row) && (
        <div className="flex items-baseline gap-3 rounded-md border border-line-subtle bg-white/[0.012] px-3 py-2 text-[11px]">
          {artifact.first_row && (
            <span className="mono text-fg-muted">
              {artifact.first_row.date}: {formatNumber(artifact.first_row.value)}
            </span>
          )}
          <span className="text-fg-faint">→</span>
          {artifact.last_row && (
            <span className="mono text-fg-secondary">
              {artifact.last_row.date}: {formatNumber(artifact.last_row.value)}
            </span>
          )}
        </div>
      )}
      <p className="mt-3 text-[11px] leading-[1.55] text-fg-muted">
        Full series available — open this turn in Build for the time-series
        canvas.
      </p>
    </CanvasFrame>
  );
}

function SeriesSetPanel({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  return (
    <CanvasFrame
      title="SeriesSet"
      meta={`${artifact.keys?.length ?? 0} keys · ${artifact.n_rows} rows`}
    >
      <div className="flex flex-wrap gap-1.5">
        {(artifact.keys ?? []).map((k) => (
          <span
            key={k}
            className="mono rounded-md border border-line-soft bg-white/[0.018] px-2 py-1 text-[11px] text-ice-200"
          >
            {k}
            {artifact.units_by_key?.[k] && (
              <span className="text-fg-faint"> · {artifact.units_by_key[k]}</span>
            )}
          </span>
        ))}
      </div>
    </CanvasFrame>
  );
}

function EventSetPanel({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  return (
    <CanvasFrame
      title="EventSet"
      meta={`${artifact.n_events ?? 0} events · ${artifact.n_dates ?? 0} dates`}
    >
      <p className="text-[12px] leading-[1.55] text-fg-secondary">
        Event mask over{' '}
        <span className="mono text-fg-primary">
          {artifact.source_series_key ?? 'source series'}
        </span>{' '}
        — open this turn in Build for the full date list and forward-window
        extraction.
      </p>
    </CanvasFrame>
  );
}

function UnknownArtifactPanel({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  return (
    <CanvasFrame title="Terminal artifact" meta={artifact.type}>
      <p className="mono text-[12px] text-fg-secondary">
        Type: {artifact.type}
      </p>
    </CanvasFrame>
  );
}

// ----------------------------------------------------------------------------

function StatCell({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  tone?: 'pos' | 'neg';
}) {
  return (
    <div className="rounded-md border border-line-subtle bg-white/[0.012] px-2.5 py-2">
      <p className="text-[9.5px] uppercase tracking-[0.14em] text-fg-faint">
        {label}
      </p>
      <p
        className={cn(
          'mono mt-1 text-[14px]',
          tone === 'pos'
            ? 'text-mint-300'
            : tone === 'neg'
              ? 'text-coral-300'
              : 'text-fg-primary',
        )}
      >
        {formatNumber(value)}
      </p>
    </div>
  );
}

function formatNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(4);
}
