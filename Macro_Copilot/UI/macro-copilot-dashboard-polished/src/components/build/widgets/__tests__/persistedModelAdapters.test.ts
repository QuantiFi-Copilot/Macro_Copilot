/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// persistedModelAdapters.test.ts — PR1 (new plan) per-tool adapter assertions.
// ----------------------------------------------------------------------------
// Locks the load-bearing PR1 invariants on the adapter layer that
// bridges persisted Series artifacts to the rich-model widget surface:
//
//   1. ``getModelAdapter`` returns a typed adapter for every registered
//      model tool name; unknown names fall back to
//      ``GENERIC_MODEL_ADAPTER``.
//   2. Tools whose backend output_class has NO time_series field
//      (attribution, half-life) get ``hasTimeSeriesOutput: false``.
//      Tools that DO emit time-series fields get
//      ``hasTimeSeriesOutput: true``.
//   3. ``adaptModelArtifact`` dispatches into the three closed
//      variants:
//        - ``persisted_series`` for time-series tools + matching
//          artifact_type
//        - ``shape_mismatch`` for time-series tools + non-matching
//          artifact_type
//        - ``pure_snapshot_unavailable`` for pure-snapshot tools
//   4. Adapter copy is non-empty and stable enough for the user-
//      visible "what's not in this saved snapshot" callout.
// ============================================================================

import {
  adaptModelArtifact,
  GENERIC_MODEL_ADAPTER,
  getModelAdapter,
  isModelTool,
  type ModelToolName,
} from '../shared/persistedModelAdapters';
import type {
  ArtifactPayloadResponse,
  PanelPayloadEnvelope,
  SeriesPayloadEnvelope,
} from '@/types/artifacts';

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

function seriesEnvelope(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'pca/factor_1',
      units: 'factor_level',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['2025-01-01', '2025-01-02'],
      values: [0.42, -0.18],
      name: 'pca/factor_1',
      type: 'Series',
    },
  };
}

function panelEnvelope(): PanelPayloadEnvelope {
  return {
    artifact_type: 'Panel',
    metadata: {
      units_by_column: { col_a: 'bps' },
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['2025-01-01'],
      columns: ['col_a'],
      data: [[4.2]],
    },
  };
}

// ----------------------------------------------------------------------------
// 1. isModelTool / getModelAdapter — closed registry
// ----------------------------------------------------------------------------

check('isModelTool: recognises every registered model tool', () => {
  const known: ModelToolName[] = [
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_half_life_tool',
    'calculate_beta_adjusted_spread_tool',
  ];
  for (const t of known) {
    assertEqual(isModelTool(t), true, t);
  }
});

check('isModelTool: rejects unknown tools', () => {
  assertEqual(isModelTool('totally_made_up_tool'), false, 'unknown');
  assertEqual(isModelTool(''), false, 'empty');
});

check('getModelAdapter: unknown tool → GENERIC_MODEL_ADAPTER (not crash)', () => {
  const a = getModelAdapter('totally_made_up_tool');
  assertEqual(a.toolName, 'generic', 'generic fallback');
});

check('getModelAdapter: each registered tool has a non-empty display name', () => {
  const tools: ModelToolName[] = [
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_half_life_tool',
    'calculate_beta_adjusted_spread_tool',
  ];
  for (const t of tools) {
    const a = getModelAdapter(t);
    assertTruthy(a.displayName.length > 0, `${t}: displayName`);
    assertTruthy(
      a.persistedRole.headline.length > 0,
      `${t}: persistedRole.headline`,
    );
    assertTruthy(
      a.persistedRole.description.length > 0,
      `${t}: persistedRole.description`,
    );
  }
});

// ----------------------------------------------------------------------------
// 2. hasTimeSeriesOutput — verified against backend output_class shape
// ----------------------------------------------------------------------------

check('hasTimeSeriesOutput: PCA has time-series output', () => {
  assertEqual(
    getModelAdapter('calculate_pca_yield_curve_tool').hasTimeSeriesOutput,
    true,
    'PCA',
  );
});

check('hasTimeSeriesOutput: rolling regression has time-series output', () => {
  assertEqual(
    getModelAdapter('calculate_rolling_regression_tool').hasTimeSeriesOutput,
    true,
    'rolling regression',
  );
});

check('hasTimeSeriesOutput: beta-adjusted spread has time-series output', () => {
  assertEqual(
    getModelAdapter('calculate_beta_adjusted_spread_tool').hasTimeSeriesOutput,
    true,
    'beta-adjusted spread',
  );
});

check('hasTimeSeriesOutput: attribution is pure-snapshot (false)', () => {
  // Verified against ``rates_agent/sovereign_bonds/tools/
  // yield_change_attribution_pca/schemas.py`` — output_class has no
  // time_series_* field.  The substrate's Series bridge would have
  // nothing to lift, so persistence is degenerate today.
  assertEqual(
    getModelAdapter('calculate_yield_change_attribution_pca_tool')
      .hasTimeSeriesOutput,
    false,
    'attribution',
  );
});

check('hasTimeSeriesOutput: half-life is pure-snapshot (false)', () => {
  // Verified against ``rates_agent/sovereign_bonds/tools/half_life/
  // schemas.py`` — output_class is current_metrics only.
  assertEqual(
    getModelAdapter('calculate_half_life_tool').hasTimeSeriesOutput,
    false,
    'half-life',
  );
});

