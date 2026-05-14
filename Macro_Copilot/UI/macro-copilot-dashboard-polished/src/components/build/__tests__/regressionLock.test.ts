/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// regressionLock.test.ts — PR4 cross-cutting regression locks.
// ----------------------------------------------------------------------------
// Locks the load-bearing invariants from PR1 / PR2 / PR3 against
// future regressions.  Each section names the PR it guards.  The
// tests are intentionally narrow + source-or-fixture-driven so they
// catch silent reverts without depending on a browser environment.
//
// Sections
// --------
//   §A — Persisted decode never fails (PR1)
//   §B — Date-policy widgets suppress sentinels (PR2)
//   §C — Fork pipeline goes through overridesToServerPatch (PR3)
//   §D — Build entrypoints stay honest (PR3)
//
// All assertions live in this one file so the runner emits a single
// "regression-lock coverage" line that's easy to scan.  The fixtures
// come from ``__fixtures__/artifactFixtures.ts`` and are deliberately
// shape-true.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

import {
  adaptModelArtifact,
  getModelAdapter,
  GENERIC_MODEL_ADAPTER,
  isModelTool,
  type ModelToolName,
} from '@/components/build/widgets/shared/persistedModelAdapters';
import {
  classifyArtifactDate,
  isSentinelOneRowSeries,
  getEventOffsetEncoding,
  windowedEventCount,
  eventCountFromPayload,
} from '@/components/build/widgets/shared/artifactFormat';
import { overridesToServerPatch } from '@/components/build/parameters/lib/overridesState';
import { validateOverrides } from '@/components/build/parameters/lib/validateOverrides';
import {
  ALL_OTHER_ARTIFACT_FIXTURES,
  ALL_SERIES_FIXTURES,
  attributionPureSnapshotSeries,
  betaAdjustedSpreadSeries,
  conditionalAggregateSeries,
  eventStudyEventSet,
  eventStudySlotSchema,
  eventStudyTargetSeries,
  eventStudyTemplateCard,
  eventStudyWindowedPanel,
  fixtureToolCatalogue,
  forkableWorkspaceDetail,
  halfLifePureSnapshotSeries,
  pcaFactorScoreSeries,
  regimeLhsSeries,
  regimeRelationshipSeriesSet,
  regimeRhsSeries,
  regimeSummaryPanel,
  rollingRegressionBetaSeries,
  SENTINEL_FIXTURES,
  sentinelOneRowSeries,
  unsupportedToolWorkspace,
} from './__fixtures__/artifactFixtures';
import type {
  ArtifactType,
  SeriesPayloadEnvelope,
} from '@/types/artifacts';

type Check = { label: string; fn: () => void | Promise<void> };
const _checks: Check[] = [];

function check(label: string, fn: () => void | Promise<void>): void {
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

function assertContains(
  haystack: string,
  needle: string,
  label: string,
): void {
  if (!haystack.includes(needle)) {
    throw new Error(`assertContains failed: ${label}\n  needle: ${needle}`);
  }
}

async function loadSource(relativePath: string): Promise<string> {
  // @ts-expect-error - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const candidates = [`${cwd}/${relativePath}`, `./${relativePath}`];
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, 'utf8');
    } catch {
      // try next
    }
  }
  throw new Error(`Could not read ${relativePath}; cwd=${cwd}`);
}

// ============================================================================
// §A — PR1 — Persisted decode never fails
// ============================================================================
// Walks every model tool, every Series fixture, every "other"
// artifact-shape fixture and asserts the adapter + the typed
// envelope round-trip without throwing.  The pre-PR1 bug pattern
// was "tool X with a real persisted body crashes the rich-widget"
// — fall-through to the generic / type-mismatch branch is fine, a
// thrown exception is not.

const MODEL_TOOL_NAMES: ModelToolName[] = [
  'calculate_pca_yield_curve_tool',
  'calculate_rolling_regression_tool',
  'calculate_yield_change_attribution_pca_tool',
  'calculate_half_life_tool',
  'calculate_beta_adjusted_spread_tool',
];

