// ============================================================================
// InterpretationCards — short, opinionated "how to read this" cards
// ----------------------------------------------------------------------------
// Sources from the model registry's interpretationCards array.  Renders a
// small column under the output canvas so the PM has a rote-friendly
// reading guide alongside the numbers.
// ============================================================================

import { Sparkles } from 'lucide-react';
import { getModelMetadata } from '@/lib/modelRegistry';

export function InterpretationCards({ toolName }: { toolName: string }) {
  const meta = getModelMetadata(toolName);
  const cards = meta?.interpretationCards ?? [];
  if (cards.length === 0) return null;
  return (
    <section className="card px-5 py-4">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles size={12} className="text-ice-300" />
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-fg-secondary">
          How to read this
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {cards.map((c, i) => (
          <div
            key={i}
            className="rounded-md border border-line-subtle bg-white/[0.008] px-3 py-2.5"
          >
            <p className="text-[11.5px] font-semibold text-fg-primary">
              {c.headline}
            </p>
            <p className="mt-1 text-[11px] leading-[1.55] text-fg-secondary">
              {c.body}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
