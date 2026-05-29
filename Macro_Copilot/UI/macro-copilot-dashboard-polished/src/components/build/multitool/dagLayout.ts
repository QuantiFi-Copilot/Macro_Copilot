// ============================================================================
// dagLayout.ts — Sugiyama-style layered layout for the multi-tool DAG graph.
// ----------------------------------------------------------------------------
// Stage D (DAG-tab redesign).  Pure, tool-agnostic geometry: turns the
// ``DagModel`` (nodes + dependency edges) into a renderable LAYERED graph —
// ranks (columns / "levels") + lanes (rows) + pixel coordinates + resolved
// edges — so ``MultiToolDagGraph`` becomes a straightforward
// absolute-positioned-nodes-over-SVG-edges composition with no graph parsing
// in the view.
//
// Algorithm (adapted from the workflow DAG at
// src/components/build/dag/lib/buildDagModel.ts):
//   1. A SYNTHETIC QUERY ROOT (id = ROOT_ID) is prepended.  It fans out to
//      every source node (a tool with no incoming dependency edge).  This
//      gives even a PARALLEL query a clean "query → [A, B, C]" shape, and a
//      multi-step query a true "query → [A, B] → C" hierarchy.
//   2. Longest-path layering via a Kahn topological pass: root → rank 0,
//      sources → rank 1, downstream → max(pred rank)+1.
//   3. Greedy lane assignment within each rank (pull toward parents to
//      minimise edge crossings), then compress lanes so the min is 0.
//   4. Pixel coordinates: x from rank, y from lane.  Root is vertically
//      centred against its fan-out targets.
//   5. Cycle defence (shouldn't occur — the supervisor emits a DAG — but we
//      never silently drop a node): unresolved nodes land at maxRank+1.
//
// No React, no fetch — trivially unit-testable.
// ============================================================================

import type { DagModel, DagNode } from './dagModel';

/** Synthetic id for the query-root node.  Real node ids are source indices
 *  (≥ 0), so -1 is a safe sentinel. */
export const ROOT_ID = -1;

export interface LaidOutNode {
  id: number;
  kind: 'root' | 'tool';
  /** Undefined for the synthetic root. */
  node?: DagNode;
  rank: number;
  lane: number;
  x: number;
  y: number;
}

export interface LaidOutEdge {
  from: number;
  to: number;
  /** Dependency label (tool→tool edges); empty for root fan-out edges. */
  label: string;
  /** True for the synthetic query-root → source fan-out edges (rendered
   *  lighter, no label); false for real tool→tool data dependencies. */
  isRootEdge: boolean;
  /** True when the edge points backwards in rank order (only via the cycle
   *  fallback) — rendered distinctly so a bad topology is visible. */
  isBackEdge: boolean;
}

export interface DagLayout {
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  /** Total column count (max rank + 1). */
  ranks: number;
  /** Total row count (max lane + 1). */
  lanes: number;
  canvasWidth: number;
  canvasHeight: number;
  /** True when the cycle fallback fired (defensive — should never happen). */
  hadCycle: boolean;
  /** Geometry constants the view + edge layer share. */
  geom: DagGeom;
}

export interface DagGeom {
  nodeW: number;
  nodeH: number;
  colGap: number;
  rowGap: number;
  padX: number;
  padTop: number;
  padBottom: number;
}

export const DEFAULT_GEOM: DagGeom = {
  nodeW: 224,
  nodeH: 96,
  colGap: 104,
  rowGap: 30,
  padX: 36,
  padTop: 52, // room for the per-level column headers
  padBottom: 28,
};

// ----------------------------------------------------------------------------
// Layout entry point
// ----------------------------------------------------------------------------

