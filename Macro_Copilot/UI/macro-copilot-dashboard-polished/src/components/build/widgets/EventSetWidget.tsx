// ============================================================================
// EventSetWidget — payload-backed renderer for EventSet artifacts.
// ----------------------------------------------------------------------------
// PR4 — loads the persisted EventSet body via the PR3 hook and renders
// the actual event count from ``payload.event_dates.length``.  Before
// PR4 the widget used the artifact summary's row-count field as the
// event count; that field is the mask LENGTH (e.g. 1,281 trading
// days), not the count of dates where the mask is true.  The bug
// surfaced on every event study card (the user audit's "1,281 events"
// complaint).
//
// What this widget renders
// ------------------------
//   - The headline event count (true count of ``event_dates``).
//   - First / last event dates (defensively formatted; null on bad
//     dates rather than the misleading 1970 epoch fallback).
//   - A compact preview list of the first N event dates.
//   - If ``per_event_metadata`` carries human-readable per-event
//     labels (e.g. ``label`` / ``name``), surface them next to the
//     dates.
//
// What this widget DOES NOT do
// ----------------------------
//   - Never reads the artifact-summary row-count field for the count.
//   - Never re-runs the threshold-events primitive.
//   - Never invents preview dates from the artifact-summary preview-
//     index strip — those are mask-position previews, which include
//     non-event rows.
//
// Empty-state contract
// --------------------
// Zero-event EventSets are common — they're the "the rule didn't fire
// during this window" honest signal.  We render an explicit "0 events
// flagged" state so the analyst doesn't mistake it for a missing
// payload.
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { PayloadShell } from './shared/PayloadShell';
import {
  eventCountFromPayload,
  formatDate,
  type EventSetPayloadEnvelope,
} from './shared/artifactFormat';

const PREVIEW_LIMIT = 6;

const EventSetWidget: NodeRenderer = ({ node, artifact }) => {
  return (
    <PayloadShell<EventSetPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="EventSet"
      displayName="EventSet"
      // NOTE: zero events is NOT "empty payload" — it's a valid honest
      // result.  We render it directly rather than route through the
      // shell's empty branch.
    >
      {(payload) => <EventSetBody payload={payload} />}
    </PayloadShell>
  );
};

function EventSetBody({ payload }: { payload: EventSetPayloadEnvelope }) {
  const eventCount = eventCountFromPayload(payload);
  const events = useMemo(
    () => buildPreviewEvents(payload, PREVIEW_LIMIT),
    [payload],
  );
  const firstDate = events.length > 0 ? events[0].date : null;
  const lastDate = useMemo(() => {
    const dates = payload.payload.event_dates ?? [];
    return dates.length > 0 ? dates[dates.length - 1] : null;
  }, [payload]);

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

      {eventCount === 0 ? (
        <p className="mt-3 text-[11px] leading-[1.5] text-fg-secondary">
          The threshold rule didn&apos;t fire on any date in the
          source series&apos; coverage window.  This is an honest zero
          — not a missing payload.
        </p>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2">
            <DateCell label="First event" value={formatDate(firstDate)} />
            <DateCell label="Last event" value={formatDate(lastDate)} />
          </div>

          {events.length > 0 && (
            <div className="mt-4 flex flex-col gap-1.5">
              <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
                Events ({Math.min(events.length, PREVIEW_LIMIT)} of{' '}
                {eventCount.toLocaleString()})
              </span>
              <ul className="flex flex-col gap-0.5">
                {events.slice(0, PREVIEW_LIMIT).map((e, i) => (
                  <li
                    key={`${e.date}-${i}`}
                    className="flex items-baseline justify-between gap-2 text-[10.5px] leading-[1.4]"
                  >
                    <span className="font-mono text-fg-secondary">
                      {formatDate(e.date)}
                    </span>
                    {e.label && (
                      <span className="truncate text-fg-faint">{e.label}</span>
                    )}
                  </li>
                ))}
              </ul>
              {eventCount > PREVIEW_LIMIT && (
                <span className="text-[9.5px] text-fg-faint">
                  +{(eventCount - PREVIEW_LIMIT).toLocaleString()} more
                </span>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function DateCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {label}
      </span>
      <span className="truncate font-mono text-[12px] text-fg-secondary">
        {value}
      </span>
    </div>
  );
}

/** Build a preview list of ``{date, label?}`` pairs from the payload.
 *  Per-event metadata MAY contain labels under ``label`` or ``name``
 *  conventions; if neither is present we ship date-only entries. */
function buildPreviewEvents(
  payload: EventSetPayloadEnvelope,
  limit: number,
): Array<{ date: string; label?: string }> {
  const dates = payload.payload.event_dates ?? [];
  const meta = payload.payload.per_event_metadata ?? [];
  const out: Array<{ date: string; label?: string }> = [];
  for (let i = 0; i < Math.min(dates.length, limit); i++) {
    const date = dates[i];
    const m = meta[i] as Record<string, unknown> | undefined;
    const labelCandidate =
      (m?.label as string | undefined) ??
      (m?.name as string | undefined) ??
      undefined;
    out.push({
      date,
      label:
        typeof labelCandidate === 'string' && labelCandidate.length > 0
          ? labelCandidate
          : undefined,
    });
  }
  return out;
}

registerArtifactRenderer('EventSet', EventSetWidget);
export { EventSetWidget };
