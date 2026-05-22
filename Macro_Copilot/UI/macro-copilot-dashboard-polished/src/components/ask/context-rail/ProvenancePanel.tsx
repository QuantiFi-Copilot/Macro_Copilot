// ============================================================================
// ProvenancePanel — step-walk for the most recent assistant turn
// ----------------------------------------------------------------------------
// Surfaces the lineage chain that produced the latest answer as a
// numbered vertical list.  Each step shows its kind (primitive /
// operator / terminal), its name, and an optional duration when known.
//
// V1 derives the steps from `workflow_lineage_summary` (workflow turns)
// or the message's traceSteps (supervisor turns).  V2 will display
// real primitive step hashes once the backend streams them.
// ============================================================================

import { GitBranch } from 'lucide-react';
import type { LineageStep } from '@/components/ask/lib/deriveContext';
import { cn } from '@/utils/cn';

type Props = {
  steps: LineageStep[];
};

export function ProvenancePanel({ steps }: Props) {
  return (
    <section className="px-4 pb-4 pt-4">
      <div className="mb-2 flex items-center gap-2">
        <GitBranch size={11} className="text-lineage-300/70" />
        <h3 className="kicker text-fg-muted">LATEST LINEAGE</h3>
      </div>

      {steps.length === 0 ? (
        <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-muted">
          The provenance walk for the most recent answer appears here once the
          orchestrator returns a result.
        </p>
      ) : (
        <ol className="mt-1 space-y-0">
          {steps.map((s, i) => (
            <li key={s.index} className="relative">
              {/* Step row */}
              <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
                <span
                  className={cn(
                    'mono shrink-0 text-[10px] font-semibold',
                    s.kind === 'terminal'
                      ? 'text-mint-300'
                      : s.kind === 'primitive'
                        ? 'text-ice-300'
                        : 'text-lineage-300',
                  )}
                >
                  {String(s.index).padStart(2, '0')}
                </span>
                <span
                  className={cn(
                    'shrink-0 rounded text-[8.5px] font-semibold uppercase tracking-[0.12em] px-1 py-px',
                    s.kind === 'terminal'
                      ? 'border border-mint-400/30 bg-mint-400/[0.06] text-mint-300'
                      : s.kind === 'primitive'
                        ? 'border border-ice-400/25 bg-ice-500/[0.06] text-ice-300'
                        : 'border border-lineage-400/25 bg-lineage-500/[0.06] text-lineage-300',
                  )}
                >
                  {s.kind === 'terminal' ? 'TERM' : s.kind === 'primitive' ? 'PRIM' : 'OP'}
                </span>
                <span className="mono min-w-0 flex-1 truncate text-[11px] text-fg-secondary">
                  {s.name}
                </span>
                {s.durationMs != null && (
                  <span className="mono shrink-0 text-[9.5px] text-fg-faint">
                    {s.durationMs}ms
                  </span>
                )}
              </div>
              {/* Vertical connector to next step */}
              {i < steps.length - 1 && (
                <span
                  aria-hidden
                  className="absolute left-[14px] top-[26px] h-3 w-px bg-line-soft"
                />
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
