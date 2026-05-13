// ============================================================================
// buildDagModel.ts — pure transformation: WorkspaceDetail → DagModel.
// ----------------------------------------------------------------------------
// PR6 — the load-bearing layer that turns a persisted workspace's
// nodes + edges into a renderable graph model.  The view layer
// (``DagView``) consumes the model directly; rendering becomes a
// straightforward grid + SVG composition with no graph parsing
// inside JSX.
//
// What the model contains
// -----------------------
//   - ``nodes``:    every workspace node + computed ``rank`` (column)
//                    and ``lane`` (row) for layout.  Each carries a
//                    visual category (``input`` / ``transform`` /
//                    ``output``) so the renderer can color-code
//                    consistently with the existing palette.
//   - ``edges``:    every workspace edge resolved against the node
//                    positions, ready for SVG path emission.  Carries
//                    ``slot_name`` so the edge label is meaningful.
//   - ``ranks``:    total column count.
//   - ``lanes``:    total row count.
//   - ``warnings``: closed-vocabulary tags the view surfaces as banners
//                    when the graph data has integrity issues.
//   - ``terminalNodeIds``: every node with no outgoing edges (or the
//                    workspace's explicit ``focus_node`` when set).
//                    Renderer emphasises these visually.
//
// Layout algorithm
// ----------------
// Sugiyama-style "longest path" layering:
//
//   1. Source nodes (in-degree 0) get ``rank = 0``.
//   2. ``rank(node) = max(rank(predecessor)) + 1`` over all
//      predecessors.  Computed via Kahn topological pass.
//   3. Within each rank, lane assignment is deterministic:
//      - Sort by the average lane of incoming nodes (so a node sits
//        near the lane of its predecessors — minimises crossings).
//      - Ties broken by original-list order for determinism.
//      - A greedy "claim the lowest free lane" pass produces the
//        final lane index per node.
//
// Cycle handling
// --------------
// Substrate validator already rejects cyclic workflows pre-execution,
// but we defend against bad data anyway: any node that Kahn's algorithm
// doesn't visit (because it's part of a cycle) is appended to the
// terminal rank + tagged with the ``cycle_detected`` warning.  The
// renderer surfaces the warning + falls back to a topo-ordered linear
// strip for the cycle portion.
//
// Missing-edge fallback
// ---------------------
// If the workspace has nodes but zero edges (legacy / synthetic data),
// we emit a ``missing_edges_fallback`` warning + lay out nodes as a
// single linear chain by input order.  The view surfaces the warning
// so the user knows they're seeing a degraded representation.
//
// Pure function — no React, no async — easy to unit-test.  Adding a
// new warning kind or layout heuristic is a closed-family extension
// (add to the literal union, add to the producer).
// ============================================================================

import type {
  EdgeSummary,
  NodeSummary,
  WorkspaceDetail,
} from '@/services/workspaceApi';
import type { StageCategory } from '@/components/build/lib/buildTypes';

// ----------------------------------------------------------------------------
// Public types
// ----------------------------------------------------------------------------

/** Visual category — mirrors ``stageCategory.stageCategoryForNode`` but
 *  the model owns the assignment so the view doesn't need to re-run
 *  the classifier per render. */
export type DagNodeCategory = StageCategory | 'unknown';

/** Closed-vocabulary integrity warning surfaced by the model.  The
 *  view layer renders a banner per warning; adding a new tag is a
 *  one-line union extension here + a one-line copy entry in the
 *  view.  Stable strings so tests can assert on them. */
export type DagWarning =
  | 'cycle_detected'
  | 'missing_edges_fallback'
  | 'orphan_edges'
  | 'no_nodes';

export interface DagModelNode {
  /** Underlying node summary — passed through so the renderer can
   *  read every existing field (kind, params, artifact, etc.). */
  node: NodeSummary;
  /** Column index (0-based).  Sources are at rank 0; terminals are
   *  at ``ranks - 1`` (when the graph is connected). */
  rank: number;
  /** Row index within the rank (0-based).  Lanes are sparse — there
   *  may be gaps between assigned lanes — but the renderer can
   *  collapse via grid auto-rows. */
  lane: number;
  /** Visual category for color-coded rail / badge / arrow tone. */
  category: DagNodeCategory;
  /** True when the node has no outgoing edges OR is the workspace's
   *  explicit ``focus_node``.  Renderer emphasises with a ring +
   *  amber rail. */
  isTerminal: boolean;
  /** True when this node was part of a cycle and got placed by the
   *  cycle-fallback heuristic.  Renderer signals visually. */
  cycleFallback: boolean;
}

