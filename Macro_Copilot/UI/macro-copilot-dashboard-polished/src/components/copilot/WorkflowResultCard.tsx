// ============================================================================
// WorkflowResultCard
// ----------------------------------------------------------------------------
// Renders the structured workflow execution payload that the chat WS streams
// (workflow_route_decision -> workflow_status -> workflow_result events).
// Shown in-line inside the assistant chat bubble alongside the streamed
// prose summary.
//
// Layout (top → bottom):
//   1. ROUTING — template_id chip + 1-line rationale
//   2. SLOT BINDINGS — the LLM-emitted slot_values, key/value list
//   3. EXECUTION — the WorkflowExecutionEnvelope summary:
//        - Series:        units, n_rows, mean (and full mini-summary)
//        - SeriesSet:     keys + units_by_key + common_index extents
//        - EventSet:      n_events / n_dates
//        - error path:    error message
//   4. LINEAGE — DAG-walk summary as an ordered chip strip
// ============================================================================

import { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Network,
  Workflow,
  AlertCircle,
} from 'lucide-react';
import type { WorkflowTurnPayload } from '@/types/copilot';
import type { WorkflowTerminalArtifact } from '@/types/workflows';
import { cn } from '@/utils/cn';

type WorkflowResultCardProps = {
  payload: WorkflowTurnPayload;
};

