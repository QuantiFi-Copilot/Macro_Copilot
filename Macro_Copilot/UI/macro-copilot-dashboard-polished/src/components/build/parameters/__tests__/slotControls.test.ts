/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// slotControls.test.ts — PR5 slot-derivation + slot-to-stage mapping tests.
// ----------------------------------------------------------------------------
// Covers the load-bearing PR5 invariants:
//
//   1. ``deriveSlotControls`` derives editable descriptors from
//      ``slot_schema`` × ``bound_slot_values`` — NOT from node params.
//   2. Dict slots expand one level so each inner field gets its own
//      ``[slot, field]`` path (the canonical ``slot_dict_overrides``
//      shape).
//   3. List slots become read-only (no per-index override flavor on
//      the backend today).
//   4. Bound values not declared in the schema appear as read-only
//      so they're visible but not editable through the slot-fork
//      path.
//   5. ``findStagesForSlot`` finds nodes via exact + value + name
//      heuristics, skips noisy booleans / tiny ints, and prefers
//      strongest match per node.
//   6. ``overridesToServerPatch`` produces the right
//      ``{slot_overrides, slot_dict_overrides}`` shape from a mixed
//      override map (the PR4 wiring still works after PR5).
// ============================================================================

import {
  deriveSlotControls,
  deriveSlotControlsFromCard,
  isOutputFieldSlot,
  isToolNameSlot,
  pairedToolSlotFor,
} from '../lib/deriveSlotControls';
import { findStagesForSlot } from '../lib/findStagesForSlot';
import {
  hasPendingOverrides,
  overridesReducer,
  overridesToServerPatch,
} from '../lib/overridesState';
import {
  isProposedOverrideValid,
  validateOverrides,
} from '../lib/validateOverrides';
import type {
  ParamControlDescriptor,
  ParamOverride,
} from '../lib/controlSchema';
import type {
  SlotDeclaration,
  ToolCard,
  WorkflowTemplateCard,
} from '@/types/workflows';
import type { NodeSummary } from '@/services/workspaceApi';

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

// ----------------------------------------------------------------------------
// Fixtures
// ----------------------------------------------------------------------------

function slotSchema(): SlotDeclaration[] {
  return [
    {
      name: 'curve_family',
      type: 'str',
      required: true,
      description: 'Curve family identifier.',
    },
    {
      name: 'tenor',
      type: 'str',
      required: true,
      description: 'Tenor point.',
    },
    {
      name: 'lookback_days',
      type: 'int',
      required: false,
      description: 'Displayed history.',
      default: 365,
    },
    {
      name: 'signal_params',
      type: 'dict',
      required: true,
      description: 'Nested signal config.',
    },
    {
      name: 'target_tenors',
      type: 'list',
      required: false,
      description: 'Tenors to include.',
    },
  ];
}

function boundValues(): Record<string, unknown> {
  return {
    curve_family: 'UST',
    tenor: '10Y',
    lookback_days: 504,
    signal_params: {
      window_days: 126,
      threshold: 1.5,
      method: 'rolling_zscore',
    },
    target_tenors: ['2Y', '5Y', '10Y'],
    // Persisted-but-undeclared field — appears as read-only.
    legacy_overflow: 'still here from a previous schema',
  };
}

function templateCard(): WorkflowTemplateCard {
  return {
    template_id: 'event_study',
    archetype: 'event_study',
    description: 'unit test',
    slot_schema: slotSchema(),
    terminal_artifact_type: 'WindowedPanel',
    primitives_used: ['get_yield_levels_tool'],
    operators_used: ['threshold_events', 'event_windows'],
    node_count: 4,
    edge_count: 3,
    archetype_signature: [],
  };
}

function workspaceNodes(): NodeSummary[] {
  return [
    {
      node_id: 'src',
      kind: 'PrimitiveNode',
      name: 'Yield levels',
      params: {
        tool_name: 'get_yield_levels_tool',
        output_field: 'time_series',
        params: { curve_family: 'UST', tenor: '10Y', lookback_days: 504 },
      },
      artifact_hash: 'a'.repeat(64),
      artifact: null,
    },
    {
      node_id: 'rolling_z',
      kind: 'OperatorNode',
      name: 'Rolling z-score',
      params: {
        operator_name: 'rolling_zscore',
        params: { window_days: 126, min_periods: 60 },
      },
      artifact_hash: 'b'.repeat(64),
      artifact: null,
    },
    {
      node_id: 'thresholds',
      kind: 'OperatorNode',
      name: 'Threshold events',
      params: {
        operator_name: 'threshold_events',
        params: { threshold: 1.5, direction: 'two_sided' },
      },
      artifact_hash: 'c'.repeat(64),
      artifact: null,
    },
    {
      node_id: 'event_win',
      kind: 'OperatorNode',
      name: 'Event windows',
      params: {
        operator_name: 'event_windows',
        params: { offsets: [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5] },
      },
      artifact_hash: 'd'.repeat(64),
      artifact: null,
    },
  ];
}