// ----------------------------------------------------------------------------
// 3. detailUnavailable — every time-series adapter explains what's
//    NOT in the persisted Series (honest "missing detail" callout).
// ----------------------------------------------------------------------------

check('detailUnavailable: PCA names the missing rich fields', () => {
  const a = getModelAdapter('calculate_pca_yield_curve_tool');
  assertTruthy(a.detailUnavailable.length >= 3, 'PCA: ≥ 3 missing fields');
  const joined = a.detailUnavailable.join(' · ').toLowerCase();
  assertTruthy(joined.includes('loadings'), 'PCA: mentions loadings');
  assertTruthy(joined.includes('variance'), 'PCA: mentions variance');
});

check('detailUnavailable: rolling regression names the missing rich fields', () => {
  const a = getModelAdapter('calculate_rolling_regression_tool');
  assertTruthy(a.detailUnavailable.length >= 2, '≥ 2 missing fields');
  const joined = a.detailUnavailable.join(' · ').toLowerCase();
  assertTruthy(
    joined.includes('snapshot') || joined.includes('current'),
    'mentions snapshot/current fields',
  );
});

check('detailUnavailable: beta-adjusted spread names the missing rich fields', () => {
  const a = getModelAdapter('calculate_beta_adjusted_spread_tool');
  assertTruthy(a.detailUnavailable.length >= 1, '≥ 1 missing field');
});

check('builderHint: every time-series adapter points at the live Build surface', () => {
  // Consolidation G-3.2: the live destination is the dual-view Build
  // surface (the rich-model "builder" chassis is retired) — the hint
  // must still point the user somewhere actionable.
  for (const t of [
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_beta_adjusted_spread_tool',
  ] as ModelToolName[]) {
    const a = getModelAdapter(t);
    assertTruthy(a.builderHint.length > 0, `${t}: builder hint`);
    assertTruthy(
      a.builderHint.toLowerCase().includes('build'),
      `${t}: points at the Build surface`,
    );
  }
});

// ----------------------------------------------------------------------------
// 4. adaptModelArtifact — three closed variants
// ----------------------------------------------------------------------------

check('adapt: PCA + Series payload → persisted_series variant', () => {
  const view = adaptModelArtifact({
    toolName: 'calculate_pca_yield_curve_tool',
    payload: seriesEnvelope(),
  });
  assertEqual(view.kind, 'persisted_series', 'kind');
  assertEqual(view.adapter.displayName, 'PCA', 'adapter');
});

check('adapt: rolling regression + Series payload → persisted_series', () => {
  const view = adaptModelArtifact({
    toolName: 'calculate_rolling_regression_tool',
    payload: seriesEnvelope(),
  });
  assertEqual(view.kind, 'persisted_series', 'kind');
});

check('adapt: PCA + Panel payload → shape_mismatch', () => {
  const view = adaptModelArtifact({
    toolName: 'calculate_pca_yield_curve_tool',
    payload: panelEnvelope() as ArtifactPayloadResponse,
  });
  assertEqual(view.kind, 'shape_mismatch', 'kind');
  if (view.kind === 'shape_mismatch') {
    assertEqual(view.expected, 'Series', 'expected');
    assertEqual(view.got, 'Panel', 'got');
  }
});

check('adapt: attribution + any payload → pure_snapshot_unavailable', () => {
  // Attribution's adapter has hasTimeSeriesOutput=false — regardless
  // of what payload type lands, we surface the snapshot-unavailable
  // state rather than render an empty body.
  const view = adaptModelArtifact({
    toolName: 'calculate_yield_change_attribution_pca_tool',
    payload: seriesEnvelope(),
  });
  assertEqual(view.kind, 'pure_snapshot_unavailable', 'kind');
});

check('adapt: half-life + Series payload → pure_snapshot_unavailable', () => {
  const view = adaptModelArtifact({
    toolName: 'calculate_half_life_tool',
    payload: seriesEnvelope(),
  });
  assertEqual(view.kind, 'pure_snapshot_unavailable', 'kind');
});

check('adapt: unknown tool + Series payload → persisted_series with generic adapter', () => {
  const view = adaptModelArtifact({
    toolName: 'totally_made_up_tool',
    payload: seriesEnvelope(),
  });
  // Generic adapter has hasTimeSeriesOutput=true + expectedArtifactType=Series
  // so a Series payload lands on persisted_series with the generic
  // adapter wrapped in.
  assertEqual(view.kind, 'persisted_series', 'kind');
  assertEqual(view.adapter, GENERIC_MODEL_ADAPTER, 'adapter');
});

// ----------------------------------------------------------------------------
// 5. expectedArtifactType — every adapter's expectation is reachable
// ----------------------------------------------------------------------------

check('expectedArtifactType: every adapter expects a real ArtifactType', () => {
  for (const t of [
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_half_life_tool',
    'calculate_beta_adjusted_spread_tool',
  ] as ModelToolName[]) {
    const a = getModelAdapter(t);
    const known = new Set([
      'Series',
      'SeriesSet',
      'EventSet',
      'Panel',
      'WindowedPanel',
      'TradeSet',
    ]);
    assertTruthy(known.has(a.expectedArtifactType), `${t}: known type`);
  }
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllPersistedModelAdaptersTests(): void {
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
    `\npersisted-model-adapters coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(
      `${failed} persisted-model-adapters check(s) failed`,
    );
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllPersistedModelAdaptersTests();
}
