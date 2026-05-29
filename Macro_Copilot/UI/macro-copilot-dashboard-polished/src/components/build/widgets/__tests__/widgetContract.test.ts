/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// widgetContract.test.ts — PR4 structural assertions on the widget surface.
// ----------------------------------------------------------------------------
// Each widget gets ONE source-level invariant test that locks the
// load-bearing PR4 behaviour:
//
//   - EventSetWidget    : does NOT use ``artifact.row_count`` as the
//                          event count (the bug Codex flagged).
//   - PanelWidget       : reads ``payload.columns`` / ``payload.data``
//                          rather than ``preview_values``.
//   - SeriesWidget      : reads ``payload.values`` / ``payload.index``.
//   - SeriesSetWidget   : surfaces member-key selection.
//   - WindowedPanelW.   : reads ``metadata.offsets`` + ``payload.event_dates``.
//   - TradeSetWidget    : reads ``payload.trades`` (not just ``row_count``).
//   - ScalarMetricW.    : has no ``registerArtifactRenderer`` call.
//
// Every payload-backed widget MUST go through ``PayloadShell``.
//
// Same source-reading pattern as the PR3 contract test (dynamic
// import of node fs, all module-load wrapped in PayloadShell shell so
// tsconfig.app.json doesn't need @types/node).
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}

function assertContains(
  haystack: string,
  needle: string,
  label: string,
): void {
  if (!haystack.includes(needle)) {
    throw new Error(`assertContains failed: ${label}\n  looking for: ${needle}`);
  }
}

function assertNotContains(
  haystack: string,
  needle: string,
  label: string,
): void {
  if (haystack.includes(needle)) {
    throw new Error(`assertNotContains failed: ${label}\n  found: ${needle}`);
  }
}

// ----------------------------------------------------------------------------
// Source loader — same pattern as the PR3 RichModelWidget contract.
// ----------------------------------------------------------------------------

let _sourceCache: Record<string, string> | null = null;

async function loadSources(): Promise<Record<string, string>> {
  if (_sourceCache) return _sourceCache;
  // @ts-ignore - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const files = [
    'EventSetWidget.tsx',
    'PanelWidget.tsx',
    'SeriesWidget.tsx',
    'SeriesSetWidget.tsx',
    'WindowedPanelWidget.tsx',
    'TradeSetWidget.tsx',
    'ScalarMetricWidget.tsx',
  ];
  const out: Record<string, string> = {};
  for (const f of files) {
    const candidates = [
      `${cwd}/src/components/build/widgets/${f}`,
      `./src/components/build/widgets/${f}`,
    ];
    let loaded = false;
    for (const p of candidates) {
      try {
        out[f] = fs.readFileSync(p, 'utf8');
        loaded = true;
        break;
      } catch {
        // try next
      }
    }
    if (!loaded) {
      throw new Error(`Could not read widget source ${f}; cwd=${cwd}`);
    }
  }
  _sourceCache = out;
  return out;
}

let SRC: Record<string, string> = {};

// ----------------------------------------------------------------------------
// EventSetWidget — the PR4 bug fix
// ----------------------------------------------------------------------------

check('EventSetWidget: does NOT use artifact.row_count as event count', () => {
  const src = SRC['EventSetWidget.tsx'];
  // The pre-PR4 buggy pattern.  ANY remaining ``artifact.row_count``
  // reference would re-introduce the bug.
  assertNotContains(
    src,
    'artifact.row_count',
    'no artifact.row_count reference',
  );
});

check('EventSetWidget: uses eventCountFromPayload helper', () => {
  const src = SRC['EventSetWidget.tsx'];
  assertContains(src, 'eventCountFromPayload', 'imports the helper');
  assertContains(
    src,
    "expectedType=\"EventSet\"",
    'declares EventSet expected type',
  );
});

// ----------------------------------------------------------------------------
// PanelWidget — payload-backed columns/rows
// ----------------------------------------------------------------------------

check('PanelWidget: reads payload columns/rows (not preview_values)', () => {
  const src = SRC['PanelWidget.tsx'];
  assertContains(src, 'panelColumns', 'uses panelColumns');
  assertContains(src, 'panelRowIndex', 'uses panelRowIndex');
  assertContains(src, 'panelCell', 'uses panelCell');
  assertNotContains(
    src,
    'artifact.preview_values',
    'no preview_values reads',
  );
});