// ----------------------------------------------------------------------------
// deriveSlotControls
// ----------------------------------------------------------------------------

check('deriveSlotControls: scalar slot → single-segment path', () => {
  const out = deriveSlotControls({
    boundSlotValues: { curve_family: 'UST' },
    slotSchema: [
      {
        name: 'curve_family',
        type: 'str',
        required: true,
        description: '',
      },
    ],
  });
  assertEqual(out.length, 1, '1 descriptor');
  assertEqual(out[0].path, ['curve_family'], 'single-segment path');
  assertEqual(out[0].meta.kind, 'curve_family', 'curve_family control kind');
  assertEqual(out[0].currentValue, 'UST', 'current value');
  assertEqual(out[0].readOnly ?? false, false, 'editable');
});

check('deriveSlotControls: dict slot expands one level → [slot, field] paths', () => {
  const out = deriveSlotControls({
    boundSlotValues: {
      signal_params: { window_days: 126, threshold: 1.5 },
    },
    slotSchema: [
      {
        name: 'signal_params',
        type: 'dict',
        required: true,
        description: '',
      },
    ],
  });
  // 1 header (hidden) + 2 inner descriptors
  const visible = out.filter((d) => !d.hidden);
  assertEqual(visible.length, 2, 'two inner descriptors');
  assertEqual(visible[0].path, ['signal_params', 'window_days'], 'window path');
  assertEqual(visible[0].meta.kind, 'window_days', 'window control');
  assertEqual(visible[1].path, ['signal_params', 'threshold'], 'threshold path');
  assertEqual(visible[1].meta.kind, 'threshold', 'threshold control');
});

check('deriveSlotControls: list slot → readonly_json', () => {
  const out = deriveSlotControls({
    boundSlotValues: { target_tenors: ['2Y', '5Y'] },
    slotSchema: [
      {
        name: 'target_tenors',
        type: 'list',
        required: false,
        description: '',
      },
    ],
  });
  assertEqual(out.length, 1, '1 descriptor');
  assertEqual(out[0].meta.kind, 'readonly_json', 'list → readonly_json');
  assertEqual(out[0].readOnly, true, 'read-only');
});

check('deriveSlotControls: undeclared bound values appear as read-only', () => {
  const out = deriveSlotControls({
    boundSlotValues: {
      curve_family: 'UST',
      legacy_overflow: 'should be read-only',
    },
    slotSchema: [
      {
        name: 'curve_family',
        type: 'str',
        required: true,
        description: '',
      },
    ],
  });
  assertEqual(out.length, 2, '2 descriptors');
  const overflow = out.find((d) => d.path[0] === 'legacy_overflow');
  assertTruthy(overflow, 'overflow descriptor exists');
  assertEqual(overflow!.readOnly, true, 'overflow read-only');
});

check('deriveSlotControls: empty schema falls through to bound-values walk', () => {
  const out = deriveSlotControls({
    boundSlotValues: { curve_family: 'UST', window: 126 },
    slotSchema: null,
  });
  assertEqual(out.length, 2, 'both bound values walked');
  // Name-pattern heuristic still applies in the fall-through.
  const cf = out.find((d) => d.path[0] === 'curve_family');
  assertEqual(cf!.meta.kind, 'curve_family', 'cf inferred');
});

check('deriveSlotControlsFromCard: convenience wrapper', () => {
  const out = deriveSlotControlsFromCard({
    boundSlotValues: { curve_family: 'UST' },
    card: templateCard(),
  });
  // 5 slots declared; +1 read-only overflow if present — but our
  // boundValues only has curve_family here, so the unused slots
  // appear with undefined currentValue.
  const cf = out.find((d) => d.path[0] === 'curve_family' && d.path.length === 1);
  assertTruthy(cf, 'curve_family descriptor present');
});

