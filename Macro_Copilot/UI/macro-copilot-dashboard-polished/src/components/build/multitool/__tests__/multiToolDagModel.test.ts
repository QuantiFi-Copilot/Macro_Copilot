// ============================================================================
// multiToolDagModel.test.ts — Stage D: pure DAG-model assertions.
// ----------------------------------------------------------------------------
// Pins the tool-agnostic core of the multi-tool DAG page: archetype detection
// (parallel vs multi-step), card-density sizing, CallMeta ordinals for
// duplicate calls, edge resolution + filtering, prompt pass-through, domain
// counting, and the rendering_density §3.4 boundary cases.  No UI — just
// ``buildDagModel`` over constructed ``?context=`` payloads.
//
// Harness mirrors the other build-folder tests: ``check()`` + a single
// ``runAll*Tests`` export the runner invokes (scripts/run_build_tests.mjs).
// ============================================================================

import { buildDagModel } from '../dagModel';

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

// Three pilot tools that decode to renderable nodes (runnable primitives).
const RYL = 'get_real_yield_level_tool';
const BEI = 'calculate_breakeven_inflation_simple_tool';
const RYCS = 'calculate_real_yield_curve_spread_tool';

/** Build an encoded ``?context=`` string the way Ask / the workspace URL
 *  encodes it. */
function ctx(
  tools: Array<{
    tool: string;
    params?: Record<string, unknown>;
    domain?: string;
    status?: 'ok' | 'error';
    error?: string;
  }>,
  opts?: {
    prompt?: string;
    edges?: Array<{ from: number; to: number; label?: string }>;
  },
): string {
  return encodeURIComponent(
    JSON.stringify({
      tools: tools.map((t) => ({
        tool: t.tool,
        params: t.params ?? {},
        ...(t.domain ? { domain: t.domain } : {}),
        ...(t.status ? { status: t.status } : {}),
        ...(t.error ? { error: t.error } : {}),
      })),
      tool_count: tools.length,
      ...(opts?.prompt ? { prompt: opts.prompt } : {}),
      ...(opts?.edges ? { edges: opts.edges } : {}),
    }),
  );
}

// ---------------------------------------------------------------------------
// Decodability precondition — the pilots must produce renderable nodes.
// ---------------------------------------------------------------------------

check('pilot tools decode to renderable DAG nodes', () => {
  const m = buildDagModel(ctx([{ tool: RYL }, { tool: BEI }, { tool: RYCS }]));
  assertEqual(m.nodes.length, 3, 'three pilots → three nodes');
});

// ---------------------------------------------------------------------------
// Archetype: parallel (no edges) vs multi-step (edges).
// ---------------------------------------------------------------------------

check('no edges → parallel archetype', () => {
  const m = buildDagModel(ctx([{ tool: RYL }, { tool: BEI }]));
  assertEqual(m.archetype, 'parallel', 'archetype');
  assertEqual(m.edges.length, 0, 'no edges');
});

check('edges present → multistep archetype + resolved edge', () => {
  const m = buildDagModel(
    ctx([{ tool: RYL }, { tool: BEI }], {
      edges: [{ from: 0, to: 1, label: 'feeds breakeven' }],
    }),
  );
  assertEqual(m.archetype, 'multistep', 'archetype');
  assertEqual(m.edges.length, 1, 'one edge');
  assertEqual(m.edges[0].from, 0, 'edge.from');
  assertEqual(m.edges[0].to, 1, 'edge.to');
  assertEqual(m.edges[0].label, 'feeds breakeven', 'edge.label preserved');
});

check('edge with no label → default label', () => {
  const m = buildDagModel(
    ctx([{ tool: RYL }, { tool: BEI }], { edges: [{ from: 0, to: 1 }] }),
  );
  assertEqual(m.edges[0].label, 'feeds', 'default edge label');
});

check('edge referencing a dropped node is filtered out', () => {
  // The 2nd tool is truly-unknown → dropped; the edge to it must vanish,
  // and the archetype falls back to parallel.
  const m = buildDagModel(
    ctx([{ tool: RYL }, { tool: 'totally_unknown_tool_xyz' }], {
      edges: [{ from: 0, to: 1 }],
    }),
  );
  assertEqual(m.nodes.length, 1, 'unknown tool dropped → 1 node');
  assertEqual(m.edges.length, 0, 'edge to dropped node filtered');
  assertEqual(m.archetype, 'parallel', 'no surviving edges → parallel');
});

check('self-edge is filtered out', () => {
  const m = buildDagModel(
    ctx([{ tool: RYL }, { tool: BEI }], { edges: [{ from: 0, to: 0 }] }),
  );
  assertEqual(m.edges.length, 0, 'self-edge filtered');
});

// ---------------------------------------------------------------------------
// Card density (rendering_density.md §10): medium ≤3, small ≥4.
// ---------------------------------------------------------------------------

check('≤3 nodes → medium size', () => {
  const m = buildDagModel(ctx([{ tool: RYL }, { tool: BEI }, { tool: RYCS }]));
  assertEqual(m.size, 'medium', 'size for 3 nodes');
});

check('≥4 nodes → small size', () => {
  const m = buildDagModel(
    ctx([
      { tool: RYL, params: { curve_family: 'USD_TIPS', tenor: '5Y' } },
      { tool: RYL, params: { curve_family: 'USD_TIPS', tenor: '10Y' } },
      { tool: BEI },
      { tool: RYCS },
    ]),
  );
  assertEqual(m.nodes.length, 4, 'four nodes');
  assertEqual(m.size, 'small', 'size for 4 nodes');
});