check('PanelWidget: routes through PayloadShell', () => {
  const src = SRC['PanelWidget.tsx'];
  assertContains(src, "expectedType=\"Panel\"", 'expected type Panel');
});

// ----------------------------------------------------------------------------
// SeriesWidget — payload-backed observations
// ----------------------------------------------------------------------------

check('SeriesWidget: reads payload values/index (not preview_values)', () => {
  const src = SRC['SeriesWidget.tsx'];
  assertContains(src, 'lastSeriesObservation', 'uses last-obs helper');
  assertContains(src, 'firstSeriesObservation', 'uses first-obs helper');
  assertContains(src, 'seriesObservationCount', 'obs count');
  assertNotContains(
    src,
    'artifact.preview_values',
    'no preview_values reads',
  );
});

// ----------------------------------------------------------------------------
// SeriesSetWidget — member-key selection
// ----------------------------------------------------------------------------

check('SeriesSetWidget: surfaces member-key selection', () => {
  const src = SRC['SeriesSetWidget.tsx'];
  assertContains(src, 'seriesSetMembers', 'reads members');
  assertContains(src, 'seriesSetMemberAsSeries', 'reads per-member payload');
  assertContains(src, 'useState', 'maintains active-member state');
});

// ----------------------------------------------------------------------------
// WindowedPanelWidget — event-window structure
// ----------------------------------------------------------------------------

check('WindowedPanelWidget: reads offsets + event_dates', () => {
  const src = SRC['WindowedPanelWidget.tsx'];
  assertContains(src, 'windowedOffsets', 'reads offsets');
  assertContains(src, 'windowedEventCount', 'reads event count');
  assertContains(src, 'offsetTick', 't-N / t0 / t+N labelling');
});

// ----------------------------------------------------------------------------
// TradeSetWidget — honest trades or paused state
// ----------------------------------------------------------------------------

check('TradeSetWidget: reads payload.trades + has paused-state branch', () => {
  const src = SRC['TradeSetWidget.tsx'];
  assertContains(src, 'tradeRowCount', 'counts from payload');
  assertContains(src, 'PausedState', 'has paused-state component');
  assertNotContains(
    src,
    'artifact.row_count',
    'no artifact.row_count reads',
  );
});

// ----------------------------------------------------------------------------
// ScalarMetricWidget — registration dropped
// ----------------------------------------------------------------------------

check('ScalarMetricWidget: registration is dropped (deferred backend type)', () => {
  const src = SRC['ScalarMetricWidget.tsx'];
  assertNotContains(
    src,
    "registerArtifactRenderer('ScalarMetric'",
    'no ScalarMetric registration',
  );
});

// ----------------------------------------------------------------------------
// Every payload-backed widget goes through PayloadShell
// ----------------------------------------------------------------------------

check('every payload-backed widget imports PayloadShell', () => {
  for (const f of [
    'EventSetWidget.tsx',
    'PanelWidget.tsx',
    'SeriesWidget.tsx',
    'SeriesSetWidget.tsx',
    'WindowedPanelWidget.tsx',
    'TradeSetWidget.tsx',
  ]) {
    assertContains(
      SRC[f],
      "import { PayloadShell }",
      `${f} imports PayloadShell`,
    );
  }
});

check('every payload-backed widget self-registers a renderer', () => {
  for (const [f, type] of [
    ['EventSetWidget.tsx', 'EventSet'],
    ['PanelWidget.tsx', 'Panel'],
    ['SeriesWidget.tsx', 'Series'],
    ['SeriesSetWidget.tsx', 'SeriesSet'],
    ['WindowedPanelWidget.tsx', 'WindowedPanel'],
    ['TradeSetWidget.tsx', 'TradeSet'],
  ] as const) {
    assertContains(
      SRC[f],
      `registerArtifactRenderer('${type}'`,
      `${f} registers ${type}`,
    );
  }
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllWidgetContractTests(): Promise<void> {
  SRC = await loadSources();
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
    `\nwidget contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} widget contract check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllWidgetContractTests();
}