check('§A · every ModelToolName has a non-generic adapter', () => {
  for (const tool of MODEL_TOOL_NAMES) {
    assertTruthy(isModelTool(tool), `${tool} is recognised`);
    const adapter = getModelAdapter(tool);
    assertTruthy(adapter.toolName === tool, `${tool} adapter routes correctly`);
    assertTruthy(
      adapter.displayName && adapter.displayName !== '',
      `${tool} has a display name`,
    );
    assertTruthy(
      adapter.persistedRole.headline.length > 0,
      `${tool} has a persisted-role headline`,
    );
  }
});

check('§A · unknown tool routes to GENERIC_MODEL_ADAPTER (no crash)', () => {
  const adapter = getModelAdapter('definitely_not_a_real_tool');
  assertEqual(adapter, GENERIC_MODEL_ADAPTER, 'generic fallback');
});

check('§A · adaptModelArtifact: every (tool × Series fixture) decodes without throwing', () => {
  const seriesFixtures = Object.entries(ALL_SERIES_FIXTURES);
  let exercised = 0;
  for (const tool of MODEL_TOOL_NAMES) {
    for (const [name, factory] of seriesFixtures) {
      const view = adaptModelArtifact({
        toolName: tool,
        payload: factory(),
      });
      assertTruthy(
        view && typeof view.kind === 'string',
        `${tool} × ${name} returns a typed view`,
      );
      assertTruthy(
        view.kind === 'persisted_series' ||
          view.kind === 'pure_snapshot_unavailable' ||
          view.kind === 'shape_mismatch',
        `${tool} × ${name} is one of the documented variants`,
      );
      exercised += 1;
    }
  }
  assertTruthy(exercised >= 50, `${exercised} (tool × payload) combos exercised`);
});

check('§A · adaptModelArtifact: time-series tools accept a real Series → persisted_series', () => {
  // PCA / RollingReg / BetaAdjustedSpread all have hasTimeSeriesOutput,
  // so a real-shaped Series MUST produce persisted_series (not the
  // pure-snapshot bail-out).
  const cases: Array<{ tool: ModelToolName; payload: SeriesPayloadEnvelope }> = [
    { tool: 'calculate_pca_yield_curve_tool', payload: pcaFactorScoreSeries() },
    {
      tool: 'calculate_rolling_regression_tool',
      payload: rollingRegressionBetaSeries(),
    },
    {
      tool: 'calculate_beta_adjusted_spread_tool',
      payload: betaAdjustedSpreadSeries(),
    },
  ];
  for (const c of cases) {
    const view = adaptModelArtifact({ toolName: c.tool, payload: c.payload });
    assertEqual(view.kind, 'persisted_series', `${c.tool} persisted_series`);
  }
});

check('§A · adaptModelArtifact: pure-snapshot tools surface unavailable variant', () => {
  // Attribution + half-life have hasTimeSeriesOutput=false, so even
  // when handed a Series body they MUST take the "pure snapshot"
  // branch and surface the detail-unavailable list.
  const cases: Array<{ tool: ModelToolName; payload: SeriesPayloadEnvelope }> = [
    {
      tool: 'calculate_yield_change_attribution_pca_tool',
      payload: attributionPureSnapshotSeries(),
    },
    {
      tool: 'calculate_half_life_tool',
      payload: halfLifePureSnapshotSeries(),
    },
  ];
  for (const c of cases) {
    const view = adaptModelArtifact({ toolName: c.tool, payload: c.payload });
    assertEqual(
      view.kind,
      'pure_snapshot_unavailable',
      `${c.tool} pure_snapshot_unavailable`,
    );
  }
});

