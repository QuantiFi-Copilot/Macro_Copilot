// ============================================================================
// VariantStrip — sibling workspaces for the active variant family.
// ----------------------------------------------------------------------------
// Renders beneath the BuildHeader on completed workspaces.  Shows
// up to 6 siblings — when the active workspace was forked from a
// parent, the strip displays the parent's other children + a link
// back to the parent.  When the active workspace is the parent, the
// strip displays its direct children.
//
// Hidden when there are no siblings to show — the strip never
// renders an empty card.
// ============================================================================

import { useMemo } from 'react';
import { Boxes, ChevronUp } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { useWorkspaceSiblings } from '@/hooks/useWorkspaceSiblings';
import type { WorkspaceDetail } from '@/services/workspaceApi';
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

  const visible = useMemo(
    () => siblings.slice(0, MAX_VISIBLE),
    [siblings],
  );

  if (visible.length === 0) return null;

  return (
    <section className="flex flex-col gap-2 border-b border-line-subtle px-6 py-4">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Boxes size={11} className="text-ice-300" />
          <h3 className="text-[10.5px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
            {role === 'child' ? 'Sibling variants' : 'Forked variants'}
          </h3>
          <span className="text-[10px] text-fg-faint">
            ({visible.length} of {siblings.length})
          </span>
        </div>

        {role === 'child' && detail.parent_workspace_id && (
          <ParentBreadcrumb
            parentWorkspaceId={detail.parent_workspace_id}
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
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
  // Look up the parent itself via list_workspaces; it's a tiny
  // request but easily-cacheable since the parent rarely changes.
  // For PR B we just link back via UUID (the slug isn't on the
  // sibling listing) — PR C will resolve to a friendly title.
  const { siblings } = useWorkspaceSiblings({
    selfId: null,
    rootId: null,
  });
  // The parent isn't in the children listing; the link goes by id
  // via a synthetic route ``/workspace?parent=<id>`` would be wrong.
  // For V1 we render a static "View parent" button that the page-
  // level navigation upgrades once we add a slug lookup endpoint.
  void siblings;
  return (
    <NavLink
      to="#"
      onClick={(e) => {
        // V1 — disabled.  PR C wires a parent-slug lookup endpoint.
        e.preventDefault();
      }}
      className="inline-flex items-center gap-1 rounded-sm border border-line-soft px-2 py-0.5 text-[10.5px] text-fg-muted opacity-70"
      title="Parent slug lookup arrives in PR C"
    >
      <ChevronUp size={10} />
      <span>Parent: {parentWorkspaceId.slice(0, 8)}…</span>
    </NavLink>
  );
}
