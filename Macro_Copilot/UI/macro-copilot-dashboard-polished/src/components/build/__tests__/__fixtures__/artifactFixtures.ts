// ============================================================================
// artifactFixtures.ts — PR4 centralised fixtures for the Build test suite.
// ----------------------------------------------------------------------------
// Six families of reusable shape-true samples mirroring the
// substrate's wire format.  Each helper is a pure factory:
// - it returns a freshly-cloned object on every call (no shared
//   reference between callers, so a test mutating a fixture can't
//   leak into a sibling test);
// - it documents WHICH substrate operator / tool emits the shape
//   so future readers don't have to chase the wire convention down.
//
// What this file is NOT
// ---------------------
// Not a runtime registry, not a contract test.  It's a fixture
// library shared by ``regressionLock.test.ts`` and any other test
// that needs to assert against a realistic envelope.  Adding a new
// fixture is a closed extension — define a factory, export it,
// optionally include it in one of the convenience bundles at the
// bottom.
// ============================================================================

import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import type {
  EventSetPayloadEnvelope,
  PanelPayloadEnvelope,
  SeriesPayloadEnvelope,
  SeriesSetPayloadEnvelope,
  TradeSetPayloadEnvelope,
  WindowedPanelPayloadEnvelope,
} from '@/types/artifacts';
import type {
  SlotDeclaration,
  ToolCard,
  WorkflowTemplateCard,
} from '@/types/workflows';

// ---------------------------------------------------------------------------
// 1. Persisted rich-model artifact envelopes
// ---------------------------------------------------------------------------
//
// What the substrate persists for each rich-model tool.  Every model
// tool emits one of:
//   - a Series (PCA factor scores, rolling β, beta-adjusted spread)
//   - a pure-snapshot Series (attribution decomposition, half-life)
//
// The Series body shape is the same; the metadata.series_key + the
// per-tool adapter (persistedModelAdapters.ts) is what disambiguates.
// These factories produce shape-true bodies the per-tool adapter
// can be exercised against without monkey-patching its registry.

export function pcaFactorScoreSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'PCA/UST/factor_scores/PC1',
      units: 'z_score',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2024-01-02', 252),
      values: rampValues(252, -2.0, 2.0),
      name: 'factor_scores_PC1',
      type: 'Series',
    },
  };
}

export function rollingRegressionBetaSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'RollingReg/UST_10Y_vs_2Y/beta',
      units: 'decimal',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2024-01-02', 252),
      values: rampValues(252, 0.6, 1.4),
      name: 'beta',
      type: 'Series',
    },
  };
}

export function betaAdjustedSpreadSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'BetaAdjustedSpread/UST_10Y_minus_betaCT2Y',
      units: 'bps',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2024-01-02', 252),
      values: rampValues(252, -25.0, 25.0),
      name: 'beta_adjusted_spread',
      type: 'Series',
    },
  };
}

/** ``calculate_yield_change_attribution_pca_tool`` lifts NO time-
 *  series field as the canonical artifact — every payload it
 *  persists is a degenerate "scalar snapshot" Series that the
 *  rich-widget surfaces honestly via the "pure snapshot — re-run for
 *  detail" branch.  We model that here as a 1-row Series whose
 *  index is the workflow's sentinel date. */
export function attributionPureSnapshotSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'Attribution/UST_10Y/decomposition_snapshot',
      units: 'bps',
      frequency: null,
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['1900-01-01'],
      values: [42.5],
      name: 'attribution_snapshot',
      type: 'Series',
    },
  };
}

export function halfLifePureSnapshotSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'HalfLife/UST_2s10s_spread/days',
      units: 'count',
      frequency: null,
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['1900-01-01'],
      values: [27.4],
      name: 'half_life_days',
      type: 'Series',
    },
  };
}