check('§A · every non-Series fixture has a registered artifact_type renderer', async () => {
  // Source-level — every fixture's artifact_type must appear in a
  // ``registerArtifactRenderer`` call inside widgets/*.tsx.  This
  // catches renderers being deleted without their fixture being
  // updated (or vice versa).
  const widgetFiles = [
    'src/components/build/widgets/SeriesWidget.tsx',
    'src/components/build/widgets/SeriesSetWidget.tsx',
    'src/components/build/widgets/PanelWidget.tsx',
    'src/components/build/widgets/EventSetWidget.tsx',
    'src/components/build/widgets/WindowedPanelWidget.tsx',
    'src/components/build/widgets/TradeSetWidget.tsx',
  ];
  const registered = new Set<ArtifactType>();
  for (const f of widgetFiles) {
    const src = await loadSource(f);
    for (const t of [
      'Series',
      'SeriesSet',
      'Panel',
      'EventSet',
      'WindowedPanel',
      'TradeSet',
    ] as const) {
      if (src.includes(`registerArtifactRenderer('${t}'`)) {
        registered.add(t);
      }
    }
  }
  const expected: ArtifactType[] = [
    'Series',
    'SeriesSet',
    'Panel',
    'EventSet',
    'WindowedPanel',
    'TradeSet',
  ];
  for (const t of expected) {
    assertTruthy(registered.has(t), `${t} renderer is registered`);
  }
});

// ============================================================================
// §B — PR2 — Date-policy widgets suppress sentinels
// ============================================================================
// Helper-level coverage already lives in widgetFormat.test.ts — this
// section adds source-level locks: the consuming widgets MUST call
// ``classifyArtifactDate`` (not bare ``formatDate``) on persisted
// dates.  Catches a regression where someone "simplifies" the code
// and reverts to formatting 1900/1970 literals.

check('§B · sentinel fixtures all classify as summary_sentinel', () => {
  for (const f of SENTINEL_FIXTURES) {
    const sample = f.factory();
    assertTruthy(
      isSentinelOneRowSeries(sample),
      `${f.name} is recognised as a sentinel one-row series`,
    );
    // And the underlying date string classifies correctly.
    assertEqual(
      classifyArtifactDate(sample.payload.index?.[0]).kind,
      'summary_sentinel',
      `${f.name} index[0] is summary_sentinel`,
    );
  }
});

check('§B · conditional-aggregate fixture carries an event_offset encoding', () => {
  const sample = conditionalAggregateSeries();
  const enc = getEventOffsetEncoding(sample);
  assertTruthy(enc, 'encoding present');
  assertEqual(enc!.anchor, '1970-01-01', 'anchor');
  assertEqual(enc!.offsets.length, 6, 'six offsets');
  // The raw 1970-01-NN dates exist on the payload — the renderer
  // must NEVER format them literally.  classifyArtifactDate with
  // the encoding hint returns event_offset_anchor for each.
  for (const idx of sample.payload.index ?? []) {
    assertEqual(
      classifyArtifactDate(idx, { hasEventOffsetEncoding: true }).kind,
      'event_offset_anchor',
      `${idx} → event_offset_anchor`,
    );
  }
});

check('§B · SeriesWidget source dispatches the three render modes', async () => {
  const src = await loadSource(
    'src/components/build/widgets/SeriesWidget.tsx',
  );
  assertContains(src, 'isSentinelOneRowSeries', 'imports sentinel detector');
  assertContains(src, 'getEventOffsetEncoding', 'imports offset detector');
  assertContains(src, 'SeriesSummaryScalar', 'sentinel mode renderer');
  assertContains(src, 'OffsetLabeledSeries', 'offset mode renderer');
  assertContains(src, 'CalendarSeries', 'calendar mode renderer');
});

check('§B · PanelWidget source consults classifyArtifactDate before formatDate', async () => {
  const src = await loadSource(
    'src/components/build/widgets/PanelWidget.tsx',
  );
  assertContains(src, 'classifyArtifactDate', 'imports classifier');
  // Specifically — the one-row-summary footer must gate the as-of
  // line on the real_date classification.  This was the PR2 fix.
  assertContains(src, "kind === 'real_date'", 'gates formatDate on real_date');
});

check('§B · WindowedPanelWidget source consults classifyArtifactDate', async () => {
  const src = await loadSource(
    'src/components/build/widgets/WindowedPanelWidget.tsx',
  );
  assertContains(src, 'classifyArtifactDate', 'imports classifier');
  assertContains(
    src,
    "kind === 'real_date'",
    'gates event-date cells on real_date',
  );
});