export function layoutDag(
  model: DagModel,
  geom: DagGeom = DEFAULT_GEOM,
): DagLayout {
  const toolIds = model.nodes.map((n) => n.id);

  // Empty model → empty layout.
  if (toolIds.length === 0) {
    return {
      nodes: [],
      edges: [],
      ranks: 0,
      lanes: 0,
      canvasWidth: 0,
      canvasHeight: 0,
      hadCycle: false,
      geom,
    };
  }

  // --- Adjacency over the AUGMENTED graph (root + tools). ---
  const allIds = [ROOT_ID, ...toolIds];
  const predecessorsOf = new Map<number, number[]>();
  const successorsOf = new Map<number, number[]>();
  for (const id of allIds) {
    predecessorsOf.set(id, []);
    successorsOf.set(id, []);
  }

  // Tool→tool dependency edges (only those with both endpoints present —
  // the model already filtered these, but be defensive).
  const toolIdSet = new Set(toolIds);
  const depEdges = model.edges.filter(
    (e) => toolIdSet.has(e.from) && toolIdSet.has(e.to) && e.from !== e.to,
  );
  for (const e of depEdges) {
    predecessorsOf.get(e.to)!.push(e.from);
    successorsOf.get(e.from)!.push(e.to);
  }

  // Root fans out to every SOURCE (tool with no incoming dependency edge).
  const sourceIds = toolIds.filter(
    (id) => (predecessorsOf.get(id) ?? []).length === 0,
  );
  for (const sid of sourceIds) {
    predecessorsOf.get(sid)!.push(ROOT_ID);
    successorsOf.get(ROOT_ID)!.push(sid);
  }

  // --- Rank assignment (Kahn longest-path). ---
  const { rankOf, visited } = computeRanks(allIds, predecessorsOf, successorsOf);
  const hadCycle = visited.size < allIds.length;
  const baseMaxRank = Array.from(rankOf.values()).reduce(
    (acc, v) => Math.max(acc, v),
    -1,
  );
  const cycleRank = baseMaxRank + 1;
  for (const id of allIds) {
    if (!rankOf.has(id)) rankOf.set(id, cycleRank);
  }

  // --- Lane assignment (greedy, parent-pull) over the augmented graph. ---
  const laneOf = assignLanes(allIds, rankOf, predecessorsOf);

  // Centre the root vertically against its fan-out targets so it doesn't
  // sit at the top corner.
  const rootSuccLanes = (successorsOf.get(ROOT_ID) ?? [])
    .map((id) => laneOf.get(id))
    .filter((v): v is number => typeof v === 'number');
  if (rootSuccLanes.length > 0) {
    const avg =
      rootSuccLanes.reduce((a, b) => a + b, 0) / rootSuccLanes.length;
    laneOf.set(ROOT_ID, avg);
  }

  const ranks =
    Array.from(rankOf.values()).reduce((m, v) => Math.max(m, v), 0) + 1;
  const lanes =
    Array.from(laneOf.values()).reduce((m, v) => Math.max(m, v), 0) + 1;

  // --- Pixel coordinates. ---
  const colPitch = geom.nodeW + geom.colGap;
  const rowPitch = geom.nodeH + geom.rowGap;
  const xOf = (rank: number) => geom.padX + rank * colPitch;
  const yOf = (lane: number) => geom.padTop + lane * rowPitch;

  const byId = new Map<number, DagNode>(model.nodes.map((n) => [n.id, n]));
  const laidNodes: LaidOutNode[] = allIds.map((id) => {
    const rank = rankOf.get(id) ?? 0;
    const lane = laneOf.get(id) ?? 0;
    return {
      id,
      kind: id === ROOT_ID ? 'root' : 'tool',
      node: id === ROOT_ID ? undefined : byId.get(id),
      rank,
      lane,
      x: xOf(rank),
      y: yOf(lane),
    };
  });

  // --- Edges (root fan-out + tool deps). ---
  const laidEdges: LaidOutEdge[] = [
    ...sourceIds.map((sid) => ({
      from: ROOT_ID,
      to: sid,
      label: '',
      isRootEdge: true,
      isBackEdge: false,
    })),
    ...depEdges.map((e) => ({
      from: e.from,
      to: e.to,
      label: e.label,
      isRootEdge: false,
      isBackEdge: (rankOf.get(e.to) ?? 0) <= (rankOf.get(e.from) ?? 0),
    })),
  ];

  const canvasWidth =
    geom.padX * 2 + ranks * geom.nodeW + Math.max(0, ranks - 1) * geom.colGap;
  const canvasHeight =
    geom.padTop +
    geom.padBottom +
    lanes * geom.nodeH +
    Math.max(0, lanes - 1) * geom.rowGap;

  return {
    nodes: laidNodes,
    edges: laidEdges,
    ranks,
    lanes,
    canvasWidth,
    canvasHeight,
    hadCycle,
    geom,
  };
}

