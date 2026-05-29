// ============================================================================
// dagLayout.test.ts — Stage D (DAG tab): layered-layout assertions.
// ----------------------------------------------------------------------------
// Pins the Sugiyama layering + synthetic-query-root behaviour of layoutDag:
// parallel queries fan out from the root at rank 1; multi-step queries layer
// correctly (sources at rank 1, dependents at rank 2+); the root is rank 0;
// coordinates increase with rank; edges resolve.  Pure model → geometry, no
// UI.  Harness mirrors the other build-folder tests.
// ============================================================================

import { buildDagModel } from '../dagModel';
import { layoutDag, ROOT_ID, type LaidOutNode } from '../dagLayout';

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];
function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}
function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (actual !== expected) {
    throw new Error(
      `assertEqual failed: ${label}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`,
    );
  }
}
function assert(cond: boolean, label: string): void {
  if (!cond) throw new Error(`assert failed: ${label}`);
}

const RYL = 'get_real_yield_level_tool';
const BEI = 'calculate_breakeven_inflation_simple_tool';
const RYCS = 'calculate_real_yield_curve_spread_tool';

function ctx(
  tools: Array<{ tool: string; params?: Record<string, unknown> }>,
  edges?: Array<{ from: number; to: number; label?: string }>,
): string {
  return encodeURIComponent(
    JSON.stringify({
      tools: tools.map((t) => ({ tool: t.tool, params: t.params ?? {} })),
      tool_count: tools.length,
      ...(edges ? { edges } : {}),
    }),
  );
}

function nodeById(nodes: LaidOutNode[], id: number): LaidOutNode | undefined {
  return nodes.find((n) => n.id === id);
}

// ---------------------------------------------------------------------------
// Parallel — root fans out to all tools at rank 1.
// ---------------------------------------------------------------------------

check('parallel: root at rank 0, tools at rank 1', () => {
  const layout = layoutDag(buildDagModel(ctx([{ tool: RYL }, { tool: BEI }])));
  const root = nodeById(layout.nodes, ROOT_ID);
  assert(root != null, 'root present');
  assertEqual(root?.rank, 0, 'root rank 0');
  assertEqual(nodeById(layout.nodes, 0)?.rank, 1, 'tool0 rank 1');
  assertEqual(nodeById(layout.nodes, 1)?.rank, 1, 'tool1 rank 1');
  assertEqual(layout.ranks, 2, 'two ranks (root + tools)');
});

check('parallel: 2 root fan-out edges, 0 dependency edges', () => {
  const layout = layoutDag(buildDagModel(ctx([{ tool: RYL }, { tool: BEI }])));
  const rootEdges = layout.edges.filter((e) => e.isRootEdge);
  const depEdges = layout.edges.filter((e) => !e.isRootEdge);
  assertEqual(rootEdges.length, 2, 'two root fan-out edges');
  assertEqual(depEdges.length, 0, 'no dependency edges');
});

// ---------------------------------------------------------------------------
// Multi-step A + B → C — sources at rank 1, dependent at rank 2.
// ---------------------------------------------------------------------------

check('A + B → C: dependent lands one rank past its sources', () => {
  const layout = layoutDag(
    buildDagModel(
      ctx([{ tool: RYL }, { tool: BEI }, { tool: RYCS }], [
        { from: 0, to: 2, label: 'feeds' },
        { from: 1, to: 2, label: 'feeds' },
      ]),
    ),
  );
  assertEqual(nodeById(layout.nodes, 0)?.rank, 1, 'A rank 1');
  assertEqual(nodeById(layout.nodes, 1)?.rank, 1, 'B rank 1');
  assertEqual(nodeById(layout.nodes, 2)?.rank, 2, 'C rank 2');
  assertEqual(layout.ranks, 3, 'three ranks (root, sources, C)');
  // Root fans out only to the SOURCES (A, B) — not to C.
  const rootEdges = layout.edges.filter((e) => e.isRootEdge);
  assertEqual(rootEdges.length, 2, 'root fans out to the 2 sources only');
  const depEdges = layout.edges.filter((e) => !e.isRootEdge);
  assertEqual(depEdges.length, 2, 'two dependency edges');
  assert(
    depEdges.every((e) => e.to === 2),
    'both dependency edges point to C',
  );
});

// ---------------------------------------------------------------------------
// Linear chain A → B → C — three distinct ranks.
// ---------------------------------------------------------------------------

check('A → B → C chain: monotonically increasing ranks', () => {
  const layout = layoutDag(
    buildDagModel(
      ctx([{ tool: RYL }, { tool: BEI }, { tool: RYCS }], [
        { from: 0, to: 1 },
        { from: 1, to: 2 },
      ]),
    ),
  );
  assertEqual(nodeById(layout.nodes, 0)?.rank, 1, 'A rank 1');
  assertEqual(nodeById(layout.nodes, 1)?.rank, 2, 'B rank 2');
  assertEqual(nodeById(layout.nodes, 2)?.rank, 3, 'C rank 3');
  assertEqual(layout.ranks, 4, 'four ranks');
  // Only A is a source → exactly one root fan-out edge.
  assertEqual(
    layout.edges.filter((e) => e.isRootEdge).length,
    1,
    'one root fan-out edge (only A is a source)',
  );
});

// ---------------------------------------------------------------------------
// Coordinates + canvas.
// ---------------------------------------------------------------------------

check('x increases with rank; canvas has positive extent', () => {
  const layout = layoutDag(
    buildDagModel(
      ctx([{ tool: RYL }, { tool: BEI }], [{ from: 0, to: 1 }]),
    ),
  );
  const root = nodeById(layout.nodes, ROOT_ID)!;
  const a = nodeById(layout.nodes, 0)!;
  const b = nodeById(layout.nodes, 1)!;
  assert(root.x < a.x, 'root left of A');
  assert(a.x < b.x, 'A left of B');
  assert(layout.canvasWidth > 0, 'canvas width > 0');
  assert(layout.canvasHeight > 0, 'canvas height > 0');
});

check('root lane is centred against its fan-out targets', () => {
  // 3 parallel sources → lanes 0,1,2 → root centred near lane 1.
  const layout = layoutDag(
    buildDagModel(ctx([{ tool: RYL }, { tool: BEI }, { tool: RYCS }])),
  );
  const root = nodeById(layout.nodes, ROOT_ID)!;
  const toolLanes = layout.nodes
    .filter((n) => n.kind === 'tool')
    .map((n) => n.lane);
  const min = Math.min(...toolLanes);
  const max = Math.max(...toolLanes);
  assert(root.lane >= min && root.lane <= max, 'root lane within fan-out span');
});

// ---------------------------------------------------------------------------
// Empty.
// ---------------------------------------------------------------------------

check('empty model → empty layout', () => {
  const layout = layoutDag(buildDagModel(ctx([])));
  assertEqual(layout.nodes.length, 0, 'no nodes');
  assertEqual(layout.edges.length, 0, 'no edges');
  assertEqual(layout.canvasWidth, 0, 'no width');
});

export async function runAllDagLayoutTests(): Promise<void> {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(`\nmulti-tool DAG layout: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} multi-tool DAG layout check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllDagLayoutTests();
}
