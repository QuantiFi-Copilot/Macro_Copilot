/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// dagModel.test.ts — PR6 DAG normalization assertions.
// ----------------------------------------------------------------------------
// Locks the load-bearing PR6 invariants for ``buildDagModel``:
//
//   1. Simple chain produces correct rank/lane assignments.
//   2. Branching graph (two parents → one merge) renders distinct
//      lanes for the parallel branches AND a single merged target
//      at the higher rank.
//   3. Multi-input operators preserve every incoming edge with its
//      slot_name.
//   4. Missing edges → linear fallback + ``missing_edges_fallback``
//      warning.
//   5. Cycle detection → ``cycle_detected`` warning + every cycle
//      node still gets a position (no silent drop).
//   6. Orphan edges (endpoints not in node list) are skipped with
//      ``orphan_edges`` warning.
//   7. Terminal nodes use ``focus_node`` when set; otherwise every
//      no-out-edge node becomes terminal.
// ============================================================================

import {
  buildDagModel,
  describeWarning,
} from '../lib/buildDagModel';
import type { DagWarning } from '../lib/buildDagModel';
import type { WorkspaceDetail } from '@/services/workspaceApi';

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(
      `assertEqual failed: ${label}\n  expected: ${e}\n  actual:   ${a}`,
    );
  }
}

function assertTruthy(value: unknown, label: string): void {
  if (!value) throw new Error(`assertTruthy failed: ${label}`);
}

function workspace(args: {
  nodes: Array<{ id: string; kind: 'primitive' | 'operator' }>;
  edges: Array<{ from: string; to: string; slot?: string }>;
  focus?: string | null;
}): WorkspaceDetail {
  return {
    workspace_id: 'ws',
    slug: 'ws',
    name: null,
    dag_hash: 'd'.repeat(64),
    focus_node: args.focus ?? null,
    parent_workspace_id: null,
    schema_version: 1,
    created_by: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    nodes: args.nodes.map((n) => ({
      node_id: n.id,
      kind: n.kind,
      name: n.id,
      params: {},
      artifact_hash: null,
      artifact: null,
    })),
    edges: args.edges.map((e) => ({
      from_node: e.from,
      to_node: e.to,
      slot_name: e.slot ?? '',
    })),
    template_id: null,
    bound_slot_values: null,
  };
}

// ----------------------------------------------------------------------------
// 1. Simple chain
// ----------------------------------------------------------------------------

check('simple chain: A → B → C → D produces sequential ranks', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
        { id: 'C', kind: 'operator' },
        { id: 'D', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'C' },
        { from: 'C', to: 'D' },
      ],
      focus: 'D',
    }),
  );
  assertEqual(m.ranks, 4, 'four ranks');
  assertEqual(m.lanes, 1, 'one lane');
  assertEqual(m.warnings, [], 'no warnings');
  const ranks = Object.fromEntries(
    m.nodes.map((n) => [n.node.node_id, n.rank]),
  );
  assertEqual(ranks, { A: 0, B: 1, C: 2, D: 3 }, 'rank assignment');
  assertEqual(m.terminalNodeIds, ['D'], 'D is terminal');
});

// ----------------------------------------------------------------------------
// 2. Branching: A,B → C → D
// ----------------------------------------------------------------------------

check('branching: two parents → one merge produces two lanes', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'primitive' },
        { id: 'C', kind: 'operator' },
        { id: 'D', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'C', slot: 'series_1' },
        { from: 'B', to: 'C', slot: 'series_2' },
        { from: 'C', to: 'D' },
      ],
      focus: 'D',
    }),
  );
  assertEqual(m.ranks, 3, 'three ranks');
  assertTruthy(m.lanes >= 2, 'at least two lanes');
  const lanes = Object.fromEntries(
    m.nodes.map((n) => [n.node.node_id, n.lane]),
  );
  // A and B occupy different lanes; C inherits one of their lanes.
  assertTruthy(lanes.A !== lanes.B, 'A and B on different lanes');
  // C is a single merge node — exactly one lane.
  assertTruthy(typeof lanes.C === 'number', 'C placed');
  assertEqual(m.warnings, [], 'no warnings');
  // Edge preservation
  const aToC = m.edges.find((e) => e.from === 'A' && e.to === 'C');
  const bToC = m.edges.find((e) => e.from === 'B' && e.to === 'C');
  assertEqual(aToC!.slotName, 'series_1', 'A→C slot');
  assertEqual(bToC!.slotName, 'series_2', 'B→C slot');
});