export function WorkflowResultCard({ payload }: WorkflowResultCardProps) {
  const { routeDecision, status, result } = payload;
  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-ice-400/20 bg-ice-500/[0.04]">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-ice-400/15 bg-ice-500/[0.04] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ice-400/30 bg-ice-500/15 text-ice-200">
            <Workflow size={13} />
          </div>
          <div className="min-w-0">
            <p className="kicker text-ice-300/70">WORKFLOW</p>
            <p className="mt-0.5 truncate text-[12.5px] font-semibold text-ice-100 mono">
              {routeDecision.template_id}
            </p>
          </div>
        </div>
        <StatusPill status={status} ok={result?.ok ?? null} />
      </div>

      {/* Rationale */}
      {routeDecision.rationale && (
        <div className="border-b border-ice-400/12 px-4 py-3">
          <p className="kicker text-ice-300/70">Routing rationale</p>
          <p className="mt-1 text-[11.5px] leading-[1.5] text-fg-secondary italic">
            "{routeDecision.rationale}"
          </p>
        </div>
      )}

      {/* Slot bindings */}
      <SlotBindingsSection slotValues={routeDecision.slot_values} />

      {/* Execution result */}
      {result && (
        <ExecutionSection result={result} />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------

function StatusPill({
  status,
  ok,
}: {
  status: WorkflowTurnPayload['status'];
  ok: boolean | null;
}) {
  if (status === 'running') {
    return (
      <span className="flex items-center gap-1.5 rounded-md border border-ice-400/30 bg-ice-500/15 px-2 py-1 text-[10px] font-semibold text-ice-200">
        <Loader2 size={10} className="animate-spin" />
        running
      </span>
    );
  }
  if (status === 'error' || ok === false) {
    return (
      <span className="flex items-center gap-1.5 rounded-md border border-coral-400/30 bg-coral-400/15 px-2 py-1 text-[10px] font-semibold text-coral-300">
        <AlertCircle size={10} />
        failed
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 rounded-md border border-mint-400/30 bg-mint-400/15 px-2 py-1 text-[10px] font-semibold text-mint-300">
      <CheckCircle2 size={10} />
      complete
    </span>
  );
}

function SlotBindingsSection({
  slotValues,
}: {
  slotValues: Record<string, unknown>;
}) {
  const [expanded, setExpanded] = useState(false);
  const entries = Object.entries(slotValues);
  if (entries.length === 0) return null;

  // Show 5 by default; expand for full.
  const visible = expanded ? entries : entries.slice(0, 5);

  return (
    <div className="border-b border-ice-400/12 px-4 py-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left"
      >
        {expanded ? <ChevronDown size={11} className="text-fg-faint" /> : <ChevronRight size={11} className="text-fg-faint" />}
        <p className="kicker text-ice-300/70">
          Slot bindings ({entries.length})
        </p>
      </button>
      <dl className="mt-2 space-y-1">
        {visible.map(([k, v]) => (
          <div
            key={k}
            className="flex items-baseline justify-between gap-3 border-b border-ice-400/[0.05] py-1 last:border-b-0"
          >
            <dt className="mono shrink-0 text-[10.5px] text-ice-300">{k}</dt>
            <dd className="mono min-w-0 truncate text-right text-[10.5px] text-fg-secondary">
              {formatSlotValue(v)}
            </dd>
          </div>
        ))}
        {!expanded && entries.length > 5 && (
          <p className="text-[10px] text-fg-faint">
            +{entries.length - 5} more — click to expand
          </p>
        )}
      </dl>
    </div>
  );
}

function ExecutionSection({
  result,
}: {
  result: NonNullable<WorkflowTurnPayload['result']>;
}) {
  if (!result.ok) {
    return (
      <div className="px-4 py-3">
        <p className="kicker text-coral-300">Execution failed</p>
        <p className="mt-1 text-[11.5px] leading-[1.5] text-coral-300/85">
          {result.error ?? '(no detail)'}
        </p>
      </div>
    );
  }

  const artifact = result.terminal_artifact;
  return (
    <div className="px-4 py-3">
      <p className="kicker text-ice-300/70">Terminal artifact</p>
      {artifact ? (
        <TerminalArtifactBlock artifact={artifact} />
      ) : (
        <p className="mt-1 text-[11.5px] text-fg-muted">(no artifact returned)</p>
      )}
      {result.workflow_lineage_summary && (
        <LineageSection lineage={result.workflow_lineage_summary} />
      )}
    </div>
  );
}

function TerminalArtifactBlock({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  if (artifact.type === 'Series') {
    // Event-relative offset Series (e.g. conditional_aggregate output):
    // index is "days from event" — render as a horizon strip, not a
    // calendar range, so we don't print the synthetic 1970 anchor.
    const isEventRelative = artifact.index_kind === 'event_relative_offset';

    return (
      <div className="mt-2 space-y-2.5">
        <div className="flex items-baseline gap-3 text-[11px]">
          <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-[0.1em] text-ice-200">
            {isEventRelative ? 'Series · Event-Relative' : 'Series'}
          </span>
          <span className="mono text-fg-secondary">{artifact.units ?? '?'}</span>
          <span className="mono text-fg-faint">·</span>
          <span className="mono text-fg-secondary">
            {artifact.n_rows} {isEventRelative ? 'horizons' : 'rows'}
          </span>
        </div>

        {artifact.summary_stats && (
          <div className="grid grid-cols-4 gap-2">
            <StatCell label="MEAN" value={artifact.summary_stats.mean} />
            <StatCell label="STD" value={artifact.summary_stats.std} />
            <StatCell label="MIN" value={artifact.summary_stats.min} />
            <StatCell label="MAX" value={artifact.summary_stats.max} />
          </div>
        )}

        {isEventRelative ? (
          <EventRelativeStrip artifact={artifact} />
        ) : (
          (artifact.first_row || artifact.last_row) && (
            <div className="flex items-baseline gap-2 rounded-md border border-line-subtle bg-white/[0.012] px-3 py-2 text-[10.5px]">
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
          )
        )}
      </div>
    );
  }

  if (artifact.type === 'SeriesSet') {
    return (
      <div className="mt-2 space-y-2">
        <div className="flex items-baseline gap-3 text-[11px]">
          <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-[0.1em] text-ice-200">
            SeriesSet
          </span>
          <span className="mono text-fg-secondary">{artifact.n_rows} rows</span>
        </div>
        {artifact.keys && (
          <div className="flex flex-wrap gap-1.5">
            {artifact.keys.map((k) => (
              <span
                key={k}
                className="mono rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[10px] text-ice-200"
              >
                {k}
                {artifact.units_by_key?.[k] ? (
                  <span className="text-fg-faint"> · {artifact.units_by_key[k]}</span>
                ) : null}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (artifact.type === 'EventSet') {
    return (
      <div className="mt-2 flex items-baseline gap-3 text-[11px]">
        <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-[0.1em] text-ice-200">
          EventSet
        </span>
        <span className="mono text-fg-secondary">
          {artifact.n_events ?? 0} events / {artifact.n_dates ?? 0} dates
        </span>
      </div>
    );
  }

  // PR-11D — ScalarMetric branch.  The canonical open-DAG terminal
  // (correlation / covariance / cointegration test stat) is a single
  // finite scalar.  Renders metric_key chip + big-number value + units
  // so the chat bubble's workflow-result card shows the actual value
  // (e.g. -0.34) instead of falling through to the bare type label.
  if (artifact.type === 'ScalarMetric') {
    const metricKey = artifact.metric_key ?? 'scalar';
    const units = artifact.units ?? '';
    return (
      <div className="mt-2 space-y-2">
        <div className="flex items-baseline gap-3 text-[11px]">
          <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-[0.1em] text-ice-200">
            ScalarMetric
          </span>
          <span className="mono text-fg-secondary">{metricKey}</span>
          {units && (
            <>
              <span className="mono text-fg-faint">·</span>
              <span className="mono text-fg-secondary">{units}</span>
            </>
          )}
        </div>
        <div className="flex items-baseline gap-2 rounded-md border border-line-subtle bg-white/[0.012] px-3 py-2 text-[11px]">
          <span className="font-serif-display text-[18px] font-light leading-none text-fg-primary">
            {formatNumber(artifact.value ?? null)}
          </span>
          {units && (
            <span className="mono text-[10px] text-fg-faint">{units}</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <p className="mt-2 mono text-[11px] text-fg-secondary">
      {artifact.type}
    </p>
  );
}

function EventRelativeStrip({
  artifact,
}: {
  artifact: WorkflowTerminalArtifact;
}) {
  // Prefer the full offset_rows; fall back to head + tail when the
  // backend didn't ship them (older builds).
  const rows: Array<{ offset: number; value: number | null }> =
    artifact.offset_rows && artifact.offset_rows.length > 0
      ? artifact.offset_rows
      : [
          artifact.first_row?.offset != null
            ? {
                offset: artifact.first_row.offset,
                value: artifact.first_row.value,
              }
            : null,
          artifact.last_row?.offset != null
            ? {
                offset: artifact.last_row.offset,
                value: artifact.last_row.value,
              }
            : null,
        ].filter((r): r is { offset: number; value: number | null } => !!r);

  if (rows.length === 0) return null;

  // Symmetric scale so positive and negative bars share a baseline.
  const finite = rows
    .map((r) => r.value)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const absMax = finite.length > 0 ? Math.max(...finite.map(Math.abs)) : 1;
  const denom = absMax === 0 ? 1 : absMax;

  const unit = artifact.units ?? '';

  return (
    <div className="rounded-md border border-line-subtle bg-white/[0.012] px-3 py-2.5">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-[0.12em] text-fg-faint">
          Mean by horizon ({artifact.offset_unit ?? 'days'} from event)
        </span>
        <span className="mono text-[9.5px] text-fg-faint">
          {unit}
        </span>
      </div>
      <div className="space-y-1">
        {rows.map((r) => {
          const v = typeof r.value === 'number' ? r.value : 0;
          const pct = (Math.abs(v) / denom) * 100;
          const positive = v >= 0;
          return (
            <div
              key={r.offset}
              className="flex items-center gap-2 text-[10.5px]"
            >
              <span className="mono w-10 shrink-0 text-fg-muted">
                Day {r.offset >= 0 ? `+${r.offset}` : r.offset}
              </span>
              <div className="relative flex h-3 flex-1 items-center">
                {/* Center axis */}
                <div className="absolute inset-y-0 left-1/2 w-px bg-line-subtle" />
                {/* Bar */}
                <div
                  className={cn(
                    'absolute h-2 rounded-[2px]',
                    positive
                      ? 'left-1/2 bg-mint-400/60'
                      : 'right-1/2 bg-coral-400/60',
                  )}
                  style={{ width: `${pct / 2}%` }}
                />
              </div>
              <span
                className={cn(
                  'mono w-14 shrink-0 text-right',
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
    </div>
  );
}

function StatCell({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
  return (
    <div className="rounded-md border border-line-subtle bg-white/[0.012] px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-[0.14em] text-fg-faint">
        {label}
      </p>
      <p className="mono mt-0.5 text-[11.5px] text-fg-primary">
        {formatNumber(value)}
      </p>
    </div>
  );
}

function LineageSection({ lineage }: { lineage: string }) {
  // Split the lineage summary on the arrow + trim the "workflow X:" prefix.
  // This is best-effort parsing — the substrate emits a known format
  // (workflow <id>: node1 → node2 → ...).  Worst case we just render
  // the raw string.
  const arrowSplit = lineage.split(/[→>]/).map((s) => s.trim()).filter(Boolean);
  const cleaned = arrowSplit
    .map((s) => s.replace(/^workflow\s+\S+:\s*/i, ''))
    .filter(Boolean);

  return (
    <div className="mt-3 border-t border-ice-400/12 pt-3">
      <div className="flex items-center gap-1.5">
        <Network size={11} className="text-ice-300/70" />
        <p className="kicker text-ice-300/70">DAG lineage</p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {cleaned.map((node, i) => (
          <span key={i} className="flex items-center gap-1.5">
            <span className="mono rounded border border-ice-400/15 bg-ice-500/[0.06] px-1.5 py-[2px] text-[10px] text-ice-100">
              {node}
            </span>
            {i < cleaned.length - 1 && (
              <span className="text-fg-faint">→</span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------

function formatSlotValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number') return formatNumber(v);
  if (typeof v === 'boolean') return String(v);
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v);
    } catch {
      return '[object]';
    }
  }
  return String(v);
}

function formatNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(4);
}
