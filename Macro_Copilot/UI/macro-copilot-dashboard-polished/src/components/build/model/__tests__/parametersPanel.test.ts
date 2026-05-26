/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// parametersPanel.test.ts — PR5 required-field validation + marshalling.
// ----------------------------------------------------------------------------
// Locks the load-bearing PR5 invariants on the schema-driven primitive
// builder's form layer:
//
//   1. ``missingRequiredFields`` flags every required input whose
//      current FormState value is empty, given the appropriate
//      control kind.  Optional fields are never flagged, even when
//      empty.
//
//   2. ``marshalForm`` continues to strip empty strings (the
//      backend distinguishes missing-key from empty-string), but
//      the panel now gates submission on
//      ``missingRequiredFields`` so the user is never silently
//      shipped a payload with omitted required fields.
//
//   3. ``isFieldFilled`` (via the public ``missingRequiredFields``
//      surface) understands the structured controls — series_spec
//      counts as filled when AT LEAST ONE inner field is set;
//      multi_tenor counts as filled when at least one non-empty
//      tenor exists; series_spec_list counts as filled when at
//      least one inner spec is non-empty.
//
// Pure-function tests — no React renderer, no DOM.  Mirrors the
// ``check`` shim style every other test file in this repo uses.
// ============================================================================

import {
  marshalForm,
  missingRequiredFields,
  type FormState,
} from '../ParametersPanel';
import {
  paramHintFor,
  inferFieldControl,
  type ControlKind,
} from '@/lib/modelRegistry';
import type { ToolCard, ToolFieldDescriptor } from '@/types/workflows';

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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function descriptor(
  name: string,
  args: Partial<ToolFieldDescriptor> = {},
): ToolFieldDescriptor {
  return {
    name,
    type: 'string',
    required: true,
    description: '',
    default: null,
    examples: null,
    ...args,
  };
}

/** Pretend tool card.  Used to drive ``paramHintFor`` so the control
 *  kind for each field matches the production inference.  The actual
 *  tool_name doesn't matter for the inferer-only path; we pick the
 *  OIS curve-spread tool because the PR5 audit specifically called
 *  out its free-text ``curve_family`` / ``*_field_name`` form
 *  controls. */
const TOOL_NAME = 'calculate_ois_curve_spread_tool';

function hintFor(name: string): ControlKind {
  return paramHintFor(TOOL_NAME, name).control;
}

// ---------------------------------------------------------------------------
// missingRequiredFields
// ---------------------------------------------------------------------------

check('missingRequiredFields: empty required scalar is flagged', () => {
  const fields: ToolFieldDescriptor[] = [
    descriptor('curve_family'),
    descriptor('short_tenor'),
    descriptor('long_tenor'),
  ];
  const form: FormState = {
    curve_family: '',
    short_tenor: '2Y',
    long_tenor: '10Y',
  };
  const out = missingRequiredFields(form, fields, hintFor);
  assertEqual(out.length, 1, 'one missing');
  assertEqual(out[0].name, 'curve_family', 'name');
  assertEqual(out[0].label, 'Curve Family', 'humanised label');
});

check('missingRequiredFields: undefined / null both count as missing', () => {
  const fields: ToolFieldDescriptor[] = [descriptor('curve_family')];
  for (const v of [undefined, null, ''] as const) {
    const form: FormState = { curve_family: v as never };
    const out = missingRequiredFields(form, fields, hintFor);
    assertEqual(out.length, 1, `${String(v)}: flagged`);
  }
});

check('missingRequiredFields: filled required scalar passes', () => {
  const fields: ToolFieldDescriptor[] = [descriptor('curve_family')];
  const form: FormState = { curve_family: 'UST' };
  const out = missingRequiredFields(form, fields, hintFor);
  assertEqual(out, [], 'no missing');
});

check('missingRequiredFields: optional fields are never flagged', () => {
  const fields: ToolFieldDescriptor[] = [
    descriptor('curve_family'),
    descriptor('lookback_days', { required: false }),
  ];
  const form: FormState = { curve_family: 'UST', lookback_days: '' };
  const out = missingRequiredFields(form, fields, hintFor);
  assertEqual(out, [], 'optional empty: still ok');
});

check('missingRequiredFields: field_name slot with empty value is flagged', () => {
  // PR5 — paramHintFor routes ``*_field_name`` to the new
  // ``field_name`` ControlKind.  The required-field check must
  // still treat an empty selection as missing.
  const fields: ToolFieldDescriptor[] = [
    descriptor('sovereign_field_name'),
    descriptor('ois_field_name', { required: false }),
  ];
  // Sanity check the inferer routes both fields to the dropdown.
  assertEqual(
    inferFieldControl('sovereign_field_name').control,
    'field_name',
    'sovereign → field_name',
  );
  assertEqual(
    inferFieldControl('ois_field_name').control,
    'field_name',
    'ois → field_name',
  );
  const form: FormState = { sovereign_field_name: '', ois_field_name: '' };
  const out = missingRequiredFields(form, fields, hintFor);
  assertEqual(out.length, 1, 'one missing');
  assertEqual(
    out[0].name,
    'sovereign_field_name',
    'required field flagged',
  );
});

