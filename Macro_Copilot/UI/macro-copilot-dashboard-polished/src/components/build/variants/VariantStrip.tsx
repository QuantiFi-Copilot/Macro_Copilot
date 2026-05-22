// ============================================================================
// VariantStrip — sibling workspaces for the active variant family.
// ----------------------------------------------------------------------------
// Renders beneath the BuildHeader on completed workspaces.  Two roles:
//
//   - When the active workspace was forked from a parent, the strip
//     shows the parent's other children + a back-to-parent link.  The
//     active workspace itself is pinned in the grid as a "Base" card
//     so the user reads the full variant family at a glance.
//
//   - When the active workspace has no parent (top-level run), the
//     strip shows its direct children.
//
// Hidden when there are no variants to surface — the strip never
// renders an empty card.
//
// Visual register matches the rest of the polished Build surfaces:
// kicker section header, count chip, three-column research-card grid.
// Comparison-metric deltas (CAGR / Sharpe / Max DD) ship in a follow-up
// PR once the substrate exposes per-variant performance summaries.
// ============================================================================

import { useMemo } from 'react';
import { Boxes, ChevronUp, Sparkles } from 'lucide-react';
import { useWorkspaceSiblings } from '@/hooks/useWorkspaceSiblings';
import type { WorkspaceDetail, WorkspaceListItem } from '@/services/workspaceApi';
import { VariantSiblingCard } from './VariantSiblingCard';

const MAX_VISIBLE = 6;

type Props = {
  detail: WorkspaceDetail;
};

export function VariantStrip({ detail }: Props) {
  // Two roles:
  //   - if this workspace HAS a parent → look up siblings via parent
  //   - if this workspace has children → look up children via self
  // We can't know in advance which role applies, so we make two
  // queries lazily.  The hook short-circuits to ``idle`` when its
  // rootId is null.
  const asChild = useWorkspaceSiblings({
    selfId: detail.workspace_id,
    rootId: detail.parent_workspace_id ?? null,
  });
  const asParent = useWorkspaceSiblings({
    selfId: null, // own children are never filtered out
    rootId: detail.parent_workspace_id ? null : detail.workspace_id,
  });

  const role: 'child' | 'parent' = detail.parent_workspace_id
    ? 'child'
    : 'parent';
  const siblings = role === 'child' ? asChild.siblings : asParent.siblings;

  // When the active workspace is a child of a fork tree, pin it as a
  // "Base" card so the user reads the variant family inclusively.
  // When the active workspace IS the parent, we don't add a pin — the
  // strip is "your variants", not "you + your variants".
  const activePin = useMemo<WorkspaceListItem | null>(() => {
    if (role !== 'child') return null;
    return {
      workspace_id: detail.workspace_id,
      slug: detail.slug,
      name: detail.name,
      dag_hash: detail.dag_hash,
      focus_node: detail.focus_node,
      parent_workspace_id: detail.parent_workspace_id,
      created_by: detail.created_by,
      created_at: detail.created_at,
      updated_at: detail.updated_at,
    };
  }, [detail, role]);

  const visible = useMemo(
    () => siblings.slice(0, MAX_VISIBLE),
    [siblings],
  );

  if (visible.length === 0 && !activePin) return null;

  return (
    <section className="flex flex-col gap-3 border-b border-line-subtle px-6 pt-4 pb-5">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-2">
          <Boxes
            size={12}
            strokeWidth={1.75}
            className="text-ice-300"
            aria-hidden
          />
          <h3 className="kicker text-fg-muted">
            {role === 'child' ? 'Variant comparison' : 'Forked variants'}
          </h3>
          <span className="font-mono text-[10px] text-fg-faint">
            {visible.length}
            {siblings.length > MAX_VISIBLE && ` / ${siblings.length}`}
          </span>
          {role === 'child' && (
            <span className="ml-1 inline-flex items-center gap-1 rounded-sm border border-line-soft bg-white/[0.02] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.16em] text-fg-faint">
              <Sparkles size={9} className="text-fg-faint" aria-hidden />
              <span>delta metrics — soon</span>
            </span>
          )}
        </div>

        {role === 'child' && detail.parent_workspace_id && (
          <ParentBreadcrumb
            parentWorkspaceId={detail.parent_workspace_id}
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {activePin && (
          <VariantSiblingCard sibling={activePin} isActive />
        )}
        {visible.map((s) => (
          <VariantSiblingCard key={s.workspace_id} sibling={s} />
        ))}
      </div>
    </section>
  );
}

function ParentBreadcrumb({
  parentWorkspaceId,
}: {
  parentWorkspaceId: string;
}) {
  // V1 — disabled affordance.  The follow-up wires a parent-slug
  // lookup endpoint so this button navigates back to the parent.
  return (
    <button
      type="button"
      disabled
      title="Parent-slug lookup arrives in the follow-up"
      className="inline-flex shrink-0 cursor-not-allowed items-center gap-1 rounded-sm border border-line-soft bg-white/[0.012] px-2 py-0.5 text-[10.5px] text-fg-muted opacity-70"
    >
      <ChevronUp size={10} strokeWidth={1.75} />
      <span className="font-mono">
        parent · {parentWorkspaceId.slice(0, 8)}
      </span>
    </button>
  );
}