// ---------------------------------------------------------------------------
// CallMeta — ordinals for duplicate (tool, params) signatures.
// ---------------------------------------------------------------------------

check('duplicate (tool, params) → CallMeta ordinals; unique → none', () => {
  const m = buildDagModel(
    ctx([
      { tool: RYL, params: { curve_family: 'USD_TIPS', tenor: '10Y' } },
      { tool: RYL, params: { curve_family: 'USD_TIPS', tenor: '10Y' } },
      { tool: RYL, params: { curve_family: 'GBP_LINKER', tenor: '10Y' } },
    ]),
  );
  // First two share a signature → call 1/2 and 2/2.
  assert(m.nodes[0].callMeta != null, 'node0 has callMeta');
  assert(m.nodes[1].callMeta != null, 'node1 has callMeta');
  assertEqual(m.nodes[0].callMeta?.m, 2, 'node0 m');
  assertEqual(m.nodes[0].callMeta?.n, 1, 'node0 n');
  assertEqual(m.nodes[1].callMeta?.n, 2, 'node1 n');
  // Third has a unique signature → no callMeta chip.
  assertEqual(m.nodes[2].callMeta, undefined, 'unique node has no callMeta');
});

// ---------------------------------------------------------------------------
// Prompt + domains + summary.
// ---------------------------------------------------------------------------

check('prompt passes through when present; undefined when absent', () => {
  const withPrompt = buildDagModel(
    ctx([{ tool: RYL }, { tool: BEI }], {
      prompt: 'compare TIPS 10Y real yield vs US 10Y breakeven',
    }),
  );
  assertEqual(
    withPrompt.prompt,
    'compare TIPS 10Y real yield vs US 10Y breakeven',
    'prompt preserved',
  );
  const without = buildDagModel(ctx([{ tool: RYL }, { tool: BEI }]));
  assertEqual(without.prompt, undefined, 'no prompt → undefined');
});

check('domains de-duplicated + sorted', () => {
  const m = buildDagModel(
    ctx([
      { tool: RYL, domain: 'rates' },
      { tool: BEI, domain: 'rates' },
      { tool: RYCS, domain: 'rates' },
    ]),
  );
  assertEqual(m.domains.length, 1, 'one distinct domain');
  assertEqual(m.domains[0], 'rates', 'domain value');
});

check('summary reflects node count + archetype', () => {
  const m = buildDagModel(ctx([{ tool: RYL }, { tool: BEI }]));
  assert(m.summary.includes('2 tool calls'), 'summary has count');
  assert(m.summary.includes('parallel'), 'summary has archetype');
});

// ---------------------------------------------------------------------------
// Per-node status (R2 — error inheritance surfaced on the strip).
// ---------------------------------------------------------------------------

check('source status=error surfaces on the node', () => {
  const m = buildDagModel(
    ctx([
      { tool: RYL },
      { tool: BEI, status: 'error', error: 'no data for that tenor' },
    ]),
  );
  assertEqual(m.nodes[0].status, 'ok', 'ok node');
  assertEqual(m.nodes[1].status, 'error', 'errored node');
  assertEqual(
    m.nodes[1].errorMessage,
    'no data for that tenor',
    'error message carried',
  );
});

// ---------------------------------------------------------------------------
// Boundary cases (rendering_density.md §3.4) + malformed input.
// ---------------------------------------------------------------------------

check('single node → showStrip false (degenerate)', () => {
  const m = buildDagModel(ctx([{ tool: RYL }]));
  assertEqual(m.nodes.length, 1, 'one node');
  assertEqual(m.showStrip, false, 'no strip for a single node');
});

check('≥2 nodes → showStrip true', () => {
  const m = buildDagModel(ctx([{ tool: RYL }, { tool: BEI }]));
  assertEqual(m.showStrip, true, 'strip shown for ≥2 nodes');
});

check('empty context → empty model', () => {
  const m = buildDagModel(ctx([]));
  assertEqual(m.nodes.length, 0, 'no nodes');
  assertEqual(m.showStrip, false, 'no strip');
  assertEqual(m.archetype, 'parallel', 'default archetype');
});

check('malformed context string → empty model (no throw)', () => {
  const m = buildDagModel('not-valid-json-%%%');
  assertEqual(m.nodes.length, 0, 'no nodes on malformed input');
});

check('node id == original source index (stable across drops)', () => {
  // unknown tool at index 1 is dropped; the surviving nodes keep their
  // ORIGINAL indices (0 and 2) so edges resolve correctly.
  const m = buildDagModel(
    ctx([{ tool: RYL }, { tool: 'totally_unknown_tool_xyz' }, { tool: BEI }]),
  );
  assertEqual(m.nodes.length, 2, 'unknown dropped → 2 nodes');
  assertEqual(m.nodes[0].id, 0, 'first node keeps index 0');
  assertEqual(m.nodes[1].id, 2, 'second node keeps original index 2');
});

export async function runAllMultiToolDagModelTests(): Promise<void> {
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
  console.log(`\nmulti-tool DAG model: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} multi-tool DAG model check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllMultiToolDagModelTests();
}
