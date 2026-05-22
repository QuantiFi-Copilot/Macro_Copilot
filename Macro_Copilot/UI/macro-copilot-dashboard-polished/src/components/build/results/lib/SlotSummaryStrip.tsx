// ============================================================================
// SlotSummaryStrip — read-only slot-value chips for a dashboard header.
// ----------------------------------------------------------------------------
// PR7 — surfaces the workspace's ``bound_slot_values`` as a compact
// chip strip at the top of a specialised dashboard so the user sees
// the actual threshold / curve / tenor / window that produced the
// downstream artifacts.  Read-only here; mutation happens in the
// Parameters tab (PR5).
//
// When ``bound_slot_values`` is null (legacy workspace), the strip
// silently renders nothing — the dashboard's regular sections still
// show their artifacts.
// ============================================================================

import type { WorkspaceDetail } from '@/services/workspaceApi';

type Props = {
  detail: WorkspaceDetail;
  /** Slot names to surface, in order.  Slots not present in
   *  ``bound_slot_values`` are skipped silently. */
  highlightSlots: string[];
};

export function SlotSummaryStrip({ detail, highlightSlots }: Props) {
  const bound = detail.bound_slot_values ?? {};
  const chips: Array<{ name: string; value: string }> = [];
  for (const name of highlightSlots) {
    if (!(name in bound)) continue;
    const raw = bound[name];
    const value = formatChipValue(raw);
    if (value === null) continue;
    chips.push({ name, value });
  }
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <span
          key={c.name}
          className="inline-flex items-center gap-1.5 rounded-sm border border-line-soft bg-white/[0.025] px-1.5 py-0.5 font-mono text-[10px]"
          title={`Slot: ${c.name}`}
        >
          <span className="text-fg-faint">{prettyName(c.name)}</span>
          <span className="text-fg-secondary">{c.value}</span>
        </span>
      ))}
    </div>
  );
}

function formatChipValue(raw: unknown): string | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'string' || typeof raw === 'number' || typeof raw === 'boolean') {
    return String(raw);
  }
  // Plain object / array — render a compact shape hint rather than
  // a serialised blob (the Parameters tab is the canonical full
  // view).
  if (Array.isArray(raw)) return `[${raw.length} items]`;
  if (typeof raw === 'object') {
    const keys = Object.keys(raw as Record<string, unknown>);
    return `{${keys.slice(0, 3).join(', ')}${keys.length > 3 ? '…' : ''}}`;
  }
  return null;
}

function prettyName(snake: string): string {
  return snake
    .split('_')
    .filter(Boolean)
    .map((tok) => tok.charAt(0).toUpperCase() + tok.slice(1).toLowerCase())
    .join(' ');
}