// ---------------------------------------------------------------------------
// 2. Event-study artifact envelopes
// ---------------------------------------------------------------------------
//
// Mirrors the persisted shapes the ``event_study`` workflow template
// emits in the canonical ``signal → events → windows → aggregate``
// chain:
//   - signal / target Series
//   - EventSet (boolean mask + event_dates)
//   - WindowedPanel (data[event_idx][offset_idx])
//   - conditional_aggregate Series carrying ``index_encoding``
//     ``{kind:"event_offset", anchor:"1970-01-01", offsets:[0..N]}``.

export function eventStudySignalSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'CPI/yoy/zscore',
      units: 'z_score',
      frequency: 'M',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2018-01-01', 60),
      values: rampValues(60, -2.5, 2.8),
      name: 'cpi_yoy_zscore',
      type: 'Series',
    },
  };
}

export function eventStudyTargetSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'UST/10Y/yield',
      units: 'percent',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2018-01-02', 1260),
      values: rampValues(1260, 1.5, 4.8),
      name: 'ust_10y_yield',
      type: 'Series',
    },
  };
}

export function eventStudyEventSet(): EventSetPayloadEnvelope {
  // 1,261 mask entries (a few years of business days), 4 distinct
  // event firings.  Mirrors the bug the PR4 audit found: pre-fix
  // event count came from ``mask_index.length`` (=1261) when the
  // correct answer is ``event_dates.length`` (=4).
  return {
    artifact_type: 'EventSet',
    metadata: {
      source_series_key: 'CPI/yoy/zscore',
      frequency: 'B',
      lineage: { steps: [] },
    },
    payload: {
      mask_index: dailyDates('2018-01-01', 1261),
      mask_values: Array.from({ length: 1261 }, (_, i) => i % 315 === 0),
      event_dates: ['2018-01-01', '2018-11-13', '2019-09-23', '2020-08-03'],
      per_event_metadata: [
        { label: 'cpi_8.6' },
        { label: 'cpi_8.5' },
        { label: 'cpi_8.3' },
        { label: 'cpi_8.0' },
      ],
    },
  };
}

export function eventStudyWindowedPanel(): WindowedPanelPayloadEnvelope {
  return {
    artifact_type: 'WindowedPanel',
    metadata: {
      offsets: [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5],
      target_series_key: 'UST/10Y/yield',
      units: 'bps',
      lineage: { steps: [] },
    },
    payload: {
      data: [
        [-2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8],
        [-3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7],
        [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5],
        [-4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6],
      ],
      event_dates: ['2018-01-01', '2018-11-13', '2019-09-23', '2020-08-03'],
      per_event_metadata: [{}, {}, {}, {}],
    },
  };
}

/** PR2 spotlight — the conditional-aggregate operator persists a
 *  Series whose index is synthetic ``_OFFSET_ANCHOR + Timedelta``
 *  dates.  The artifact-store auto-promotes ``index_encoding`` so
 *  downstream widgets can decode the offsets back into ``t+N``
 *  labels.  This fixture is the canonical "renders as event-relative
 *  axis, NOT 1970-01-NN dates" payload. */
export function conditionalAggregateSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'CondAgg/UST_10Y/forward_5d',
      units: 'bps',
      frequency: null,
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      // 1970-anchored synthetic dates — should NEVER render literally.
      index: [
        '1970-01-01',
        '1970-01-02',
        '1970-01-03',
        '1970-01-04',
        '1970-01-05',
        '1970-01-06',
      ],
      values: [0, 1.2, 2.4, 3.0, 3.4, 3.5],
      name: 'cond_agg_forward_5d',
      type: 'Series',
      index_encoding: {
        kind: 'event_offset',
        anchor: '1970-01-01',
        offsets: [0, 1, 2, 3, 4, 5],
      },
    } as SeriesPayloadEnvelope['payload'],
  };
}

// ---------------------------------------------------------------------------
// 3. Regime-relationship artifact envelopes
// ---------------------------------------------------------------------------
//
// Mirrors the ``regime_conditioned_relationship`` workflow.  The
// terminal Panel/Series shapes here include the 1900-01-01 sentinel
// rows the ``summarize_series`` operator emits when boiling a
// regime's window down to a scalar — Panels rendered against this
// shape should NOT show the sentinel date in the "as-of" footer.