export interface DagModelEdge {
  /** Source node id. */
  from: string;
  /** Target node id. */
  to: string;
  /** Slot name on the target node (the "input slot" the source
   *  feeds).  Empty string when the substrate edge didn't carry a
   *  slot name (legacy / synthetic data). */
  slotName: string;
  /** Resolved positions of the endpoints — handy for the SVG
   *  renderer so it doesn't re-lookup. */
  fromRank: number;
  fromLane: number;
  toRank: number;
  toLane: number;
  /** True when the edge points BACK in rank order (only possible if
   *  the graph had a cycle that the fallback heuristic resolved
   *  defensively).  Renderer marks visually. */
  isBackEdge: boolean;
}

export interface DagModel {
  nodes: DagModelNode[];
  edges: DagModelEdge[];
  /** Total column count (``max(rank) + 1`` over non-empty models). */
  ranks: number;
  /** Total row count (``max(lane) + 1`` over non-empty models). */
  lanes: number;
  /** Closed-vocabulary integrity warnings.  Empty when the graph
   *  parsed cleanly. */
  warnings: DagWarning[];
  /** Node ids the renderer should emphasise as final outputs. */
  terminalNodeIds: string[];
}

// ----------------------------------------------------------------------------
// Entry point
// ----------------------------------------------------------------------------

export function buildDagModel(detail: WorkspaceDetail): DagModel {
  const warnings: DagWarning[] = [];
  const nodes = detail.nodes;
  if (nodes.length === 0) {
    return {
      nodes: [],
      edges: [],
      ranks: 0,
      lanes: 0,
      warnings: ['no_nodes'],
      terminalNodeIds: [],
    };
  }

  // Index nodes for fast lookup.
  const byId = new Map<string, NodeSummary>();
  for (const n of nodes) byId.set(n.node_id, n);

  // Filter orphan edges (endpoints not in the node list).  Surface
  // a warning so the user knows the topology is partial.
  const validEdges: EdgeSummary[] = [];
  let orphanCount = 0;
  for (const e of detail.edges) {
    if (byId.has(e.from_node) && byId.has(e.to_node)) {
      validEdges.push(e);
    } else {
      orphanCount += 1;
    }
  }
  if (orphanCount > 0) warnings.push('orphan_edges');

  // Missing edges → linear fallback.  The view surfaces the warning
  // explicitly so the user knows they're seeing a degraded shape.
  if (validEdges.length === 0 && nodes.length > 1) {
    warnings.push('missing_edges_fallback');
    return buildLinearFallback(nodes, detail.focus_node, warnings);
  }

  // Build the predecessor / successor adjacency.
  const predecessorsOf = new Map<string, string[]>();
  const successorsOf = new Map<string, string[]>();
  for (const n of nodes) {
    predecessorsOf.set(n.node_id, []);
    successorsOf.set(n.node_id, []);
  }
  for (const e of validEdges) {
    predecessorsOf.get(e.to_node)!.push(e.from_node);
    successorsOf.get(e.from_node)!.push(e.to_node);
  }

  // Rank assignment — longest-path layering via a Kahn pass.
  const { rankOf, visited, hasCycle } = computeRanks(
    nodes,
    predecessorsOf,
  );
  if (hasCycle) warnings.push('cycle_detected');

  // Place every unvisited (cycle-participating) node at the maximum
  // rank + 1 so the layout doesn't drop them silently.
  const baseMaxRank = Array.from(rankOf.values()).reduce(
    (acc, v) => Math.max(acc, v),
    -1,
  );
  const cycleRank = baseMaxRank + 1;
  for (const n of nodes) {
    if (!rankOf.has(n.node_id)) {
      rankOf.set(n.node_id, cycleRank);
    }
  }

  // Lane assignment — within each rank, pick lanes that minimise
  // crossings with the incoming edges.
  const laneOf = assignLanes(nodes, rankOf, predecessorsOf);

  // Terminal-node detection: explicit focus_node wins; otherwise
  // every node with no outgoing edges is a terminal.
  const focusTerminal = detail.focus_node;
  const focusTerminalSet = new Set<string>();
  if (focusTerminal && byId.has(focusTerminal)) {
    focusTerminalSet.add(focusTerminal);
  } else {
    for (const n of nodes) {
      if ((successorsOf.get(n.node_id) ?? []).length === 0) {
        focusTerminalSet.add(n.node_id);
      }
    }
  }

  // Build the DagModelNode list.
  const modelNodes: DagModelNode[] = nodes.map((n) => {
    const rank = rankOf.get(n.node_id) ?? 0;
    const lane = laneOf.get(n.node_id) ?? 0;
    const inCycle = !visited.has(n.node_id);
    return {
      node: n,
      rank,
      lane,
      category: classifyCategory(n, focusTerminalSet),
      isTerminal: focusTerminalSet.has(n.node_id),
      cycleFallback: inCycle,
    };
  });

  // Build the DagModelEdge list with resolved endpoint positions.
  const modelEdges: DagModelEdge[] = validEdges.map((e) => {
    const fromRank = rankOf.get(e.from_node) ?? 0;
    const fromLane = laneOf.get(e.from_node) ?? 0;
    const toRank = rankOf.get(e.to_node) ?? 0;
    const toLane = laneOf.get(e.to_node) ?? 0;
    return {
      from: e.from_node,
      to: e.to_node,
      slotName: e.slot_name ?? '',
      fromRank,
      fromLane,
      toRank,
      toLane,
      isBackEdge: toRank <= fromRank,
    };
  });

  const ranks = modelNodes.reduce((m, n) => Math.max(m, n.rank), 0) + 1;
  const lanes = modelNodes.reduce((m, n) => Math.max(m, n.lane), 0) + 1;

  return {
    nodes: modelNodes,
    edges: modelEdges,
    ranks,
    lanes,
    warnings,
    terminalNodeIds: Array.from(focusTerminalSet),
  };
}

