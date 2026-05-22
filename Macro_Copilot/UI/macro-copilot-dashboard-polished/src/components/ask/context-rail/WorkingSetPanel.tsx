// ============================================================================
// WorkingSetPanel — "every artifact this thread has produced"
// ----------------------------------------------------------------------------
// The Jupyter-kernel model made visible.  Lists every artifact the
// assistant has produced this thread (workflow terminals + intermediate
// tool outputs), each with its name, type, units, and a 6-char short
// hash.
//
// Click a row to scroll the conversation to the message that produced
// it.  The empty state is intentionally informative — explains the
// working-set concept rather than just saying "no items".
// ============================================================================

import { Boxes } from 'lucide-react';
import type { WorkingSetArtifact } from '@/components/ask/lib/deriveContext';

type Props = {
  artifacts: WorkingSetArtifact[];
  onArtifactClick?: (artifact: WorkingSetArtifact) => void;
};

export function WorkingSetPanel({ artifacts, onArtifactClick }: Props) {
  return (
    <section className="px-4 pt-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Boxes size={11} className="text-lineage-300/70" />
          <h3 className="kicker text-fg-muted">
            WORKING SET · {artifacts.length} {artifacts.length === 1 ? 'ARTIFACT' : 'ARTIFACTS'}
          </h3>
        </div>
      </div>

      {artifacts.length === 0 ? (
        <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-muted">
          Artifacts produced by the orchestrator (Series, EventSets, Panels)
          appear here as the conversation runs. Each carries its lineage hash
          so you can replay or compare.
        </p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {artifacts.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => onArtifactClick?.(a)}
                className="group flex w-full flex-col gap-0.5 rounded-md px-2 py-1.5 text-left transition-colors duration-150 ease-sleek hover:bg-white/[0.025]"
              >
                <span className="mono truncate text-[11.5px] font-medium text-fg-primary">
                  {a.name}
                </span>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="mono truncate text-[10px] text-fg-secondary">
                    {a.typeLabel}
                  </span>
                  <span className="mono shrink-0 text-[10px] text-lineage-300/70">
                    {a.shortHash}
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
