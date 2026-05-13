// ============================================================================
// WindowedPanelWidget — payload-backed renderer for WindowedPanel artifacts.
// ----------------------------------------------------------------------------
// PR4 — loads the persisted WindowedPanel body via the PR3 hook and
// shows the event-window anatomy: event count, offset range with
// ``t-N / t0 / t+N`` labels, and a small head-of-events preview table
// showing values at the negative / zero / positive offsets.  Before
// PR4 the widget rendered only the row count + a sparkline of the
// flattened series, which collapsed exactly the dimensions an analyst
// needs to see (offsets and events).
//
// Wire shape recap
// ----------------
// ``WindowedPanelPayloadBody`` = ``{data, event_dates, per_event_metadata}``
// with ``data[event_idx][offset_idx]`` numeric matrix and
// ``metadata.offsets`` carrying the column axis (e.g.
// ``[-5, -4, …, +4, +5]`` for a +/- 5-day window).  The aggregate
// path (Series-shaped average across events) is NOT in this payload
// — it's emitted as a separate Series artifact by the next operator.
//
// Layout
// ------
//   - Header strip: event count · offset range (e.g. ``t-5 … t+5``) ·
//     units.
//   - Compact preview table: first M events × every offset, with
//     date labels and ``formatNumberWithUnits``-formatted cells.
//   - Overflow indicator when there are more events than rendered.
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { PayloadShell } from './shared/PayloadShell';
import {
  formatDate,
  formatNumberWithUnits,
  MISSING_VALUE_DASH,
  windowedEventCount,
  windowedOffsets,
  type WindowedPanelPayloadEnvelope,
} from './shared/artifactFormat';

/** Event-preview row count.  Each row is wide (one column per
 *  offset), so we keep this small even on the wide-card size. */
const EVENT_PREVIEW_LIMIT_HEAD = 3;
const EVENT_PREVIEW_LIMIT_TAIL = 1;
/** Render at most this many offset columns in the preview table.  The
 *  full event-window is on the artifact's payload (PR5+ may expose a
 *  detail view); the card just needs to convey the anatomy. */
const OFFSET_PREVIEW_LIMIT = 8;

const WindowedPanelWidget: NodeRenderer = ({ node, artifact, size }) => {
  return (
    <PayloadShell<WindowedPanelPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="WindowedPanel"
      displayName="WindowedPanel"
      isEmpty={(p) => windowedEventCount(p) === 0}
      emptyMessage="The event-windowed panel persisted but contains no events."
    >
      {(payload) => <WindowedBody payload={payload} size={size} />}
    </PayloadShell>
  );
};