check('deriveSlotControls: full event-study fixture round-trips', () => {
  const out = deriveSlotControlsFromCard({
    boundSlotValues: boundValues(),
    card: templateCard(),
  });
  const editable = out.filter((d) => !d.hidden && !d.readOnly);
  // Editable: curve_family + tenor + lookback_days + signal_params.window_days
  // + signal_params.threshold + signal_params.method (= 6)
  assertEqual(editable.length, 6, 'six editable descriptors');
  const paths = editable.map((d) => d.path.join('.'));
  assertTruthy(paths.includes('curve_family'), 'has curve_family');
  assertTruthy(
    paths.includes('signal_params.window_days'),
    'has signal_params.window_days',
  );
  assertTruthy(
    paths.includes('signal_params.threshold'),
    'has signal_params.threshold',
  );
  // List slot + legacy overflow → read-only.
  const readOnly = out.filter((d) => d.readOnly);
  assertTruthy(readOnly.length >= 2, 'list + overflow read-only');
});

// ----------------------------------------------------------------------------
// findStagesForSlot
// ----------------------------------------------------------------------------

check('findStagesForSlot: exact key + value match wins', () => {
  const out = findStagesForSlot({
    path: ['signal_params', 'window_days'],
    boundValue: 126,
    nodes: workspaceNodes(),
  });
  assertEqual(out.length, 1, '1 match');
  assertEqual(out[0].nodeId, 'rolling_z', 'rolling_zscore');
  assertEqual(out[0].strength, 'exact', 'exact strength');
});

check('findStagesForSlot: tenor value match → src node', () => {
  const out = findStagesForSlot({
    path: ['tenor'],
    boundValue: '10Y',
    nodes: workspaceNodes(),
  });
  assertEqual(out.length, 1, '1 match');
  assertEqual(out[0].nodeId, 'src', 'yield levels');
  assertEqual(out[0].strength, 'exact', 'exact (key match too)');
});

check('findStagesForSlot: threshold value match → thresholds node', () => {
  const out = findStagesForSlot({
    path: ['signal_params', 'threshold'],
    boundValue: 1.5,
    nodes: workspaceNodes(),
  });
  assertEqual(out.length, 1, '1 match');
  assertEqual(out[0].nodeId, 'thresholds', 'threshold_events node');
});

check('findStagesForSlot: noisy boolean values are skipped', () => {
  // A slot value of ``true`` should NOT match every node that
  // happens to have a true-valued param.  Only exact (key match)
  // counts.
  const nodes: NodeSummary[] = [
    {
      node_id: 'n1',
      kind: 'OperatorNode',
      name: 'Node 1',
      params: { params: { use_min_periods: true } },
      artifact_hash: 'a'.repeat(64),
      artifact: null,
    },
    {
      node_id: 'n2',
      kind: 'OperatorNode',
      name: 'Node 2',
      params: { params: { strict_validation: true } },
      artifact_hash: 'b'.repeat(64),
      artifact: null,
    },
  ];
  // Path leaf 'enable_thing' doesn't match either key by name.
  const out = findStagesForSlot({
    path: ['enable_thing'],
    boundValue: true,
    nodes,
  });
  assertEqual(out, [], 'no false matches for noisy boolean value');
});

check('findStagesForSlot: empty nodes → empty result', () => {
  const out = findStagesForSlot({
    path: ['curve_family'],
    boundValue: 'UST',
    nodes: [],
  });
  assertEqual(out, [], 'no matches');
});

check('findStagesForSlot: small integer values do not match by value alone', () => {
  // A slot value of 1 should NOT collide with every "1" elsewhere.
  const nodes: NodeSummary[] = [
    {
      node_id: 'n1',
      kind: 'OperatorNode',
      name: 'Node 1',
      params: { params: { min_periods: 1 } },
      artifact_hash: 'a'.repeat(64),
      artifact: null,
    },
  ];
  const out = findStagesForSlot({
    path: ['some_slot_no_match_key'],
    boundValue: 1,
    nodes,
  });
  assertEqual(out, [], 'no false match for tiny value');
});

