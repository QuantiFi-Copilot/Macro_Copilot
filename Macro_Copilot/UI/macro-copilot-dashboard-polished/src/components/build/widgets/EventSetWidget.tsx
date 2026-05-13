// ============================================================================
// EventSetWidget — renderer for EventSet artifacts.
// ----------------------------------------------------------------------------
// EventSets are produced by ``threshold_events`` (and any future
// event-extracting operator) — a sparse list of dates flagged by some
// rule on an upstream Series.
//
// V1 render: count of events + first / last event date strip.  The
// substrate doesn't pack event values into the summary preview the
// same way Series does, so we don't try to draw a sparkline here;
// we surface what an EventSet IS for a desk analyst — "13 events on
// these specific days".
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';

const EventSetWidget: NodeRenderer = ({ artifact }) => {
  const eventCount = artifact.row_count ?? 0;

  const firstDate = useMemo(() => {
    const idx = artifact.preview_index ?? [];
    return idx[0] ?? null;
  }, [artifact]);
  const lastDate = useMemo(() => {
    const idx = artifact.preview_index ?? [];
    return idx.length > 0 ? idx[idx.length - 1] : null;
  }, [artifact]);

  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-4">
      <div className="flex items-baseline gap-2">
        <span className="font-serif-display text-[28px] font-light leading-none text-fg-primary">
          {eventCount.toLocaleString()}
        </span>
        <span className="text-[11px] text-fg-muted">
          {eventCount === 1 ? 'event flagged' : 'events flagged'}
        </span>
      </div>
      {(firstDate || lastDate) && (
        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2">
          <DateCell label="First event" value={firstDate} />
          <DateCell label="Last event" value={lastDate} />
        </div>
      )}
    </div>
  );
};

function DateCell({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {label}
      </span>
      <span className="truncate font-mono text-[12px] text-fg-secondary">
        {value ?? '—'}
      </span>
    </div>
  );
}

registerArtifactRenderer('EventSet', EventSetWidget);
export { EventSetWidget };