export function regimeLhsSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'UST/10Y/yield_diff',
      units: 'bps',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2018-01-01', 504),
      values: rampValues(504, -8.0, 8.0),
      name: 'ust_10y_diff',
      type: 'Series',
    },
  };
}

export function regimeRhsSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'UST/2Y/yield_diff',
      units: 'bps',
      frequency: 'B',
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: dailyDates('2018-01-01', 504),
      values: rampValues(504, -6.0, 6.0),
      name: 'ust_2y_diff',
      type: 'Series',
    },
  };
}

export function regimeRelationshipSeriesSet(): SeriesSetPayloadEnvelope {
  return {
    artifact_type: 'SeriesSet',
    metadata: {
      units_by_key: {
        beta: 'decimal',
        alpha: 'bps',
        r_squared: 'decimal',
      },
      missingness_by_key: {},
      upstream_lineage_by_key: {},
      frequency: 'B',
      lineage: { steps: [] },
    },
    payload: {
      common_index: dailyDates('2018-01-01', 504),
      series_by_key: {
        beta: rampValues(504, 0.4, 1.2),
        alpha: rampValues(504, -1.5, 1.5),
        r_squared: rampValues(504, 0.3, 0.9),
      },
    },
  };
}

/** Regime summary Panel — one row at the substrate's sentinel date.
 *  Pre-PR2 the widget literal-rendered ``as-of 1900-01-01``.  The
 *  PR2 classifier marks this row as a scalar marker; PR4 locks the
 *  rendering by asserting that the widget calls ``classifyArtifactDate``
 *  before formatting the row. */
export function regimeSummaryPanel(): PanelPayloadEnvelope {
  return {
    artifact_type: 'Panel',
    metadata: {
      units_by_column: {
        mean_beta: 'decimal',
        std_beta: 'decimal',
        n_obs: 'count',
      },
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['1900-01-01'],
      columns: ['mean_beta', 'std_beta', 'n_obs'],
      data: [[0.92, 0.18, 252]],
    },
  };
}

// ---------------------------------------------------------------------------
// 4. Sentinel-date fixtures (PR2 date policy)
// ---------------------------------------------------------------------------
//
// Two flavours of substrate-emitted sentinel:
//   - ``summarize_series`` writes 1900-01-01.
//   - ``conditional_aggregate`` writes 1970-anchored offsets +
//     index_encoding.
// Used by tests that prove ``classifyArtifactDate`` classifies them
// correctly AND that the consuming widgets respect the policy.

export function sentinelOneRowSeries(): SeriesPayloadEnvelope {
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: 'SummarizeSeries/UST_10Y_mean',
      units: 'percent',
      frequency: null,
      missingness_policy: {},
      lineage: { steps: [] },
    },
    payload: {
      index: ['1900-01-01'],
      values: [4.21],
      name: 'mean',
      type: 'Series',
    },
  };
}

export function offsetAnchoredSeries(): SeriesPayloadEnvelope {
  return conditionalAggregateSeries();
}

// ---------------------------------------------------------------------------
// 5. Parameter-override workspace fixtures (PR3 fork surface)
// ---------------------------------------------------------------------------
//
// A forkable workspace + matching template card.  Walks the slot
// schema the canonical event-study template declares so the slot
// deriver / fork validator can be exercised against a realistic
// shape.

export function forkableWorkspaceDetail(
  overrides: Partial<WorkspaceDetail> = {},
): WorkspaceDetail {
  return {
    workspace_id: 'ws-fixture',
    slug: 'event-study-fixture',
    name: 'Event study fixture',
    dag_hash: 'd'.repeat(64),
    focus_node: 'compare',
    parent_workspace_id: null,
    schema_version: 1,
    created_by: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    nodes: eventStudyNodes(),
    edges: [],
    template_id: 'event_study',
    bound_slot_values: {
      curve_family: 'UST',
      tenor: '10Y',
      lookback_days: 504,
      signal_tool_name: 'get_yield_levels_tool',
      signal_output_field: 'time_series',
      signal_params: {
        window_days: 126,
        threshold: 1.5,
        method: 'rolling_zscore',
      },
    },
    ...overrides,
  };
}