check('findStagesForSlot: name-only fallback when value differs', () => {
  // Same key name but different value → 'name' strength match.
  const nodes: NodeSummary[] = [
    {
      node_id: 'n1',
      kind: 'OperatorNode',
      name: 'Node 1',
      params: { params: { window_days: 60 } },
      artifact_hash: 'a'.repeat(64),
      artifact: null,
    },
  ];
  const out = findStagesForSlot({
    path: ['window_days'],
    boundValue: 126,
    nodes,
  });
  assertEqual(out.length, 1, '1 match');
  assertEqual(out[0].strength, 'name', 'name-only match');
});

check('findStagesForSlot: dedupes same node across multiple weak matches', () => {
  // One node has BOTH a key match and a value match for the slot.
  // Should produce one entry with 'exact' strength.
  const nodes: NodeSummary[] = [
    {
      node_id: 'n1',
      kind: 'OperatorNode',
      name: 'Node 1',
      params: {
        params: {
          window_days: 126,
          other_thing: 126, // collides on value
        },
      },
      artifact_hash: 'a'.repeat(64),
      artifact: null,
    },
  ];
  const out = findStagesForSlot({
    path: ['window_days'],
    boundValue: 126,
    nodes,
  });
  assertEqual(out.length, 1, '1 match per node');
  assertEqual(out[0].strength, 'exact', 'strongest wins');
});

// ----------------------------------------------------------------------------
// overridesToServerPatch — PR4 wiring still works
// ----------------------------------------------------------------------------

check('overridesToServerPatch: scalar slot override', () => {
  const descriptor: ParamControlDescriptor = {
    path: ['curve_family'],
    label: 'Curve Family',
    meta: { kind: 'curve_family', allowedDomains: ['sovereign'] },
    currentValue: 'UST',
  };
  let state = overridesReducer({}, {
    type: 'set',
    descriptor,
    value: 'DE_BUND',
  });
  assertEqual(hasPendingOverrides(state), true, 'pending after set');
  const patch = overridesToServerPatch(state);
  assertEqual(patch.slot_overrides, { curve_family: 'DE_BUND' }, 'scalar slot');
  assertEqual(patch.slot_dict_overrides, {}, 'no dict override');
});

check('overridesToServerPatch: dict-field override', () => {
  const descriptor: ParamControlDescriptor = {
    path: ['signal_params', 'window_days'],
    label: 'Window Days',
    meta: { kind: 'window_days', min: 20, max: 1260 },
    currentValue: 126,
  };
  const state = overridesReducer({}, {
    type: 'set',
    descriptor,
    value: 252,
  });
  const patch = overridesToServerPatch(state);
  assertEqual(patch.slot_overrides, {}, 'no scalar override');
  assertEqual(
    patch.slot_dict_overrides,
    { signal_params: { window_days: 252 } },
    'dict patch',
  );
});

check('overridesToServerPatch: mixed scalar + dict overrides', () => {
  const cfDescriptor: ParamControlDescriptor = {
    path: ['curve_family'],
    label: 'Curve Family',
    meta: { kind: 'curve_family', allowedDomains: ['sovereign'] },
    currentValue: 'UST',
  };
  const winDescriptor: ParamControlDescriptor = {
    path: ['signal_params', 'window_days'],
    label: 'Window Days',
    meta: { kind: 'window_days', min: 20, max: 1260 },
    currentValue: 126,
  };
  const thrDescriptor: ParamControlDescriptor = {
    path: ['signal_params', 'threshold'],
    label: 'Threshold',
    meta: { kind: 'threshold', min: 0, max: 5, step: 0.1 },
    currentValue: 1.5,
  };
  let state: Record<string, ParamOverride> = {};
  state = overridesReducer(state, {
    type: 'set',
    descriptor: cfDescriptor,
    value: 'DE_BUND',
  });
  state = overridesReducer(state, {
    type: 'set',
    descriptor: winDescriptor,
    value: 252,
  });
  state = overridesReducer(state, {
    type: 'set',
    descriptor: thrDescriptor,
    value: 2.0,
  });
  const patch = overridesToServerPatch(state);
  assertEqual(patch.slot_overrides, { curve_family: 'DE_BUND' }, 'scalar');
  assertEqual(
    patch.slot_dict_overrides,
    { signal_params: { window_days: 252, threshold: 2.0 } },
    'dict merged',
  );
});

