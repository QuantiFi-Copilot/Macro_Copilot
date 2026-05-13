// ============================================================================
// VariantComparisonTable — variant deltas inside the copilot rail.
// ----------------------------------------------------------------------------
// PR C — Mockup C's right rail shows a "VARIANT COMPARISON" table that
// pairs the base workspace's bound slot values against any child
// variants (workspaces forked from this one), with a colour-coded Δ
// column so the reader can scan "what changed" at a glance.
//
// Data path:
//   1. List variants via ``listWorkspaces({ parent_workspace_id })``.
//   2. Fetch each variant's ``WorkspaceDetail`` to read its
//      ``bound_slot_values``.
//   3. Compute the union of keys that differ between base + each
//      variant, render one row per differing key.
//
// Failure modes:
//   - Base workspace has NULL ``bound_slot_values`` (legacy) → the
//     table renders a single explanatory line "Variant comparison
//     unavailable — this workspace predates slot binding."
//   - Base has no variants → component renders nothing (the rail
//     simply skips the section).
//
// The component is self-contained — it owns its own data-fetch state
// and renders nothing in loading / empty branches so the rail's
// vertical rhythm stays clean.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  getWorkspace,
  listWorkspaces,
} from '@/services/workspaceApi';
import type {
  WorkspaceDetail,
  WorkspaceListItem,
} from '@/services/workspaceApi';
import { cn } from '@/utils/cn';

type Props = {
  workspace: WorkspaceDetail;
};

interface VariantBundle {
  list: WorkspaceListItem;
  detail: WorkspaceDetail;
}

interface DiffRow {
  key: string;
  base: ScalarOrMissing;
  variants: ScalarOrMissing[];
}

type ScalarOrMissing = { kind: 'value'; value: string | number } | { kind: 'missing' };

