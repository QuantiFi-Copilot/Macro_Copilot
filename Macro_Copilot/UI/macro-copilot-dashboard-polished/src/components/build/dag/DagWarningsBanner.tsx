// ============================================================================
// DagWarningsBanner — surfaces ``DagModel.warnings`` as inline alerts.
// ----------------------------------------------------------------------------
// PR6 — closed-vocabulary warnings (``cycle_detected``,
// ``missing_edges_fallback``, ``orphan_edges``, ``no_nodes``) get
// honest banner text from ``describeWarning``.  Rendering is
// inline above the canvas so the user can't miss a degraded
// topology while inspecting the graph.
// ============================================================================

import { AlertTriangle } from 'lucide-react';
import { describeWarning, type DagWarning } from './lib/buildDagModel';

type Props = {
  warnings: DagWarning[];
};

export function DagWarningsBanner({ warnings }: Props) {
  if (warnings.length === 0) return null;
  return (
    <div className="flex shrink-0 flex-col gap-1.5 border-b border-line-subtle bg-amber-500/[0.04] px-6 py-2.5">
      {warnings.map((w) => (
        <div key={w} className="flex items-start gap-2 text-[10.5px]">
          <AlertTriangle
            size={11}
            strokeWidth={1.75}
            aria-hidden
            className="mt-0.5 shrink-0 text-amber-300"
          />
          <p className="leading-[1.5] text-fg-secondary">
            <span className="font-mono uppercase tracking-[0.1em] text-amber-200">
              {w.replace(/_/g, ' ')}
            </span>
            <span className="ml-2">{describeWarning(w)}</span>
          </p>
        </div>
      ))}
    </div>
  );
}
