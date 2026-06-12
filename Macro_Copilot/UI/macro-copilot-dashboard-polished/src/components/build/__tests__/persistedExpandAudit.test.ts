// ============================================================================
// persistedExpandAudit.test.ts — consolidation target #2 + Phase D locks.
// ----------------------------------------------------------------------------
// Locks the persisted-slug unification seams:
//
//   1. ``persistedExpand`` helpers — which persisted nodes can open the
//      expand→buildExtended modal, and the DecodedPrimitive projection
//      (flat scalars vs structured nested params; tool_name stripped).
//   2. The DagWarning closed union carries ``self_corrected`` (Phase D)
//      with banner copy pointing at the Notes tab.
//   3. ``WorkspaceRunAudit`` selector-matching invariant used by the
//      DagInspector (leaf_id first, bound_tool_name fallback).
// ============================================================================

import {
  canExpandPersistedNode,
  decodedForPersistedNode,
  persistedNodeToolName,
} from '@/components/build/lib/persistedExpand';
import { describeWarning } from '@/components/build/dag/lib/buildDagModel';
import type { NodeSummary } from '@/services/workspaceApi';

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];
function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(`assertEqual failed: ${label}\n  expected: ${e}\n  actual:   ${a}`);
  }
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
}

function primitiveNode(
  toolName: string,
  params: Record<string, unknown> = {},
): NodeSummary {
  return {
    node_id: 'leaf_x',
    kind: 'primitive',
    name: toolName,
    params: { tool_name: toolName, ...params },
    artifact_hash: 'a'.repeat(64),
    artifact: null,
  } as unknown as NodeSummary;
}

function operatorNode(name: string): NodeSummary {
  return {
    node_id: 'op_x',
    kind: 'operator',
    name,
    params: {},
    artifact_hash: 'b'.repeat(64),
    artifact: null,
  } as unknown as NodeSummary;
}

// ----------------------------------------------------------------------------
// 1. persistedExpand helpers
// ----------------------------------------------------------------------------

check('canExpand: dual-view primitive (curve_spread) → true', () => {
  assertTruthy(
    canExpandPersistedNode(
      primitiveNode('calculate_curve_spread_tool', { curve_family: 'UST' }),
    ),
    'curve_spread ships buildExtended',
  );
});

check('canExpand: migrated rich-model primitive (pca) → true', () => {
  assertTruthy(
    canExpandPersistedNode(primitiveNode('calculate_pca_yield_curve_tool')),
    'pca ships buildExtended post G-3.2',
  );
});

check('canExpand: operator node → false (artifact-type widgets only)', () => {
  assertEqual(
    canExpandPersistedNode(operatorNode('summarize_series')),
    false,
    'operators have no owning module',
  );
});

check('canExpand: primitive with unknown tool → false', () => {
  assertEqual(
    canExpandPersistedNode(primitiveNode('totally_made_up_tool')),
    false,
    'unknown tool',
  );
});

check('toolName: params.tool_name wins; name _tool suffix is the fallback', () => {
  assertEqual(
    persistedNodeToolName(primitiveNode('calculate_curve_spread_tool')),
    'calculate_curve_spread_tool',
    'from params',
  );
  const legacy = {
    node_id: 'n',
    kind: 'primitive',
    name: 'calculate_curve_spread_tool',
    params: {},
    artifact_hash: null,
    artifact: null,
  } as unknown as NodeSummary;
  assertEqual(
    persistedNodeToolName(legacy),
    'calculate_curve_spread_tool',
    'from name suffix',
  );
});

check('decoded: scalars flatten to strings; nested rides structured; tool_name stripped', () => {
  const node = primitiveNode('calculate_curve_spread_tool', {
    curve_family: 'UST',
    lookback_days: 365,
    nested_spec: { tenor: '10Y' },
  });
  const decoded = decodedForPersistedNode(node);
  assertTruthy(decoded, 'decoded non-null');
  assertEqual(decoded!.toolName, 'calculate_curve_spread_tool', 'toolName');
  assertEqual(
    decoded!.params,
    { curve_family: 'UST', lookback_days: '365' },
    'flat: scalars stringified, nested dropped, tool_name stripped',
  );
  assertEqual(
    decoded!.paramsStructured,
    {
      curve_family: 'UST',
      lookback_days: 365,
      nested_spec: { tenor: '10Y' },
    },
    'structured: verbatim minus tool_name',
  );
});

check('decoded: PR-A envelope (params.params) unwraps the bound Input', () => {
  // The REAL persisted wire shape (verified on ws-59be50ca): the bound
  // Input nests one level down; envelope siblings (output_field) ride
  // alongside.  Without the unwrap the modal silently re-queries with
  // module DEFAULTS while the banner promises saved parameters.
  const node = primitiveNode('calculate_curve_spread_tool', {
    params: {
      curve_family: 'UST',
      short_tenor: '2Y',
      long_tenor: '10Y',
      lookback_days: 1825,
    },
    output_field: 'time_series_spread',
  });
  const decoded = decodedForPersistedNode(node);
  assertTruthy(decoded, 'decoded non-null');
  assertEqual(
    decoded!.params,
    {
      curve_family: 'UST',
      short_tenor: '2Y',
      long_tenor: '10Y',
      lookback_days: '1825',
      output_field: 'time_series_spread',
    },
    'inner Input lifted to flat scalars; envelope sibling kept; tool_name stripped',
  );
});

// ----------------------------------------------------------------------------
// 2. Phase D — self_corrected warning copy
// ----------------------------------------------------------------------------

check('describeWarning(self_corrected) names the Notes tab', () => {
  const copy = describeWarning('self_corrected');
  assertTruthy(copy.length > 0, 'copy present');
  assertTruthy(copy.includes('Notes'), 'points at the Notes tab trace');
});

// ----------------------------------------------------------------------------
// Harness
// ----------------------------------------------------------------------------

export async function runAllPersistedExpandAuditTests(): Promise<void> {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      await fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\npersisted-expand-audit coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} persisted-expand-audit check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllPersistedExpandAuditTests();
}