// ----------------------------------------------------------------------------
// 3. Multi-input operator preserves slot names
// ----------------------------------------------------------------------------

check('multi-input operator: all incoming edges + slot names preserved', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'sig', kind: 'primitive' },
        { id: 'mask', kind: 'operator' },
        { id: 'events', kind: 'operator' },
        { id: 'win', kind: 'operator' },
      ],
      edges: [
        { from: 'sig', to: 'mask', slot: 'series' },
        { from: 'mask', to: 'events', slot: 'mask' },
        { from: 'sig', to: 'win', slot: 'series' },
        { from: 'events', to: 'win', slot: 'events' },
      ],
      focus: 'win',
    }),
  );
  const winIncoming = m.edges.filter((e) => e.to === 'win');
  assertEqual(winIncoming.length, 2, 'win has two inputs');
  const slots = winIncoming.map((e) => e.slotName).sort();
  assertEqual(slots, ['events', 'series'], 'both slot names preserved');
});

// ----------------------------------------------------------------------------
// 4. Missing-edges fallback
// ----------------------------------------------------------------------------

check('missing edges: produces linear fallback + warning', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
        { id: 'C', kind: 'operator' },
      ],
      edges: [],
      focus: null,
    }),
  );
  assertEqual(m.warnings, ['missing_edges_fallback'], 'missing_edges warning');
  assertEqual(m.ranks, 3, 'linear ranks');
  assertEqual(m.lanes, 1, 'single lane');
  const ranks = Object.fromEntries(
    m.nodes.map((n) => [n.node.node_id, n.rank]),
  );
  assertEqual(ranks, { A: 0, B: 1, C: 2 }, 'sequential ranks');
});

check('missing edges: single node → no warning needed (degenerate case)', () => {
  // One node alone — no fallback needed; the missing-edges path
  // gates on ``nodes.length > 1``.
  const m = buildDagModel(
    workspace({
      nodes: [{ id: 'only', kind: 'primitive' }],
      edges: [],
    }),
  );
  assertEqual(m.warnings, [], 'no warning for single node');
  assertEqual(m.ranks, 1, 'one rank');
  assertEqual(m.lanes, 1, 'one lane');
});

// ----------------------------------------------------------------------------
// 5. Cycle detection
// ----------------------------------------------------------------------------

check('cycle: A → B → A produces cycle_detected warning + no infinite loop', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'A' },
      ],
    }),
  );
  assertTruthy(m.warnings.includes('cycle_detected'), 'cycle warning');
  // Both nodes still placed somewhere — no silent drop.
  assertEqual(m.nodes.length, 2, 'both nodes present');
});

check('cycle: every cycle node is flagged with cycleFallback', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'operator' },
        { id: 'B', kind: 'operator' },
        { id: 'C', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'C' },
        { from: 'C', to: 'A' },
      ],
    }),
  );
  // Every node is in the cycle; rankOf is unset for all of them
  // until the post-pass.  All should be marked cycleFallback.
  for (const n of m.nodes) {
    assertEqual(n.cycleFallback, true, `${n.node.node_id} cycle flag`);
  }
});

// ----------------------------------------------------------------------------
// 6. Orphan edges
// ----------------------------------------------------------------------------

check('orphan edges: skipped + warning emitted', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'B' },
        { from: 'A', to: 'GHOST' }, // orphan
        { from: 'NONE', to: 'B' }, // orphan
      ],
    }),
  );
  assertTruthy(m.warnings.includes('orphan_edges'), 'orphan warning');
  // Only the valid A→B edge survives.
  assertEqual(m.edges.length, 1, 'orphans filtered');
  assertEqual(m.edges[0].from, 'A', 'A→B preserved');
});

// ----------------------------------------------------------------------------
// 7. Terminal-node detection
// ----------------------------------------------------------------------------

check('terminals: explicit focus_node wins', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
      ],
      edges: [{ from: 'A', to: 'B' }],
      focus: 'A', // unusual but legal — pin a non-leaf as terminal
    }),
  );
  assertEqual(m.terminalNodeIds, ['A'], 'A pinned as terminal');
  const aNode = m.nodes.find((n) => n.node.node_id === 'A');
  assertEqual(aNode!.isTerminal, true, 'A flagged terminal');
  // B is NOT terminal — focus override wins over leaf detection.
  const bNode = m.nodes.find((n) => n.node.node_id === 'B');
  assertEqual(bNode!.isTerminal, false, 'B not terminal');
});