// ----------------------------------------------------------------------------
// Rank assignment — longest-path layering
// ----------------------------------------------------------------------------

function computeRanks(
  nodes: NodeSummary[],
  predecessorsOf: Map<string, string[]>,
): {
  rankOf: Map<string, number>;
  visited: Set<string>;
  hasCycle: boolean;
} {
  const rankOf = new Map<string, number>();
  const visited = new Set<string>();

  // In-degree from predecessors.  Walked in input order so two
  // independent sources keep stable lanes.
  const indeg = new Map<string, number>();
  for (const n of nodes) {
    indeg.set(n.node_id, (predecessorsOf.get(n.node_id) ?? []).length);
  }

  const queue: string[] = [];
  for (const n of nodes) {
    if ((indeg.get(n.node_id) ?? 0) === 0) {
      queue.push(n.node_id);
      rankOf.set(n.node_id, 0);
    }
  }

  // Inverse adjacency for successor walks — built on the fly.
  const successorsOf = new Map<string, string[]>();
  for (const n of nodes) successorsOf.set(n.node_id, []);
  for (const [to, preds] of predecessorsOf) {
    for (const from of preds) {
      successorsOf.get(from)!.push(to);
    }
  }

  while (queue.length > 0) {
    const id = queue.shift()!;
    visited.add(id);
    const r = rankOf.get(id) ?? 0;
    for (const succ of successorsOf.get(id) ?? []) {
      // Promote rank to be at least r+1 — longest-path layering.
      const candidate = r + 1;
      const existing = rankOf.get(succ);
      if (existing === undefined || candidate > existing) {
        rankOf.set(succ, candidate);
      }
      const d = (indeg.get(succ) ?? 0) - 1;
      indeg.set(succ, d);
      if (d === 0) queue.push(succ);
    }
  }

  const hasCycle = visited.size < nodes.length;
  return { rankOf, visited, hasCycle };
}

// ----------------------------------------------------------------------------
// Lane assignment — minimise crossings (greedy)
// ----------------------------------------------------------------------------

