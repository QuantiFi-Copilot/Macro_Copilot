// ============================================================================
// ProvenanceRow — the always-visible auditability strip
// ----------------------------------------------------------------------------
// One thin row near the bottom of every research card surfacing:
//   - Convention provenance (count + source-tag distribution dots)
//   - Lineage hash chip (V1: deterministic short hash; V2: real
//     content-addressed hash from the substrate)
//   - Execution time
//   - Data context (event count / observation count for workflow turns)
//
// V1 conventions surface is intentionally minimal — the WS doesn't yet
// stream per-tool convention metadata.  We surface what we know:
// number of tools that ran + a "all industry-standard (per registry
// default)" dot strip.  When the backend adds per-call source-tag
// metadata, this row is where it lights up.
// ============================================================================

import { Hash } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';
import { cn } from '@/utils/cn';

type Props = {
  message: CopilotMessage;
};

export function ProvenanceRow({ message }: Props) {
  // Provenance is undefined while the LLM is still streaming the
  // routing decision / first tool call.  Suppress the row in that
  // case rather than render an empty skeleton.
  const completedTools = message.traceSteps.filter(
    (s) => s.status === 'complete',
  );
  const hasWorkflow = !!message.workflow?.result;
  if (completedTools.length === 0 && !hasWorkflow) return null;

  // For workflow turns we surface the workflow lineage hash.  For
  // supervisor turns we synthesize from message id + first tool.
  const hashSeed =
    message.workflow?.result?.workflow_lineage_summary ??
    `${message.id}::${completedTools[0]?.tool ?? 'unknown'}`;
  const shortHash = stableShortHash(hashSeed);

  const dotCount = Math.max(completedTools.length, 1);
  const totalDuration = message.totalDurationMs;

  // Data context — workflow event/obs counts, otherwise tool count
  const dataContext = formatDataContext(message);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line-subtle px-5 py-2.5">
      {/* Convention dots */}
      <div className="flex items-center gap-1.5">
        <span className="flex items-center gap-[3px]">
          {Array.from({ length: Math.min(dotCount, 6) }).map((_, i) => (
            <span
              key={i}
              className="h-[5px] w-[5px] rounded-full bg-mint-400/60"
            />
          ))}
        </span>
        <span className="text-[10.5px] text-fg-muted">
          {completedTools.length === 1
            ? '1 tool'
            : `${completedTools.length} tools`}{' '}
          · industry-standard defaults
        </span>
      </div>

      <Sep />

      {/* Lineage hash */}
      <span className="lineage-chip" title="Content-addressed lineage hash (V1: deterministic stub)">
        <Hash size={9} />
        <span>lineage {shortHash}</span>
      </span>

      {totalDuration != null && (
        <>
          <Sep />
          <span className="mono text-[10.5px] text-fg-muted">
            {totalDuration < 1000
              ? `${totalDuration} ms`
              : `${(totalDuration / 1000).toFixed(1)} s`}
          </span>
        </>
      )}

      {dataContext && (
        <>
          <Sep />
          <span className={cn('mono text-[10.5px] text-fg-muted')}>
            {dataContext}
          </span>
        </>
      )}
    </div>
  );
}

function Sep() {
  return <span className="text-fg-faint">·</span>;
}

function formatDataContext(message: CopilotMessage): string | null {
  const a = message.workflow?.result?.terminal_artifact;
  if (!a) return null;
  if (a.type === 'EventSet') {
    return `${a.n_events ?? 0} events / ${a.n_dates ?? 0} dates`;
  }
  if (a.type === 'Series' && a.index_kind === 'event_relative_offset') {
    const stats = a.summary_stats;
    if (stats && stats.n_finite != null) {
      return `${stats.n_finite} finite obs across ${a.n_rows} horizons`;
    }
    return `${a.n_rows} horizons`;
  }
  if (a.type === 'Series') {
    return `${a.n_rows} obs`;
  }
  return null;
}

function stableShortHash(seed: string): string {
  // Same DJB2-style hash as deriveContext.ts — kept inline so this
  // module is independent of derive helpers.  Returns 6 hex chars.
  let h = 5381;
  for (let i = 0; i < seed.length; i++) {
    h = ((h << 5) + h + seed.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(16).padStart(8, '0').slice(-6);
}
