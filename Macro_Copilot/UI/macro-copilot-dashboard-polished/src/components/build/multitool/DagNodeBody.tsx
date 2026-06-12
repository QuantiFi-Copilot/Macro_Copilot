// ============================================================================
// DagNodeBody.tsx — Generic per-node renderer for the multi-tool DAG.
// ----------------------------------------------------------------------------
// Stage D.  THE generic dispatch point: for each DAG node, look up the owning
// module by toolName and render its ``surfaces.buildCompact`` if it ships one
// (the dual-view contract).  Tools that predate the contract (no buildCompact)
// fall back to the legacy artifact-type cards so NOTHING regresses.
//
// This is the ONLY place that knows "compact card vs legacy card" — adding a
// new dual-view tool needs ZERO edits here: it just ships buildCompact and the
// registry lookup picks it up.  No tool names are hardcoded.
//
// Per rendering_density.md §3.2 + §3.3 the compact card's expand affordance
// calls ``onExpand`` (shared infra) — here wired to open the shared
// expand-to-modal with the node's decoded + id (so the canvas can highlight
// the originating node on return).
// ============================================================================

import { getPrimitiveModule } from '@/modules';
import { MultiGenericBuilderCard } from '../primitive/MultiGenericBuilderCard';
import { MultiUnsupportedKnownCard } from '../primitive/MultiUnsupportedKnownCard';
import { useOpenExtendedView } from './expandedView';
import type { DagNode } from './dagModel';

export function DagNodeBody({
  node,
  size,
}: {
  node: DagNode;
  size: 'small' | 'medium';
}) {
  const { open } = useOpenExtendedView();

  // 1. Dual-view contract — the module ships a compact Build view.  This is
  //    the preferred path for every tool migrated to the rendering-density
  //    standard; the card fetches its own live data + handles its own
  //    loading/error states via the shared shell.
  const mod = getPrimitiveModule(node.toolName);
  const Compact = mod?.surfaces?.buildCompact;
  if (Compact) {
    return (
      <Compact
        toolName={node.toolName}
        params={node.params}
        size={size}
        onExpand={() => open(node.decoded, node.id)}
        callMeta={node.callMeta}
      />
    );
  }

  // 2. Fallback — the tool ships no buildCompact (no owning module /
  //    module without the dual-view surfaces).  Render the generic
  //    cards so the node still shows useful content.  Migrating such a
  //    tool to dual-view automatically promotes it to path (1) above.
  if (node.decoded.kind === 'generic_builder') {
    return (
      <MultiGenericBuilderCard toolName={node.toolName} params={node.params} />
    );
  }
  // unsupported_known | workflow_incompatible → honest paused / incompatible card.
  return (
    <MultiUnsupportedKnownCard toolName={node.toolName} params={node.params} />
  );
}