export function VariantComparisonTable({ workspace }: Props) {
  const [variants, setVariants] = useState<VariantBundle[]>([]);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>(
    'idle',
  );

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    (async () => {
      try {
        const list = await listWorkspaces({
          parent_workspace_id: workspace.workspace_id,
          limit: 8,
        });
        if (cancelled) return;
        // Fetch each variant's detail in parallel so we have its
        // bound_slot_values.  A list endpoint that returned overrides
        // inline would obsolete this; for now N≤8 parallel fetches.
        const details = await Promise.all(
          list.items.map((item) => getWorkspace(item.slug).catch(() => null)),
        );
        if (cancelled) return;
        const bundles: VariantBundle[] = [];
        for (let i = 0; i < list.items.length; i++) {
          const d = details[i];
          if (d) bundles.push({ list: list.items[i], detail: d });
        }
        setVariants(bundles);
        setStatus('ready');
      } catch {
        if (!cancelled) setStatus('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspace.workspace_id]);

  // Compute diff rows: keys whose value differs across base + variants.
  const rows = useMemo(
    () => diffRows(workspace, variants),
    [workspace, variants],
  );

  // Hide the section entirely when there are no variants OR no
  // differing keys.  Keeps the rail clean for the common single-
  // workspace case.
  if (status !== 'ready') return null;
  if (variants.length === 0) return null;

  if (!workspace.bound_slot_values) {
    return (
      <div className="rounded-md border border-line-soft bg-white/[0.012] px-3 py-2.5 text-[10.5px] italic text-fg-faint">
        Variant comparison unavailable — this workspace predates slot
        binding.
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-line-soft bg-white/[0.012] px-3 py-2.5 text-[10.5px] italic text-fg-faint">
        {variants.length} variant
        {variants.length === 1 ? '' : 's'} — no diverging slots.
      </div>
    );
  }

  return (
    <div className="rounded-md border border-line-soft bg-white/[0.012] overflow-hidden">
      <div className="flex items-center justify-between border-b border-line-soft px-3 py-1.5">
        <span className="text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
          Variant comparison
        </span>
        <span className="text-[9.5px] text-fg-faint/80">
          {variants.length} variant{variants.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[10.5px]">
          <thead>
            <tr className="border-b border-line-soft/70">
              <th className="px-3 py-1.5 text-left font-medium uppercase tracking-[0.12em] text-fg-faint">
                Slot
              </th>
              <th className="px-2 py-1.5 text-left font-medium uppercase tracking-[0.12em] text-fg-faint">
                Base
              </th>
              {variants.map((v, i) => (
                <th
                  key={v.detail.workspace_id}
                  className="px-2 py-1.5 text-left font-medium uppercase tracking-[0.12em] text-fg-faint"
                >
                  {variantHeader(v, i)}
                </th>
              ))}
              <th className="px-2 py-1.5 text-right font-medium uppercase tracking-[0.12em] text-fg-faint">
                Δ
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className="border-b border-line-soft/40 last:border-b-0"
              >
                <td className="truncate px-3 py-1.5 font-mono text-fg-secondary">
                  {row.key}
                </td>
                <td className="px-2 py-1.5 font-mono text-fg-secondary">
                  {renderScalar(row.base)}
                </td>
                {row.variants.map((v, i) => (
                  <td
                    key={i}
                    className="px-2 py-1.5 font-mono text-fg-secondary"
                  >
                    {renderScalar(v)}
                  </td>
                ))}
                <td className="px-2 py-1.5 text-right font-mono">
                  {renderDelta(row.base, row.variants[0])}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function variantHeader(v: VariantBundle, i: number): string {
  // Prefer the workspace's display name; fall back to "Variant A/B/C".
  const letter = String.fromCharCode('A'.charCodeAt(0) + i);
  if (v.list.name && v.list.name.trim()) {
    return v.list.name.trim().slice(0, 14);
  }
  return `Variant ${letter}`;
}

function renderScalar(s: ScalarOrMissing): string {
  if (s.kind === 'missing') return '—';
  return formatValue(s.value);
}

function renderDelta(base: ScalarOrMissing, variant: ScalarOrMissing) {
  if (base.kind === 'missing' || variant.kind === 'missing') {
    return <span className="text-fg-faint/70">—</span>;
  }
  if (typeof base.value !== 'number' || typeof variant.value !== 'number') {
    if (base.value === variant.value) {
      return <span className="text-fg-faint/70">—</span>;
    }
    return <span className="text-fg-faint">≠</span>;
  }
  const diff = variant.value - base.value;
  if (diff === 0) return <span className="text-fg-faint/70">—</span>;
  const tone =
    diff > 0
      ? 'text-emerald-300'
      : 'text-rose-300';
  const sign = diff > 0 ? '+' : '';
  return (
    <span className={cn('font-mono', tone)}>
      {sign}
      {formatValue(diff)}
    </span>
  );
}

function formatValue(v: string | number): string {
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v);
    return v.toPrecision(4);
  }
  return v;
}

function diffRows(
  base: WorkspaceDetail,
  variants: VariantBundle[],
): DiffRow[] {
  if (!base.bound_slot_values) return [];
  const baseFlat = flattenSlots(base.bound_slot_values);
  const variantFlats = variants.map((v) =>
    flattenSlots(v.detail.bound_slot_values ?? {}),
  );

  // Build the union of keys across base + variants.  Only keep keys
  // whose value differs in at least one variant.
  const allKeys = new Set<string>();
  for (const k of Object.keys(baseFlat)) allKeys.add(k);
  for (const flat of variantFlats) {
    for (const k of Object.keys(flat)) allKeys.add(k);
  }

  const rows: DiffRow[] = [];
  for (const key of allKeys) {
    const baseVal = scalarOrMissing(baseFlat[key]);
    const variantVals = variantFlats.map((flat) =>
      scalarOrMissing(flat[key]),
    );
    const anyDiff = variantVals.some(
      (v) => !scalarsEqual(baseVal, v),
    );
    if (anyDiff) {
      rows.push({ key, base: baseVal, variants: variantVals });
    }
  }
  rows.sort((a, b) => a.key.localeCompare(b.key));
  return rows;
}

function flattenSlots(
  obj: Record<string, unknown>,
  prefix = '',
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${k}` : k;
    if (
      v != null &&
      typeof v === 'object' &&
      !Array.isArray(v) &&
      Object.keys(v as Record<string, unknown>).length > 0
    ) {
      Object.assign(out, flattenSlots(v as Record<string, unknown>, fullKey));
    } else {
      out[fullKey] = v;
    }
  }
  return out;
}

function scalarOrMissing(v: unknown): ScalarOrMissing {
  if (v === undefined) return { kind: 'missing' };
  if (typeof v === 'string' || typeof v === 'number') {
    return { kind: 'value', value: v };
  }
  if (typeof v === 'boolean') {
    return { kind: 'value', value: v ? 'true' : 'false' };
  }
  if (v === null) return { kind: 'value', value: 'null' };
  if (Array.isArray(v)) {
    return { kind: 'value', value: `[${v.length}]` };
  }
  return { kind: 'value', value: '{…}' };
}

function scalarsEqual(a: ScalarOrMissing, b: ScalarOrMissing): boolean {
  if (a.kind === 'missing' && b.kind === 'missing') return true;
  if (a.kind === 'missing' || b.kind === 'missing') return false;
  return a.value === b.value;
}
