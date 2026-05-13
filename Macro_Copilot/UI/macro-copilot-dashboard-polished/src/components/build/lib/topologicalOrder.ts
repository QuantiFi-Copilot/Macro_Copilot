// ============================================================================
// topologicalOrder.ts — Kahn topo-sort over a workspace's persisted edges.
// ----------------------------------------------------------------------------
// The substrate's ``WorkspaceDetail`` returns nodes in their stored
// order (sorted by node_id for stable JSON output).  The Build UI
// wants nodes in EXECUTION order — primitives first, then operators
// in dependency order, terminal last — so the DAG view reads
// left-to-right the way a desk analyst would describe the flow.
//
// Implementation is a plain Kahn algorithm; cycles in the persisted
// data shouldn't happen (the substrate validator rejects cyclic
// workflows pre-execution) but we tolerate them defensively by
// flushing any leftover nodes at the tail.
//
// Pure function, no React dependencies — lives in lib/ so the same
// helper can be unit-tested without a render context.
// ============================================================================

import type { EdgeSummary, NodeSummary } from '@/services/workspaceApi';

/** Return ``nodes`` re-ordered so every node appears after all its
 *  upstream dependencies.  Ties (independent siblings) keep the
 *  input ordering for determinism. */
export function topologicalOrder(
  nodes: NodeSummary[],
  edges: EdgeSummary[],
): NodeSummary[] {
  const byId = new Map<string, NodeSummary>();
  for (const n of nodes) byId.set(n.node_id, n);

  // In-degree per node + adjacency for outgoing edges.
  const indeg = new Map<string, number>();
  const adj = new Map<string, string[]>();
  for (const n of nodes) {
    indeg.set(n.node_id, 0);
    adj.set(n.node_id, []);
  }
  for (const e of edges) {
    if (!byId.has(e.from_node) || !byId.has(e.to_node)) continue;
    adj.get(e.from_node)!.push(e.to_node);
    indeg.set(e.to_node, (indeg.get(e.to_node) ?? 0) + 1);
  }

  // Queue starts with every zero-indegree node, in original-list order
  // (preserves determinism).
  const queue: string[] = [];
  for (const n of nodes) {
    if ((indeg.get(n.node_id) ?? 0) === 0) queue.push(n.node_id);
  }

  const out: NodeSummary[] = [];
  while (queue.length > 0) {
    const id = queue.shift()!;
    const node = byId.get(id);
    if (node) out.push(node);
    for (const next of adj.get(id) ?? []) {
      const d = (indeg.get(next) ?? 0) - 1;
      indeg.set(next, d);
      if (d === 0) queue.push(next);
    }
  }

  // Defensive: any node not visited (e.g. cycle) gets appended in
  // original order so the DAG view never silently drops a node.
  const seen = new Set(out.map((n) => n.node_id));
  for (const n of nodes) {
    if (!seen.has(n.node_id)) out.push(n);
  }
  return out;
}