check('overridesToServerPatch: setting back to original clears the override', () => {
  const descriptor: ParamControlDescriptor = {
    path: ['curve_family'],
    label: 'Curve Family',
    meta: { kind: 'curve_family', allowedDomains: ['sovereign'] },
    currentValue: 'UST',
  };
  let state = overridesReducer({}, {
    type: 'set',
    descriptor,
    value: 'DE_BUND',
  });
  assertEqual(hasPendingOverrides(state), true, 'pending');
  state = overridesReducer(state, {
    type: 'set',
    descriptor,
    value: 'UST',
  });
  assertEqual(hasPendingOverrides(state), false, 'cleared back to original');
  assertEqual(
    overridesToServerPatch(state),
    { slot_overrides: {}, slot_dict_overrides: {} },
    'empty patch',
  );
});

// ----------------------------------------------------------------------------
// PR3 — tool_name / output_field slot detection helpers
// ----------------------------------------------------------------------------

check('isToolNameSlot: matches bare and prefixed tool_name slots', () => {
  assertEqual(isToolNameSlot('tool_name'), true, 'bare');
  assertEqual(isToolNameSlot('signal_tool_name'), true, 'prefixed');
  assertEqual(isToolNameSlot('TARGET_TOOL_NAME'), true, 'caps');
  assertEqual(isToolNameSlot('tool_name_suffix'), false, 'suffix mismatch');
  assertEqual(isToolNameSlot('curve_family'), false, 'unrelated slot');
});

check('isOutputFieldSlot: matches bare and prefixed output_field slots', () => {
  assertEqual(isOutputFieldSlot('output_field'), true, 'bare');
  assertEqual(isOutputFieldSlot('signal_output_field'), true, 'prefixed');
  assertEqual(isOutputFieldSlot('target_OUTPUT_FIELD'), true, 'caps');
  assertEqual(isOutputFieldSlot('field_name'), false, 'bloomberg field');
  assertEqual(isOutputFieldSlot('output_field_extra'), false, 'suffix mismatch');
});

check('pairedToolSlotFor: derives sibling tool_name for output_field', () => {
  assertEqual(pairedToolSlotFor('output_field'), 'tool_name', 'bare');
  assertEqual(
    pairedToolSlotFor('signal_output_field'),
    'signal_tool_name',
    'signal pair',
  );
  assertEqual(
    pairedToolSlotFor('target_output_field'),
    'target_tool_name',
    'target pair',
  );
  assertEqual(pairedToolSlotFor('curve_family'), null, 'unrelated');
});

// ----------------------------------------------------------------------------
// PR3 — deriveSlotControls populates allowedTools / allowedFields
// ----------------------------------------------------------------------------

function toolCatalogue(): ToolCard[] {
  return [
    {
      tool_name: 'get_yield_levels_tool',
      domain: 'rates',
      description: 'Sovereign yield levels.',
      input_fields: [],
      output_fields: [
        { name: 'time_series', type: 'Series', required: true },
        { name: 'spot', type: 'float', required: true },
      ],
      methodology: {
        what_it_does: '',
        assumptions: [],
        citations: [],
        planned_extensions: [],
      },
      conventions: [],
    },
    {
      tool_name: 'get_swap_rates_tool',
      domain: 'rates',
      description: 'OIS swap rates.',
      input_fields: [],
      output_fields: [
        { name: 'swap_curve', type: 'Series', required: true },
        { name: 'forward_curve', type: 'Series', required: false },
      ],
      methodology: {
        what_it_does: '',
        assumptions: [],
        citations: [],
        planned_extensions: [],
      },
      conventions: [],
    },
  ];
}

check('deriveSlotControls: *_tool_name slot → tool_name kind with catalogue', () => {
  const out = deriveSlotControls({
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    slotSchema: [
      {
        name: 'signal_tool_name',
        type: 'str',
        required: true,
        description: '',
      },
    ],
    tools: toolCatalogue(),
  });
  assertEqual(out.length, 1, '1 descriptor');
  assertEqual(out[0].meta.kind, 'tool_name', 'tool_name kind');
  const meta = out[0].meta as Extract<typeof out[0]['meta'], { kind: 'tool_name' }>;
  assertEqual(
    meta.allowedTools,
    ['get_swap_rates_tool', 'get_yield_levels_tool'],
    'allowedTools from catalogue (sorted)',
  );
  assertEqual(out[0].readOnly ?? false, false, 'editable');
});