check('§B · SeriesSetWidget source consults classifyArtifactDate', async () => {
  const src = await loadSource(
    'src/components/build/widgets/SeriesSetWidget.tsx',
  );
  assertContains(src, 'classifyArtifactDate', 'imports classifier');
  assertContains(
    src,
    "kind === 'real_date'",
    'gates first/last-date cells on real_date',
  );
});

check('§B · event counts come from event_dates.length (regression on row_count bug)', () => {
  const eventSet = eventStudyEventSet();
  // The bug pattern was: pre-fix widgets read mask_index.length as
  // the event count (1261 calendar days).  The CORRECT answer is
  // event_dates.length (4 firings).
  assertEqual(eventSet.payload.mask_index.length, 1261, 'mask is large');
  assertEqual(
    eventCountFromPayload(eventSet),
    4,
    'event count from event_dates',
  );
});

check('§B · WindowedPanel event count matches event_dates.length', () => {
  const wp = eventStudyWindowedPanel();
  assertEqual(windowedEventCount(wp), 4, 'four events');
});

check('§B · regime summary Panel one-row sentinel classifies correctly', () => {
  const panel = regimeSummaryPanel();
  const rowDate = panel.payload.index?.[0];
  assertEqual(
    classifyArtifactDate(rowDate).kind,
    'summary_sentinel',
    'regime summary footer suppresses sentinel literal',
  );
});

// ============================================================================
// §C — PR3 — Fork pipeline goes through overridesToServerPatch
// ============================================================================
// Source-level lock that ``WorkspaceOverridesProvider.apply`` still
// computes the patch via ``overridesToServerPatch`` and submits it
// through ``forkWorkspace``.  Combined with the helper-level tests
// in slotControls.test.ts, this rules out a regression where apply
// is rewired to call a different (less-validated) endpoint.

check('§C · WorkspaceOverridesProvider.apply uses the canonical fork API', async () => {
  const src = await loadSource(
    'src/components/build/lib/workspaceOverridesContext.tsx',
  );
  assertContains(src, 'overridesToServerPatch(overrides)', 'computes patch');
  assertContains(
    src,
    'forkWorkspace(workspace.slug',
    'submits via forkWorkspace',
  );
  assertContains(src, 'slot_overrides:', 'sends slot_overrides bucket');
  assertContains(
    src,
    'slot_dict_overrides:',
    'sends slot_dict_overrides bucket',
  );
  assertContains(
    src,
    'navigate(`/workspace/${res.slug}`)',
    'navigates to the new variant slug on success',
  );
});

check('§C · WorkspaceOverridesProvider apply gate refuses invalid overrides', async () => {
  const src = await loadSource(
    'src/components/build/lib/workspaceOverridesContext.tsx',
  );
  assertContains(src, 'validateOverrides', 'calls validateOverrides');
  assertContains(src, 'validation.ok', 'reads validation.ok');
  assertContains(src, 'canApply', 'exposes canApply gate');
});

check('§C · overridesToServerPatch + scalar tool_name override → slot_overrides bucket', () => {
  // End-to-end: a queue with one tool_name override produces a
  // patch with ``slot_overrides[signal_tool_name] = "swap"`` and
  // empty ``slot_dict_overrides``.
  const patch = overridesToServerPatch({
    signal_tool_name: {
      mode: 'scalar',
      path: ['signal_tool_name'],
      value: 'get_swap_rates_tool',
      previousValue: 'get_yield_levels_tool',
    },
  });
  assertEqual(
    patch.slot_overrides,
    { signal_tool_name: 'get_swap_rates_tool' },
    'scalar bucket',
  );
  assertEqual(patch.slot_dict_overrides, {}, 'dict bucket empty');
});