export function eventStudyTemplateCard(): WorkflowTemplateCard {
  return {
    template_id: 'event_study',
    archetype: 'event_study',
    description: 'Event study over a thresholded signal series.',
    slot_schema: eventStudySlotSchema(),
    terminal_artifact_type: 'WindowedPanel',
    primitives_used: ['get_yield_levels_tool'],
    operators_used: [
      'threshold_events',
      'event_windows',
      'conditional_aggregate',
      'series_arithmetic',
    ],
    node_count: 7,
    edge_count: 9,
    archetype_signature: ['threshold_events', 'event_windows'],
  };
}

export function eventStudySlotSchema(): SlotDeclaration[] {
  return [
    { name: 'curve_family', type: 'str', required: true, description: '' },
    { name: 'tenor', type: 'str', required: true, description: '' },
    { name: 'lookback_days', type: 'int', required: false, description: '' },
    {
      name: 'signal_tool_name',
      type: 'str',
      required: true,
      description: 'Tool used to construct the signal series.',
    },
    {
      name: 'signal_output_field',
      type: 'str',
      required: true,
      description: 'Output field of the signal tool to lift.',
    },
    {
      name: 'signal_params',
      type: 'dict',
      required: true,
      description: 'Inner signal-construction parameters.',
    },
  ];
}

/** Minimal tool catalogue covering the tools the slot fixtures
 *  reference.  Mirrors the ``ToolCard`` shape ``useTools()``
 *  produces — the PR3 validator + slot deriver consume this
 *  unchanged. */