check('deriveSlotControls: *_tool_name without catalogue → kind set, allowedTools undefined', () => {
  const out = deriveSlotControls({
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    slotSchema: [
      {
        name: 'signal_tool_name',
        type: 'str',
        required: true,
        description: '',
      },
    ],
    // No tools — catalogue not loaded.
  });
  assertEqual(out[0].meta.kind, 'tool_name', 'still tool_name kind');
  const meta = out[0].meta as Extract<typeof out[0]['meta'], { kind: 'tool_name' }>;
  assertEqual(meta.allowedTools, undefined, 'allowedTools absent');
});

check('deriveSlotControls: *_output_field slot → output_field kind keyed by paired tool', () => {
  const out = deriveSlotControls({
    boundSlotValues: {
      signal_tool_name: 'get_yield_levels_tool',
      signal_output_field: 'time_series',
    },
    slotSchema: [
      {
        name: 'signal_tool_name',
        type: 'str',
        required: true,
        description: '',
      },
      {
        name: 'signal_output_field',
        type: 'str',
        required: true,
        description: '',
      },
    ],
    tools: toolCatalogue(),
  });
  const fieldDesc = out.find((d) => d.path[0] === 'signal_output_field');
  assertTruthy(fieldDesc, 'field descriptor present');
  assertEqual(
    fieldDesc!.meta.kind,
    'output_field',
    'output_field kind',
  );
  const meta = fieldDesc!.meta as Extract<
    NonNullable<typeof fieldDesc>['meta'],
    { kind: 'output_field' }
  >;
  assertEqual(
    meta.relatedToolSlot,
    'signal_tool_name',
    'paired tool slot',
  );
  assertEqual(
    meta.selectedTool,
    'get_yield_levels_tool',
    'selectedTool from bound',
  );
  assertEqual(
    meta.allowedFields,
    ['time_series', 'spot'],
    'fields from chosen tool',
  );
});

check('deriveSlotControls: output_field reflects effectiveToolSelections (pending override)', () => {
  // User queued a tool-name override.  The output_field control
  // should reflect the OVERRIDE, not the bound tool.
  const out = deriveSlotControls({
    boundSlotValues: {
      signal_tool_name: 'get_yield_levels_tool',
      signal_output_field: 'time_series',
    },
    slotSchema: [
      {
        name: 'signal_tool_name',
        type: 'str',
        required: true,
        description: '',
      },
      {
        name: 'signal_output_field',
        type: 'str',
        required: true,
        description: '',
      },
    ],
    tools: toolCatalogue(),
    effectiveToolSelections: { signal_tool_name: 'get_swap_rates_tool' },
  });
  const fieldDesc = out.find((d) => d.path[0] === 'signal_output_field');
  const meta = fieldDesc!.meta as Extract<
    NonNullable<typeof fieldDesc>['meta'],
    { kind: 'output_field' }
  >;
  assertEqual(
    meta.selectedTool,
    'get_swap_rates_tool',
    'override wins over bound',
  );
  assertEqual(
    meta.allowedFields,
    ['swap_curve', 'forward_curve'],
    'fields from override target',
  );
});

check('deriveSlotControls: output_field with no paired tool → relatedToolSlot null', () => {
  // Bare ``output_field`` slot with NO ``tool_name`` slot in the
  // schema.  Pairing still defaults to ``tool_name`` (the convention)
  // but there's no value bound → selectedTool: null + no allowedFields.
  const out = deriveSlotControls({
    boundSlotValues: { output_field: 'time_series' },
    slotSchema: [
      {
        name: 'output_field',
        type: 'str',
        required: true,
        description: '',
      },
    ],
    tools: toolCatalogue(),
  });
  const meta = out[0].meta as Extract<
    typeof out[0]['meta'],
    { kind: 'output_field' }
  >;
  assertEqual(meta.relatedToolSlot, 'tool_name', 'still paired');
  assertEqual(meta.selectedTool, null, 'no tool selected');
  assertEqual(meta.allowedFields, undefined, 'no fields without tool');
});

check('deriveSlotControls: numeric int slot stays numeric kind', () => {
  const out = deriveSlotControls({
    boundSlotValues: { custom_count: 7 },
    slotSchema: [
      {
        name: 'custom_count',
        type: 'int',
        required: true,
        description: '',
      },
    ],
  });
  assertEqual(out[0].meta.kind, 'numeric', 'numeric for int');
});