check('§C · overridesToServerPatch + dict-field override → slot_dict_overrides bucket', () => {
  const patch = overridesToServerPatch({
    'signal_params.window_days': {
      mode: 'dict_field',
      path: ['signal_params', 'window_days'],
      value: 252,
      previousValue: 126,
    },
  });
  assertEqual(patch.slot_overrides, {}, 'scalar empty');
  assertEqual(
    patch.slot_dict_overrides,
    { signal_params: { window_days: 252 } },
    'dict bucket has the override',
  );
});

check('§C · validateOverrides: forkable workspace fixture passes when overrides are coherent', () => {
  const ws = forkableWorkspaceDetail();
  const card = eventStudyTemplateCard();
  const tools = fixtureToolCatalogue();
  // Coherent override: change signal_tool_name + signal_output_field
  // to a valid pairing on the same tool catalogue.
  const overrides = {
    signal_tool_name: {
      mode: 'scalar' as const,
      path: ['signal_tool_name'] as [string],
      value: 'get_swap_rates_tool',
      previousValue: 'get_yield_levels_tool',
    },
    signal_output_field: {
      mode: 'scalar' as const,
      path: ['signal_output_field'] as [string],
      value: 'swap_curve',
      previousValue: 'time_series',
    },
  };
  const res = validateOverrides({
    overrides,
    boundSlotValues: ws.bound_slot_values,
    tools,
    knownSlotNames: new Set(card.slot_schema.map((s) => s.name)),
  });
  assertEqual(res.ok, true, 'coherent pair accepted');
});

check('§C · validateOverrides: forkable workspace rejects mismatched tool/field', () => {
  // Same fixture, but the field doesn't belong to the chosen tool.
  const ws = forkableWorkspaceDetail();
  const card = eventStudyTemplateCard();
  const tools = fixtureToolCatalogue();
  const overrides = {
    signal_tool_name: {
      mode: 'scalar' as const,
      path: ['signal_tool_name'] as [string],
      value: 'get_swap_rates_tool',
      previousValue: 'get_yield_levels_tool',
    },
    signal_output_field: {
      mode: 'scalar' as const,
      path: ['signal_output_field'] as [string],
      // ``time_series`` is NOT in get_swap_rates_tool's output_fields.
      value: 'time_series',
      previousValue: 'time_series',
    },
  };
  const res = validateOverrides({
    overrides,
    boundSlotValues: ws.bound_slot_values,
    tools,
    knownSlotNames: new Set(card.slot_schema.map((s) => s.name)),
  });
  assertEqual(res.ok, false, 'mismatch rejected');
  assertEqual(
    res.errors[0].reason,
    'unknown_output_field',
    'reason is unknown_output_field',
  );
});

// ============================================================================
// §D — PR3 — Build entrypoints stay honest
// ============================================================================
// Cross-check that the backtest dashboard stays paused even when a
// non-empty TradeSet exists somewhere in the workspace, and that
// the proposed-overrides chips still validate before dispatching.

check('§D · BacktestDashboard always mounts the paused banner', async () => {
  const src = await loadSource(
    'src/components/build/results/dashboards/BacktestDashboard.tsx',
  );
  assertContains(src, '<PausedBanner', 'paused banner mounted');
  assertContains(
    src,
    'Backtest archetype · paused on the LLM surface',
    'paused copy is honest',
  );
});

check('§D · ProposedOverridesChips validates suggestions before dispatching', async () => {
  const src = await loadSource(
    'src/components/build/copilot-rail/ProposedOverridesChips.tsx',
  );
  assertContains(src, 'isProposedOverrideValid', 'imports validator');
  assertContains(src, "data-validation", 'tags each chip with validation state');
  assertContains(src, 'AlertTriangle', 'renders the invalid-chip icon');
});

check('§D · build_custom_dag tile is disabled with an empty promptSeed', async () => {
  const src = await loadSource(
    'src/components/build/empty/categoryDefinitions.ts',
  );
  // Single source of truth — the audit asks us to lock that the
  // custom-DAG tile cannot accidentally become an active path.
  assertContains(src, "id: 'build_custom_dag'", 'tile exists');
  assertContains(src, 'soon: true', 'tile is paused');
  assertContains(src, "promptSeed: ''", 'no dead promptSeed text');
});

