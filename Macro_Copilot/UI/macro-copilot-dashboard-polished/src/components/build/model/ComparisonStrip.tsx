// ============================================================================
// ComparisonStrip — pinned-runs side-by-side viewer
// ----------------------------------------------------------------------------
// Each pinned run captures (params, output, label, color).  The strip
// shows a small panel per pinned run with delta highlights vs the
// active run on top-level scalar metrics.  Clicking a pinned run loads
// its params back into the controls rail.
//
// V1 keeps the comparison panel intentionally tight — full-canvas
// side-by-side rendering would require duplicate chart space; the
// Header offers a "Pin run" action and we surface deltas on the
// snapshot tier so the comparison is genuinely actionable.
// ============================================================================

import { ArrowDownLeft, ArrowUpRight, Pin, X } from 'lucide-react';
import { cn } from '@/utils/cn';

export type PinnedRun = {
  id: string;
  label: string;
  params: Record<string, unknown>;
  output: Record<string, unknown>;
  /** ISO timestamp of when the run completed. */
  ranAt: string;
};

export function ComparisonStrip({
  pinnedRuns,
  activeOutput,
  onLoadParams,
  onUnpin,
}: {
  pinnedRuns: PinnedRun[];
  activeOutput: Record<string, unknown> | null;
  onLoadParams: (params: Record<string, unknown>) => void;
  onUnpin: (id: string) => void;
}) {
  if (pinnedRuns.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line-subtle bg-white/[0.005] px-3 py-3 text-center">
        <Pin size={12} className="mx-auto text-fg-faint" />
        <p className="mt-1.5 text-[10.5px] leading-snug text-fg-faint">
          No pinned runs. After a successful run, click <span className="text-fg-secondary">Pin</span> in
          the header to compare against this configuration.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {pinnedRuns.map((p) => {
        const deltas = computeDeltas(activeOutput, p.output);
        return (
          <div
            key={p.id}
            className="rounded-md border border-line-soft bg-white/[0.012] px-2.5 py-2"
          >
            <div className="flex items-start justify-between gap-2">
              <button
                type="button"
                onClick={() => onLoadParams(p.params)}
                className="min-w-0 flex-1 text-left"
                title="Load this run's params into the controls"
              >
                <p className="truncate text-[11.5px] font-semibold text-fg-primary">
                  {p.label}
                </p>
                <p className="mt-0.5 mono text-[9.5px] text-fg-faint">
                  {new Date(p.ranAt).toLocaleString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit',
                  })}
                </p>
              </button>
              <button
                type="button"
                onClick={() => onUnpin(p.id)}
                title="Unpin"
                className="rounded p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary"
              >
                <X size={11} />
              </button>
            </div>

            {deltas.length > 0 ? (
              <div className="mt-2 space-y-1">
                {deltas.slice(0, 3).map((d) => (
                  <div
                    key={d.label}
                    className="flex items-baseline justify-between text-[10.5px]"
                  >
                    <span className="mono text-fg-faint">{d.label}</span>
                    <span className="flex items-center gap-1">
                      {d.deltaSign > 0 ? (
                        <ArrowUpRight size={9} className="text-mint-400" />
                      ) : d.deltaSign < 0 ? (
                        <ArrowDownLeft size={9} className="text-coral-400" />
                      ) : null}
                      <span
                        className={cn(
                          'mono',
                          d.deltaSign > 0
                            ? 'text-mint-300'
                            : d.deltaSign < 0
                              ? 'text-coral-300'
                              : 'text-fg-muted',
                        )}
                      >
                        {d.deltaText}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delta computation — diffs top-level scalar metrics in current_metrics.
// ---------------------------------------------------------------------------

type Delta = {
  label: string;
  deltaText: string;
  deltaSign: number;
};

function computeDeltas(
  activeOutput: Record<string, unknown> | null,
  pinnedOutput: Record<string, unknown>,
): Delta[] {
  if (!activeOutput) return [];
  const a = scalars(activeOutput);
  const p = scalars(pinnedOutput);
  const out: Delta[] = [];
  for (const [k, av] of Object.entries(a)) {
    const pv = p[k];
    if (pv === undefined) continue;
    const d = av - pv;
    if (!Number.isFinite(d)) continue;
    out.push({
      label: humanLabel(k),
      deltaText:
        Math.abs(d) >= 1 ? d.toFixed(2) : Math.abs(d) >= 0.01 ? d.toFixed(3) : d.toFixed(4),
      deltaSign: d > 0 ? 1 : d < 0 ? -1 : 0,
    });
  }
  return out;
}

function scalars(output: Record<string, unknown>): Record<string, number> {
  const m = (output['current_metrics'] ?? {}) as Record<string, unknown>;
  const source = Object.keys(m).length > 0 ? m : output;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(source)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

function humanLabel(snake: string): string {
  return snake.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