check('terminals: when no focus_node, every leaf is terminal', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
        { id: 'C', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'B' },
        { from: 'A', to: 'C' },
      ],
      focus: null,
    }),
  );
  const terminalSet = new Set(m.terminalNodeIds);
  assertTruthy(terminalSet.has('B'), 'B is leaf');
  assertTruthy(terminalSet.has('C'), 'C is leaf');
  assertEqual(terminalSet.has('A'), false, 'A is not leaf');
});

// ----------------------------------------------------------------------------
// 8. Empty workspace
// ----------------------------------------------------------------------------

check('empty nodes → no_nodes warning + empty model', () => {
  const m = buildDagModel(
    workspace({ nodes: [], edges: [] }),
  );
  assertEqual(m.warnings, ['no_nodes'], 'no_nodes warning');
  assertEqual(m.nodes, [], 'no nodes');
  assertEqual(m.edges, [], 'no edges');
  assertEqual(m.ranks, 0, 'zero ranks');
  assertEqual(m.lanes, 0, 'zero lanes');
});

// ----------------------------------------------------------------------------
// 9. Categories
// ----------------------------------------------------------------------------

check('categories: primitives = input, operators = transform, focus = output', () => {
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'A', kind: 'primitive' },
        { id: 'B', kind: 'operator' },
        { id: 'C', kind: 'operator' },
      ],
      edges: [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'C' },
      ],
      focus: 'C',
    }),
  );
  const byId = Object.fromEntries(
    m.nodes.map((n) => [n.node.node_id, n.category]),
  );
  assertEqual(byId.A, 'input', 'A is input');
  assertEqual(byId.B, 'transform', 'B is transform');
  assertEqual(byId.C, 'output', 'C is output');
});

// ----------------------------------------------------------------------------
// 10. Event-study-shape topology (representative real workflow)
// ----------------------------------------------------------------------------

check('event study shape: signal → zscore → events → windows; signal → windows', () => {
  // Real event_study template binding:
  //   signal (primitive) → rolling_zscore (operator)
  //   rolling_zscore → threshold_events (operator)
  //   threshold_events → event_windows (operator, takes mask)
  //   signal → event_windows (operator, takes original series)
  // event_windows is the focus / terminal.
  const m = buildDagModel(
    workspace({
      nodes: [
        { id: 'signal', kind: 'primitive' },
        { id: 'zscore', kind: 'operator' },
        { id: 'events', kind: 'operator' },
        { id: 'windows', kind: 'operator' },
      ],
      edges: [
        { from: 'signal', to: 'zscore', slot: 'series' },
        { from: 'zscore', to: 'events', slot: 'series' },
        { from: 'events', to: 'windows', slot: 'mask' },
        { from: 'signal', to: 'windows', slot: 'series' },
      ],
      focus: 'windows',
    }),
  );
  // signal:0, zscore:1, events:2, windows:3 — windows is at rank 3
  // because of the LONGEST path (signal → zscore → events → windows).
  const ranks = Object.fromEntries(
    m.nodes.map((n) => [n.node.node_id, n.rank]),
  );
  assertEqual(ranks.signal, 0, 'signal rank 0');
  assertEqual(ranks.zscore, 1, 'zscore rank 1');
  assertEqual(ranks.events, 2, 'events rank 2');
  assertEqual(ranks.windows, 3, 'windows rank 3 (longest path)');
  // windows has two incoming edges.
  const wIn = m.edges.filter((e) => e.to === 'windows');
  assertEqual(wIn.length, 2, 'windows two inputs');
  // No back-edges.
  assertEqual(
    wIn.every((e) => !e.isBackEdge),
    true,
    'no back edges',
  );
});

// ----------------------------------------------------------------------------
// 11. describeWarning is total over the warning union
// ----------------------------------------------------------------------------

check('describeWarning: every closed-vocab tag has copy', () => {
  const tags: DagWarning[] = [
    'cycle_detected',
    'missing_edges_fallback',
    'orphan_edges',
    'no_nodes',
  ];
  for (const t of tags) {
    const desc = describeWarning(t);
    assertTruthy(desc.length > 0, `${t} has description`);
  }
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllDagModelTests(): void {
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
  console.log(`\ndag-model coverage: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} dag-model check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllDagModelTests();
}