// ----------------------------------------------------------------------------
// Rank assignment — longest-path layering (Kahn pass).
// ----------------------------------------------------------------------------

function computeRanks(
  ids: number[],
  predecessorsOf: Map<number, number[]>,
  successorsOf: Map<number, number[]>,
): { rankOf: Map<number, number>; visited: Set<number> } {
  const rankOf = new Map<number, number>();
  const visited = new Set<number>();
  const indeg = new Map<number, number>();
  for (const id of ids) {
    indeg.set(id, (predecessorsOf.get(id) ?? []).length);
  }

  const queue: number[] = [];
  for (const id of ids) {
    if ((indeg.get(id) ?? 0) === 0) {
      queue.push(id);
      rankOf.set(id, 0);
    }
  }

  while (queue.length > 0) {
    const id = queue.shift()!;
    visited.add(id);
    const r = rankOf.get(id) ?? 0;
    for (const succ of successorsOf.get(id) ?? []) {
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

  return { rankOf, visited };
}

// ----------------------------------------------------------------------------
// Lane assignment — greedy parent-pull, then compress to min lane 0.
// ----------------------------------------------------------------------------

function assignLanes(
  ids: number[],
  rankOf: Map<number, number>,
  predecessorsOf: Map<number, number[]>,
): Map<number, number> {
  const laneOf = new Map<number, number>();
  const byRank = new Map<number, number[]>();
  for (const id of ids) {
    const r = rankOf.get(id) ?? 0;
    if (!byRank.has(r)) byRank.set(r, []);
    byRank.get(r)!.push(id);
  }
  const sortedRanks = Array.from(byRank.keys()).sort((a, b) => a - b);

  for (const rank of sortedRanks) {
    const group = byRank.get(rank)!;
    const withScore = group.map((id, idx) => {
      const preds = predecessorsOf.get(id) ?? [];
      const predLanes = preds
        .map((p) => laneOf.get(p))
        .filter((v): v is number => typeof v === 'number');
      const avgLane =
        predLanes.length > 0
          ? predLanes.reduce((a, b) => a + b, 0) / predLanes.length
          : Number.POSITIVE_INFINITY;
      return { id, idx, avgLane };
    });
    withScore.sort((a, b) => {
      if (a.avgLane !== b.avgLane) return a.avgLane - b.avgLane;
      return a.idx - b.idx;
    });

    const usedLanes = new Set<number>();
    for (const entry of withScore) {
      let candidate = Number.isFinite(entry.avgLane)
        ? Math.round(entry.avgLane)
        : 0;
      while (usedLanes.has(candidate)) candidate += 1;
      usedLanes.add(candidate);
      laneOf.set(entry.id, candidate);
    }
  }

  // Compress so the smallest assigned lane is 0.
  const minLane = Array.from(laneOf.values()).reduce(
    (m, v) => Math.min(m, v),
    Number.POSITIVE_INFINITY,
  );
  if (Number.isFinite(minLane) && minLane !== 0) {
    for (const [id, v] of laneOf) laneOf.set(id, v - (minLane as number));
  }

  return laneOf;
}