export function fixtureToolCatalogue(): ToolCard[] {
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

function eventStudyNodes(): NodeSummary[] {
  return [
    primitiveNode('signal', 'get_yield_levels_tool'),
    primitiveNode('target', 'get_yield_levels_tool'),
    operatorNode('events', 'threshold_events'),
    operatorNode('windows', 'event_windows'),
    operatorNode('aggregate', 'conditional_aggregate'),
    operatorNode('unconditional_aggregate', 'conditional_aggregate'),
    operatorNode('compare', 'series_arithmetic'),
  ];
}

// ---------------------------------------------------------------------------
// 6. Unsupported / custom tool context fixture
// ---------------------------------------------------------------------------
//
// A workspace whose nodes reference a tool that does NOT exist in
// the catalogue.  Used by the PR4 regression locks to assert that
// the renderer registry + adapter pipeline degrades gracefully —
// no crash, no fabricated body, just the generic-artifact fallback.

export function unsupportedToolWorkspace(): WorkspaceDetail {
  return {
    workspace_id: 'ws-unsupported-fixture',
    slug: 'unsupported',
    name: 'Custom tool fixture',
    dag_hash: 'e'.repeat(64),
    focus_node: 'custom',
    parent_workspace_id: null,
    schema_version: 1,
    created_by: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    nodes: [
      primitiveNode('custom', 'custom_unregistered_tool'),
      operatorNode('custom_op', 'unknown_operator'),
    ],
    edges: [],
    template_id: null,
    bound_slot_values: null,
  };
}

export function unsupportedPrimitiveNode(): NodeSummary {
  return primitiveNode('custom', 'custom_unregistered_tool');
}

// ---------------------------------------------------------------------------
// Convenience bundle — every fixture by name so tests can iterate
// generically (e.g. "every fixture decodes" or "every sentinel-date
// fixture classifies correctly").
// ---------------------------------------------------------------------------

export const ALL_SERIES_FIXTURES: Record<string, () => SeriesPayloadEnvelope> =
  {
    pca_factor_score: pcaFactorScoreSeries,
    rolling_regression_beta: rollingRegressionBetaSeries,
    beta_adjusted_spread: betaAdjustedSpreadSeries,
    attribution_pure_snapshot: attributionPureSnapshotSeries,
    half_life_pure_snapshot: halfLifePureSnapshotSeries,
    event_study_signal: eventStudySignalSeries,
    event_study_target: eventStudyTargetSeries,
    conditional_aggregate: conditionalAggregateSeries,
    regime_lhs: regimeLhsSeries,
    regime_rhs: regimeRhsSeries,
    sentinel_one_row: sentinelOneRowSeries,
    offset_anchored: offsetAnchoredSeries,
  };

/** Every fixture whose payload index contains the 1900-01-01
 *  sentinel.  Iterated by ``regressionLock.test.ts`` to assert
 *  ``isSentinelOneRowSeries`` classifies all of them. */
export const SENTINEL_FIXTURES: Array<{
  name: string;
  factory: () => SeriesPayloadEnvelope;
}> = [
  { name: 'attribution_pure_snapshot', factory: attributionPureSnapshotSeries },
  { name: 'half_life_pure_snapshot', factory: halfLifePureSnapshotSeries },
  { name: 'sentinel_one_row', factory: sentinelOneRowSeries },
];

export const ALL_OTHER_ARTIFACT_FIXTURES: Record<
  string,
  () =>
    | EventSetPayloadEnvelope
    | PanelPayloadEnvelope
    | SeriesSetPayloadEnvelope
    | TradeSetPayloadEnvelope
    | WindowedPanelPayloadEnvelope
> = {
  event_study_event_set: eventStudyEventSet,
  event_study_windowed_panel: eventStudyWindowedPanel,
  regime_relationship_series_set: regimeRelationshipSeriesSet,
  regime_summary_panel: regimeSummaryPanel,
  paused_backtest_trade_set: pausedBacktestTradeSet,
};

/** Backtest archetype is paused — the persisted TradeSet for the
 *  surface used in dashboards comes back empty (zero trades).  We
 *  surface it as a fixture so tests can assert the BacktestDashboard
 *  honours the paused state even when the persisted shape is non-
 *  empty / arbitrary. */
export function pausedBacktestTradeSet(): TradeSetPayloadEnvelope {
  return {
    artifact_type: 'TradeSet',
    metadata: {
      source_event_key: 'cpi/threshold',
      methodology_policy: 'simulated',
      lineage: { steps: [] },
    },
    payload: { trades: [] },
  };
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function primitiveNode(id: string, toolName: string): NodeSummary {
  return {
    node_id: id,
    kind: 'PrimitiveNode',
    name: id,
    params: { tool_name: toolName, output_field: 'time_series', params: {} },
    artifact_hash: hashFor(id),
    artifact: null,
  };
}

function operatorNode(id: string, operatorName: string): NodeSummary {
  return {
    node_id: id,
    kind: 'OperatorNode',
    name: id,
    params: { operator_name: operatorName, params: {} },
    artifact_hash: hashFor(id),
    artifact: null,
  };
}

function hashFor(seed: string): string {
  // Stable 64-char lowercase-hex shape — ``isLikelyArtifactHash``
  // expects this exact pattern.  Not a real hash; just makes the
  // fixture data look right to validators.
  let acc = 0;
  for (let i = 0; i < seed.length; i++) acc = (acc * 31 + seed.charCodeAt(i)) | 0;
  const padded = Math.abs(acc).toString(16).padStart(16, '0');
  return (padded + 'abcdef0123456789'.repeat(8)).slice(0, 64);
}

function dailyDates(start: string, count: number): string[] {
  const out: string[] = [];
  const base = new Date(start + 'T00:00:00Z');
  for (let i = 0; i < count; i++) {
    const d = new Date(base.getTime() + i * 86400_000);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function rampValues(count: number, from: number, to: number): number[] {
  if (count <= 1) return [from];
  const step = (to - from) / (count - 1);
  const out: number[] = [];
  for (let i = 0; i < count; i++) out.push(from + i * step);
  return out;
}