function WindowedBody({
  payload,
  size,
}: {
  payload: WindowedPanelPayloadEnvelope;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  const offsets = useMemo(() => windowedOffsets(payload), [payload]);
  const eventCount = useMemo(() => windowedEventCount(payload), [payload]);
  const units = payload.metadata.units;

  const offsetRange = useMemo(() => {
    if (offsets.length === 0) return null;
    return {
      first: offsets[0],
      last: offsets[offsets.length - 1],
    };
  }, [offsets]);

  const visibleOffsets = useMemo(() => {
    if (offsets.length <= OFFSET_PREVIEW_LIMIT) {
      return { offsets, indices: offsets.map((_, i) => i) };
    }
    // Walk evenly across the offset axis so the preview always
    // includes t-N, somewhere near t0, and t+N — never just the
    // leading window.
    const indices = pickEvenIndices(offsets.length, OFFSET_PREVIEW_LIMIT);
    return {
      offsets: indices.map((i) => offsets[i]),
      indices,
    };
  }, [offsets]);
  const omittedOffsets = Math.max(
    0,
    offsets.length - visibleOffsets.offsets.length,
  );

  const eventSlots = useMemo(
    () => buildEventSlots(eventCount, size === 'small'),
    [eventCount, size],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-3">
      <div className="kicker text-fg-muted">
        {eventCount.toLocaleString()} event{eventCount === 1 ? '' : 's'} ·{' '}
        {offsetLabel(offsetRange)} · {offsets.length} offset
        {offsets.length === 1 ? '' : 's'}
      </div>

      <div className="mt-2 overflow-x-auto">
        <table className="min-w-full border-collapse text-[10.5px] tabular-nums">
          <thead>
            <tr className="border-b border-line-subtle">
              <th className="py-1 pr-3 text-left font-medium uppercase tracking-[0.1em] text-fg-faint">
                event
              </th>
              {visibleOffsets.offsets.map((o) => (
                <th
                  key={o}
                  className="py-1 pr-3 text-right font-mono font-medium text-fg-faint"
                >
                  {offsetTick(o)}
                </th>
              ))}
              {omittedOffsets > 0 && (
                <th className="py-1 text-right font-medium text-fg-faint">
                  +{omittedOffsets}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {eventSlots.map((slot) =>
              slot === 'ellipsis' ? (
                <tr key="ellipsis" className="border-t border-line-subtle/60">
                  <td
                    className="py-1 text-fg-faint"
                    colSpan={
                      visibleOffsets.offsets.length +
                      1 +
                      (omittedOffsets > 0 ? 1 : 0)
                    }
                  >
                    …
                  </td>
                </tr>
              ) : (
                <tr key={`e${slot}`} className="border-t border-line-subtle/40">
                  <td className="py-1 pr-3 font-mono text-fg-secondary">
                    {formatDate(payload.payload.event_dates?.[slot])}
                  </td>
                  {visibleOffsets.indices.map((oIdx) => {
                    const v = payload.payload.data?.[slot]?.[oIdx];
                    return (
                      <td
                        key={oIdx}
                        className="py-1 pr-3 text-right font-mono text-fg-primary"
                      >
                        {formatNumberWithUnits(v, units)}
                      </td>
                    );
                  })}
                  {omittedOffsets > 0 && (
                    <td className="py-1 text-right font-mono text-fg-faint">
                      {MISSING_VALUE_DASH}
                    </td>
                  )}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Format an integer offset as ``t-N`` / ``t0`` / ``t+N``.  Used in
 *  table column headers so the analyst reads the offset axis as
 *  "days from the event" rather than as a raw integer. */
function offsetTick(o: number): string {
  if (o === 0) return 't0';
  if (o > 0) return `t+${o}`;
  return `t${o}`; // negatives already have the sign baked in
}

function offsetLabel(range: { first: number; last: number } | null): string {
  if (!range) return 'no offsets';
  if (range.first === range.last) return `offset ${offsetTick(range.first)}`;
  return `${offsetTick(range.first)} … ${offsetTick(range.last)}`;
}

/** Pick which event rows to render: first N + ellipsis + last M.
 *  Compact mode trims to first 2 + last 1. */
function buildEventSlots(
  totalEvents: number,
  compact: boolean,
): Array<number | 'ellipsis'> {
  const head = compact ? 2 : EVENT_PREVIEW_LIMIT_HEAD;
  const tail = compact ? 0 : EVENT_PREVIEW_LIMIT_TAIL;
  if (totalEvents <= head + tail) {
    return Array.from({ length: totalEvents }, (_, i) => i);
  }
  const slots: Array<number | 'ellipsis'> = [];
  for (let i = 0; i < head; i++) slots.push(i);
  if (tail > 0) {
    slots.push('ellipsis');
    for (let i = totalEvents - tail; i < totalEvents; i++) slots.push(i);
  } else {
    slots.push('ellipsis');
  }
  return slots;
}

/** Pick ``count`` evenly-spaced indices from ``[0, total)`` including
 *  both endpoints.  Used to thin the offset axis while keeping t-N
 *  and t+N visible. */
function pickEvenIndices(total: number, count: number): number[] {
  if (count >= total) return Array.from({ length: total }, (_, i) => i);
  if (count <= 1) return [0];
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    out.push(Math.round((i * (total - 1)) / (count - 1)));
  }
  // Deduplicate (rounding can collide at small totals) while
  // preserving order.
  return [...new Set(out)];
}

registerArtifactRenderer('WindowedPanel', WindowedPanelWidget);
export { WindowedPanelWidget };
