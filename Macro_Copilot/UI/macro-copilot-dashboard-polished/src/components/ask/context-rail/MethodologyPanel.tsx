// ============================================================================
// MethodologyPanel — "every tool / convention surface this thread is
// running on"
// ----------------------------------------------------------------------------
// V1: lists tools the supervisor has called this thread, with a call-
// count badge and a placeholder source-tag dot.  When the backend
// extends `tool_result` with per-call source-tag distribution, this
// panel lights up with real per-tool convention summaries.
//
// The empty state is the brief's invitation: ask a question and the
// methodology will populate.
// ============================================================================

import { BookOpen } from 'lucide-react';
import type { MethodologyEntry } from '@/components/ask/lib/deriveContext';

type Props = {
  entries: MethodologyEntry[];
};

export function MethodologyPanel({ entries }: Props) {
  return (
    <section className="px-4 pt-4">
      <div className="mb-2 flex items-center gap-2">
        <BookOpen size={11} className="text-lineage-300/70" />
        <h3 className="kicker text-fg-muted">
          METHODOLOGY · {entries.length} {entries.length === 1 ? 'TOOL' : 'TOOLS'}
        </h3>
      </div>

      {entries.length === 0 ? (
        <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-muted">
          Every tool the orchestrator calls ships with a versioned methodology
          (windows, fill policies, citations). They list here, with their
          source-tag distribution.
        </p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {entries.map((e) => (
            <li
              key={e.toolName}
              className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5"
            >
              <div className="flex min-w-0 items-center gap-2">
                {/* V1 placeholder source-tag dot — assumes industry-
                    standard until the backend ships real metadata. */}
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-mint-400/70" />
                <span className="mono truncate text-[11.5px] text-fg-secondary">
                  {e.toolName}
                </span>
              </div>
              {e.callCount > 1 && (
                <span className="mono shrink-0 text-[10px] text-fg-faint">
                  ×{e.callCount}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