check('§D · unsupported-tool workspace fixture is non-forkable', () => {
  const ws = unsupportedToolWorkspace();
  // The fixture's nodes reference a tool absent from any
  // catalogue.  template_id is null → ``isForkable`` is false →
  // WorkspaceSlotsPanel renders the NotForkableBanner instead of
  // pretending to expose slot controls.
  assertEqual(ws.template_id, null, 'template_id null');
  assertEqual(ws.bound_slot_values, null, 'bound_slot_values null');
  assertTruthy(ws.nodes.length > 0, 'still has nodes');
});

// ============================================================================
// §E — Misc cross-cutting: every fixture survives a JSON round-trip.
// ============================================================================
// Substrate emits JSON-shaped envelopes over the wire.  A fixture
// that doesn't survive a JSON round-trip would mask wire-format
// drift — assert each one round-trips exactly.

check('§E · every fixture survives a JSON.stringify round-trip', () => {
  let exercised = 0;
  for (const [name, factory] of Object.entries(ALL_SERIES_FIXTURES)) {
    const sample = factory();
    const round = JSON.parse(JSON.stringify(sample));
    assertEqual(
      round.artifact_type,
      sample.artifact_type,
      `${name} artifact_type preserved`,
    );
    exercised += 1;
  }
  for (const [name, factory] of Object.entries(ALL_OTHER_ARTIFACT_FIXTURES)) {
    const sample = factory();
    const round = JSON.parse(JSON.stringify(sample));
    assertEqual(
      round.artifact_type,
      sample.artifact_type,
      `${name} artifact_type preserved`,
    );
    exercised += 1;
  }
  assertTruthy(exercised >= 16, `${exercised} fixtures round-tripped`);
});

check('§E · template-card fixture matches the slot schema fixture', () => {
  const card = eventStudyTemplateCard();
  const schema = eventStudySlotSchema();
  assertEqual(
    card.slot_schema.length,
    schema.length,
    'fixture stays in lockstep',
  );
  // Spot-check: the tool_name + output_field slots PR3 keys off must
  // exist in the schema fixture.
  const names = card.slot_schema.map((s) => s.name);
  assertTruthy(names.includes('signal_tool_name'), 'tool_name slot present');
  assertTruthy(
    names.includes('signal_output_field'),
    'output_field slot present',
  );
});

check('§E · regime fixtures expose the load-bearing shapes', () => {
  // Cheap shape-only sanity checks — keep the regression locks
  // honest if a downstream test depends on these.
  const lhs = regimeLhsSeries();
  const rhs = regimeRhsSeries();
  const ss = regimeRelationshipSeriesSet();
  assertEqual(lhs.artifact_type, 'Series', 'lhs');
  assertEqual(rhs.artifact_type, 'Series', 'rhs');
  assertEqual(ss.artifact_type, 'SeriesSet', 'series set');
  const memberKeys = Object.keys(ss.payload.series_by_key);
  assertTruthy(memberKeys.includes('beta'), 'beta member');
  assertTruthy(memberKeys.includes('alpha'), 'alpha member');
  assertTruthy(memberKeys.includes('r_squared'), 'r² member');
});

check('§E · event-study target series fixture has the canonical shape', () => {
  const series = eventStudyTargetSeries();
  assertEqual(series.artifact_type, 'Series', 'Series');
  assertEqual(series.metadata.units, 'percent', 'percent units');
  assertTruthy(
    (series.payload.index?.length ?? 0) > 250,
    'multi-year history',
  );
  assertEqual(
    series.payload.index?.length,
    series.payload.values?.length,
    'index/values aligned',
  );
});

check('§E · sentinel one-row series has exactly one observation', () => {
  const s = sentinelOneRowSeries();
  assertEqual(s.payload.index?.length, 1, 'one row');
  assertEqual(s.payload.values?.length, 1, 'one value');
});

// ============================================================================
// Runner
// ============================================================================

export async function runAllRegressionLockTests(): Promise<void> {
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
    `\nregression-lock coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} regression-lock check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllRegressionLockTests();
}