function assignLanes(
  nodes: NodeSummary[],
  rankOf: Map<string, number>,
  predecessorsOf: Map<string, string[]>,
): Map<string, number> {
  const laneOf = new Map<string, number>();
  // Group nodes by rank, preserving input order as the tie-break.
  const byRank = new Map<number, NodeSummary[]>();
  for (const n of nodes) {
    const r = rankOf.get(n.node_id) ?? 0;
    if (!byRank.has(r)) byRank.set(r, []);
    byRank.get(r)!.push(n);
  }
  const sortedRanks = Array.from(byRank.keys()).sort((a, b) => a - b);

  for (const rank of sortedRanks) {
    const group = byRank.get(rank)!;
    // Sort group by the average lane of its predecessors — pulls
    // each node toward its parents to minimise the visible edge
    // span.  Ties broken by input-order index (set earlier in the
    // input ``nodes`` list).
    const withScore = group.map((node, idx) => {
      const preds = predecessorsOf.get(node.node_id) ?? [];
      const predLanes = preds
        .map((p) => laneOf.get(p))
        .filter((v): v is number => typeof v === 'number');
      const avgLane =
        predLanes.length > 0
          ? predLanes.reduce((a, b) => a + b, 0) / predLanes.length
          : Number.POSITIVE_INFINITY; // sources go last (sink to bottom)
      return { node, idx, avgLane };
    });
    withScore.sort((a, b) => {
      if (a.avgLane !== b.avgLane) return a.avgLane - b.avgLane;
      return a.idx - b.idx;
    });

    // Greedy lane assignment: walk the sorted group, give each node
    // the lowest free lane (>= the ceil(avgLane) when finite).
    const usedLanes = new Set<number>();
    for (const entry of withScore) {
      let candidate = Number.isFinite(entry.avgLane)
        ? Math.round(entry.avgLane)
        : 0;
      while (usedLanes.has(candidate)) candidate += 1;
      usedLanes.add(candidate);
      laneOf.set(entry.node.node_id, candidate);
    }
  }

  // Compress lanes globally so the smallest assigned lane is 0
  // (otherwise an all-rank-2-lanes pattern would render with empty
  // top rows).  Pure presentation tightening — doesn't alter
  // relative order.
  const minLane = Array.from(laneOf.values()).reduce(
    (m, v) => Math.min(m, v),
    Number.POSITIVE_INFINITY,
  );
  if (Number.isFinite(minLane) && minLane !== 0) {
    for (const [id, v] of laneOf) {
      laneOf.set(id, v - (minLane as number));
    }
  }

  return laneOf;
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function classifyCategory(
  node: NodeSummary,
  focusTerminalSet: Set<string>,
): DagNodeCategory {
  if (focusTerminalSet.has(node.node_id)) return 'output';
  if (node.kind === 'primitive') return 'input';
  if (node.kind === 'operator') return 'transform';
  return 'unknown';
}

function buildLinearFallback(
  nodes: NodeSummary[],
  focusNode: string | null,
  warnings: DagWarning[],
): DagModel {
  const modelNodes: DagModelNode[] = nodes.map((n, i) => {
    const isTerminal = focusNode
      ? n.node_id === focusNode
      : i === nodes.length - 1;
    const focusTerminalSet = new Set<string>([
      isTerminal ? n.node_id : '',
    ]);
    return {
      node: n,
      rank: i,
      lane: 0,
      category: classifyCategory(n, focusTerminalSet),
      isTerminal,
      cycleFallback: false,
    };
  });
  return {
    nodes: modelNodes,
    edges: [],
    ranks: nodes.length,
    lanes: 1,
    warnings,
    terminalNodeIds: modelNodes
      .filter((n) => n.isTerminal)
      .map((n) => n.node.node_id),
  };
}

/** Human-readable description of a DAG warning — surfaced by the
 *  view's warning banner.  Exported so tests can assert the same
 *  strings the user sees. */
export function describeWarning(w: DagWarning): string {
  switch (w) {
    case 'cycle_detected':
      return 'A cycle was detected in the persisted graph — the affected nodes are placed at the rightmost rank as a fallback.';
    case 'missing_edges_fallback':
      return 'This workspace has no persisted edges; the DAG falls back to a linear strip in input order.';
    case 'orphan_edges':
      return 'One or more edges reference unknown nodes and were skipped.';
    case 'no_nodes':
      return 'This workspace has no persisted nodes.';
  }
}