check('missingRequiredFields: series_spec counts as filled when ANY inner field is set', () => {
  // PR5 — series_spec is structured.  An empty {} is missing; a
  // partial {curve_family: "UST"} counts as filled (the user has
  // started).  Matches ``marshalSeriesSpec`` which keeps any non-
  // empty inner field.
  const fields: ToolFieldDescriptor[] = [descriptor('target_spec')];
  // The OIS curve spread tool doesn't ship a series_spec — for the
  // structured-control check we use the rolling-regression tool
  // which DOES register target_spec as series_spec.
  const seriesSpecHintFor = (n: string): ControlKind =>
    paramHintFor('calculate_rolling_regression_tool', n).control;
  // Empty: missing.
  {
    const out = missingRequiredFields(
      { target_spec: {} },
      fields,
      seriesSpecHintFor,
    );
    assertEqual(out.length, 1, 'empty spec missing');
  }
  // Partial: filled.
  {
    const out = missingRequiredFields(
      { target_spec: { curve_family: 'UST' } },
      fields,
      seriesSpecHintFor,
    );
    assertEqual(out, [], 'partial spec ok');
  }
});

check('missingRequiredFields: multi_tenor empty list = missing', () => {
  const fields: ToolFieldDescriptor[] = [descriptor('tenors')];
  // PCA's ``tenors`` field is registered as multi_tenor.
  const multiHintFor = (n: string): ControlKind =>
    paramHintFor('calculate_pca_yield_curve_tool', n).control;
  // Pre-check the registration is right.
  assertEqual(multiHintFor('tenors'), 'multi_tenor', 'pca tenors hint');
  {
    const out = missingRequiredFields(
      { tenors: [] },
      fields,
      multiHintFor,
    );
    assertEqual(out.length, 1, 'empty list: missing');
  }
  {
    const out = missingRequiredFields(
      { tenors: ['', '', ''] },
      fields,
      multiHintFor,
    );
    assertEqual(out.length, 1, 'all-empty entries: missing');
  }
  {
    const out = missingRequiredFields(
      { tenors: ['', '2Y', ''] },
      fields,
      multiHintFor,
    );
    assertEqual(out, [], 'one non-empty entry: filled');
  }
});

check('missingRequiredFields: series_spec_list with all empty entries → missing', () => {
  const fields: ToolFieldDescriptor[] = [descriptor('regressor_specs')];
  // Rolling regression registers regressor_specs as series_spec_list.
  const hintForList = (n: string): ControlKind =>
    paramHintFor('calculate_rolling_regression_tool', n).control;
  assertEqual(
    hintForList('regressor_specs'),
    'series_spec_list',
    'regressor list hint',
  );
  {
    const out = missingRequiredFields(
      { regressor_specs: [] },
      fields,
      hintForList,
    );
    assertEqual(out.length, 1, 'empty list missing');
  }
  {
    const out = missingRequiredFields(
      { regressor_specs: [{}, {}] },
      fields,
      hintForList,
    );
    assertEqual(out.length, 1, 'all-empty specs missing');
  }
  {
    const out = missingRequiredFields(
      { regressor_specs: [{}, { curve_family: 'UST' }] },
      fields,
      hintForList,
    );
    assertEqual(out, [], 'one filled spec is enough');
  }
});

// ---------------------------------------------------------------------------
// marshalForm preserves PR2 wire shape after PR5
// ---------------------------------------------------------------------------

check('marshalForm: empty required strings are still stripped (Pydantic-compat)', () => {
  // Backwards-compat assertion: PR5 added the required-field gate
  // but DID NOT change the wire shape.  An empty string still drops
  // out of the payload; the panel-level gate is what stops a half-
  // filled form from reaching the network.
  const fields: ToolFieldDescriptor[] = [
    descriptor('curve_family'),
    descriptor('short_tenor'),
  ];
  const form: FormState = { curve_family: '', short_tenor: '2Y' };
  const out = marshalForm(form, fields, hintFor);
  assertEqual(out, { short_tenor: '2Y' }, 'empty stripped, filled kept');
});

check('marshalForm: filled required field round-trips', () => {
  const fields: ToolFieldDescriptor[] = [
    descriptor('curve_family'),
    descriptor('short_tenor'),
    descriptor('long_tenor'),
    descriptor('sovereign_field_name'),
  ];
  const form: FormState = {
    curve_family: 'UST',
    short_tenor: '2Y',
    long_tenor: '10Y',
    sovereign_field_name: 'YLD_YTM_MID',
  };
  const out = marshalForm(form, fields, hintFor);
  assertEqual(
    out,
    {
      curve_family: 'UST',
      short_tenor: '2Y',
      long_tenor: '10Y',
      sovereign_field_name: 'YLD_YTM_MID',
    },
    'full form round-trips',
  );
});

check('marshalForm: coercion still maps numerics + booleans', () => {
  // Backwards-compat assertion: PR5's helpers do NOT touch the
  // coercion path.
  const fields: ToolFieldDescriptor[] = [
    descriptor('lookback_days', { type: 'integer', required: false }),
    descriptor('include_flag', { type: 'boolean', required: false }),
  ];
  const form: FormState = { lookback_days: '252', include_flag: 'true' };
  const out = marshalForm(form, fields, hintFor);
  assertEqual(out.lookback_days, 252, 'integer coerced');
  assertEqual(out.include_flag, true, 'boolean coerced');
});

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

export function runAllParametersPanelTests(): void {
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
    `\nparameters-panel coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} parameters-panel check(s) failed`);
  }
}

// Note: this test file lives under ``src/components/build/model/__tests__/``
// but the test runner ALSO walks ``src/components/build/__tests__/`` —
// any directory named ``__tests__`` is picked up.  See
// ``scripts/run_build_tests.mjs``.
const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllParametersPanelTests();
}