check('deriveSlotControls: nested dict tool_name + output_field also typed', () => {
  // Some templates nest the tool/field selector inside a config dict.
  // The dict-expansion path must apply the same classifier so inner
  // fields with ``_tool_name`` / ``_output_field`` leaf names still
  // route to the typed controls.
  const out = deriveSlotControls({
    boundSlotValues: {
      signal_params: {
        signal_tool_name: 'get_yield_levels_tool',
        signal_output_field: 'time_series',
      },
    },
    slotSchema: [
      {
        name: 'signal_params',
        type: 'dict',
        required: true,
        description: '',
      },
    ],
    tools: toolCatalogue(),
  });
  const tool = out.find(
    (d) => d.path.length === 2 && d.path[1] === 'signal_tool_name',
  );
  const field = out.find(
    (d) => d.path.length === 2 && d.path[1] === 'signal_output_field',
  );
  assertTruthy(tool, 'tool inner descriptor');
  assertTruthy(field, 'field inner descriptor');
  assertEqual(tool!.meta.kind, 'tool_name', 'inner tool_name kind');
  assertEqual(field!.meta.kind, 'output_field', 'inner output_field kind');
});

// ----------------------------------------------------------------------------
// PR3 — validateOverrides
// ----------------------------------------------------------------------------

check('validateOverrides: empty queue → ok', () => {
  const res = validateOverrides({
    overrides: {},
    boundSlotValues: {},
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, true, 'ok');
  assertEqual(res.errors, [], 'no errors');
});

check('validateOverrides: tool_name with known tool → ok', () => {
  const desc: ParamControlDescriptor = {
    path: ['signal_tool_name'],
    label: 'Signal Tool',
    meta: { kind: 'tool_name', allowedTools: ['get_yield_levels_tool'] },
    currentValue: 'get_yield_levels_tool',
  };
  const state = overridesReducer({}, {
    type: 'set',
    descriptor: desc,
    value: 'get_swap_rates_tool',
  });
  const res = validateOverrides({
    overrides: state,
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, true, 'known tool accepted');
});

check('validateOverrides: tool_name with unknown tool → unknown_tool error', () => {
  const desc: ParamControlDescriptor = {
    path: ['signal_tool_name'],
    label: 'Signal Tool',
    meta: { kind: 'tool_name', allowedTools: ['get_yield_levels_tool'] },
    currentValue: 'get_yield_levels_tool',
  };
  const state = overridesReducer({}, {
    type: 'set',
    descriptor: desc,
    value: 'made_up_tool',
  });
  const res = validateOverrides({
    overrides: state,
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, false, 'rejected');
  assertEqual(res.errors[0].reason, 'unknown_tool', 'reason');
  assertEqual(
    res.errors[0].overrideKey,
    'signal_tool_name',
    'override key',
  );
});

check('validateOverrides: output_field outside paired tool → unknown_output_field error', () => {
  const desc: ParamControlDescriptor = {
    path: ['signal_output_field'],
    label: 'Signal Output',
    meta: {
      kind: 'output_field',
      allowedFields: ['time_series', 'spot'],
      selectedTool: 'get_yield_levels_tool',
      relatedToolSlot: 'signal_tool_name',
    },
    currentValue: 'time_series',
  };
  const state = overridesReducer({}, {
    type: 'set',
    descriptor: desc,
    value: 'not_a_real_field',
  });
  const res = validateOverrides({
    overrides: state,
    boundSlotValues: {
      signal_tool_name: 'get_yield_levels_tool',
      signal_output_field: 'time_series',
    },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, false, 'rejected');
  assertEqual(
    res.errors[0].reason,
    'unknown_output_field',
    'reason',
  );
});

check('validateOverrides: changing tool + matching field together → ok', () => {
  // Pair: changing tool_name to get_swap_rates_tool AND
  // output_field to swap_curve (a valid pairing).
  const toolDesc: ParamControlDescriptor = {
    path: ['signal_tool_name'],
    label: 'Tool',
    meta: { kind: 'tool_name', allowedTools: [] },
    currentValue: 'get_yield_levels_tool',
  };
  const fieldDesc: ParamControlDescriptor = {
    path: ['signal_output_field'],
    label: 'Field',
    meta: {
      kind: 'output_field',
      allowedFields: ['time_series', 'spot'],
      selectedTool: 'get_yield_levels_tool',
      relatedToolSlot: 'signal_tool_name',
    },
    currentValue: 'time_series',
  };
  let state = overridesReducer({}, {
    type: 'set',
    descriptor: toolDesc,
    value: 'get_swap_rates_tool',
  });
  state = overridesReducer(state, {
    type: 'set',
    descriptor: fieldDesc,
    value: 'swap_curve',
  });
  const res = validateOverrides({
    overrides: state,
    boundSlotValues: {
      signal_tool_name: 'get_yield_levels_tool',
      signal_output_field: 'time_series',
    },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, true, 'effective-tool match accepted');
});

check('validateOverrides: unknown slot when schema is known → unknown_slot error', () => {
  const desc: ParamControlDescriptor = {
    path: ['imaginary_slot'],
    label: 'Imaginary',
    meta: { kind: 'string' },
    currentValue: 'x',
  };
  const state = overridesReducer({}, {
    type: 'set',
    descriptor: desc,
    value: 'y',
  });
  const res = validateOverrides({
    overrides: state,
    boundSlotValues: {},
    tools: null,
    knownSlotNames: new Set(['curve_family', 'tenor']),
  });
  assertEqual(res.ok, false, 'rejected');
  assertEqual(res.errors[0].reason, 'unknown_slot', 'reason');
});

check('validateOverrides: missing tool catalogue → skips tool checks', () => {
  // A tool_name override with NO catalogue loaded must NOT block
  // apply; we'd otherwise fail closed on a transient network blip.
  const desc: ParamControlDescriptor = {
    path: ['signal_tool_name'],
    label: 'Tool',
    meta: { kind: 'tool_name' },
    currentValue: 'get_yield_levels_tool',
  };
  const state = overridesReducer({}, {
    type: 'set',
    descriptor: desc,
    value: 'whatever',
  });
  const res = validateOverrides({
    overrides: state,
    boundSlotValues: {},
    tools: null,
  });
  assertEqual(res.ok, true, 'tool check skipped without catalogue');
});

// ----------------------------------------------------------------------------
// PR3 — isProposedOverrideValid (chip-level validator)
// ----------------------------------------------------------------------------

check('isProposedOverrideValid: valid chip → ok', () => {
  const desc: ParamControlDescriptor = {
    path: ['signal_tool_name'],
    label: 'Tool',
    meta: { kind: 'tool_name' },
    currentValue: 'get_yield_levels_tool',
  };
  const res = isProposedOverrideValid({
    descriptor: desc,
    value: 'get_swap_rates_tool',
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, true, 'valid');
});

check('isProposedOverrideValid: unknown tool chip → invalid', () => {
  const desc: ParamControlDescriptor = {
    path: ['signal_tool_name'],
    label: 'Tool',
    meta: { kind: 'tool_name' },
    currentValue: 'get_yield_levels_tool',
  };
  const res = isProposedOverrideValid({
    descriptor: desc,
    value: 'nonexistent_tool',
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, false, 'invalid');
  assertEqual(res.errors[0].reason, 'unknown_tool', 'reason');
});

check('isProposedOverrideValid: unknown slot chip → invalid', () => {
  const desc: ParamControlDescriptor = {
    path: ['made_up_slot'],
    label: 'Made Up',
    meta: { kind: 'string' },
    currentValue: '',
  };
  const res = isProposedOverrideValid({
    descriptor: desc,
    value: 'x',
    boundSlotValues: {},
    tools: null,
    knownSlotNames: new Set(['curve_family']),
  });
  assertEqual(res.ok, false, 'invalid');
  assertEqual(res.errors[0].reason, 'unknown_slot', 'reason');
});

check('isProposedOverrideValid: invalid output_field for paired tool → invalid', () => {
  const desc: ParamControlDescriptor = {
    path: ['signal_output_field'],
    label: 'Field',
    meta: {
      kind: 'output_field',
      relatedToolSlot: 'signal_tool_name',
      selectedTool: 'get_yield_levels_tool',
      allowedFields: ['time_series', 'spot'],
    },
    currentValue: 'time_series',
  };
  const res = isProposedOverrideValid({
    descriptor: desc,
    value: 'swap_curve',
    boundSlotValues: { signal_tool_name: 'get_yield_levels_tool' },
    tools: toolCatalogue(),
  });
  assertEqual(res.ok, false, 'rejected');
  assertEqual(
    res.errors[0].reason,
    'unknown_output_field',
    'reason',
  );
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllSlotControlsTests(): void {
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
  console.log(
    `\nslot-controls coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} slot-controls check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllSlotControlsTests();
}
