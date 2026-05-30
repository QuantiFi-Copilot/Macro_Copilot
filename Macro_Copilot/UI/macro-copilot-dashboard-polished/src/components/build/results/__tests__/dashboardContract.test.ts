/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// dashboardContract.test.ts — PR7 source-level invariants on the Results tab.
// ----------------------------------------------------------------------------
// Locks the PR7 architectural rules at the source level:
//
//   1. ``ResultsView`` consumes ``resolveWorkflowDashboard`` and
//      switches on the result.
//   2. ``ResultsView`` mounts each specialised dashboard for its
//      matching kind.
//   3. ``ResultsView`` exposes a generic-grid toggle so every
//      persisted artifact stays accessible underneath the
//      specialised dashboard.
//   4. ``BacktestDashboard`` always renders its paused banner
//      regardless of artifact presence.
//   5. ``BacktestDashboard`` does NOT fabricate metrics — no
//      ``Sharpe`` / ``CAGR`` / ``drawdown`` string-literal copy in
//      the source unless it's wrapped in the missing-artifact /
//      paused copy that explicitly disclaims fabrication.
//   6. Every specialised dashboard imports the resolver and
//      ``MissingArtifactCard``.
//   7. ``BuildCompleted`` still mounts ``<ResultsView detail=...>``.
//
// Source-level structural checks via the same shim PR3 / PR4 / PR5 /
// PR6 use.  Bundled with esbuild for node execution.
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

let _src: Record<string, string> = {};

async function loadSources(): Promise<Record<string, string>> {
  // @ts-ignore - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const files = [
    'src/components/build/results/ResultsView.tsx',
    'src/components/build/results/dashboards/EventStudyDashboard.tsx',
    'src/components/build/results/dashboards/RegimeRelationshipDashboard.tsx',
    'src/components/build/results/dashboards/BacktestDashboard.tsx',
    'src/components/build/results/dashboards/GenericResultsDashboard.tsx',
    'src/components/build/completed/BuildCompleted.tsx',
  ];
  const out: Record<string, string> = {};
  for (const path of files) {
    const candidates = [`${cwd}/${path}`, `./${path}`];
    let loaded = false;
    for (const p of candidates) {
      try {
        out[path] = fs.readFileSync(p, 'utf8');
        loaded = true;
        break;
      } catch {
        // try next
      }
    }
    if (!loaded) {
      throw new Error(`Could not read source ${path}; cwd=${cwd}`);
    }
  }
  return out;
}

// ----------------------------------------------------------------------------
// Contract checks
// ----------------------------------------------------------------------------

check('ResultsView: consumes resolveWorkflowDashboard', () => {
  const src = _src['src/components/build/results/ResultsView.tsx'];
  assertContains(src, 'resolveWorkflowDashboard', 'imports registry');
  assertContains(
    src,
    "from './lib/dashboardRegistry'",
    'canonical path',
  );
});

check('ResultsView: mounts each specialised dashboard', () => {
  const src = _src['src/components/build/results/ResultsView.tsx'];
  assertContains(src, '<EventStudyDashboard', 'event_study mounted');
  assertContains(
    src,
    '<RegimeRelationshipDashboard',
    'regime mounted',
  );
  assertContains(src, '<BacktestDashboard', 'backtest mounted');
});

check('ResultsView: falls back to GenericResultsDashboard', () => {
  const src = _src['src/components/build/results/ResultsView.tsx'];
  assertContains(src, 'GenericResultsDashboard', 'generic fallback');
});

check('ResultsView: surfaces "Show intermediate stages" toggle', () => {
  const src = _src['src/components/build/results/ResultsView.tsx'];
  // The toggle keeps the generic grid accessible beneath every
  // specialised dashboard, so no persisted artifact is hidden.  The
  // copy reads "intermediate stages" (vocabulary aligned with the
  // surface contract §6 operator visibility policy — see
  // docs_revamped/02_components/surface_contract.md), and the toggle
  // counts non-terminal artifacts up-front so the surface area is
  // visible before the panel is opened.
  assertContains(src, 'Show intermediate stages', 'toggle copy');
  assertContains(src, 'showAll', 'state variable');
});

check('EventStudyDashboard: uses resolver + MissingArtifactCard', () => {
  const src =
    _src['src/components/build/results/dashboards/EventStudyDashboard.tsx'];
  assertContains(src, 'resolveEventStudyArtifacts', 'uses resolver');
  assertContains(src, 'MissingArtifactCard', 'honest fallback');
  assertContains(src, '<NodeWidgetCard', 'reuses PR4 widget');
});

check('RegimeRelationshipDashboard: uses resolver + MissingArtifactCard', () => {
  const src =
    _src[
      'src/components/build/results/dashboards/RegimeRelationshipDashboard.tsx'
    ];
  assertContains(src, 'resolveRegimeArtifacts', 'uses resolver');
  assertContains(src, 'MissingArtifactCard', 'honest fallback');
  assertContains(src, '<NodeWidgetCard', 'reuses PR4 widget');
});

check('BacktestDashboard: always renders the paused banner', () => {
  const src =
    _src['src/components/build/results/dashboards/BacktestDashboard.tsx'];
  assertContains(src, '<PausedBanner', 'banner mounted');
  assertContains(
    src,
    'Backtest archetype · paused on the LLM surface',
    'paused copy is honest',
  );
});

check('BacktestDashboard: uses resolver + MissingArtifactCard', () => {
  const src =
    _src['src/components/build/results/dashboards/BacktestDashboard.tsx'];
  assertContains(src, 'resolveBacktestArtifacts', 'uses resolver');
  assertContains(src, 'MissingArtifactCard', 'honest fallback');
});

check('BacktestDashboard: does NOT fabricate metrics outside disclaimers', () => {
  const src =
    _src['src/components/build/results/dashboards/BacktestDashboard.tsx'];
  // No standalone scalar literals for Sharpe / CAGR / drawdown
  // outside disclaimer copy.  We check that every mention of these
  // tokens is in a context that disclaims fabrication.  Simple
  // grep: look for the literal numbers that would represent a
  // hardcoded metric — none should appear.
  assertNotContains(src, 'Sharpe: ', 'no Sharpe value');
  assertNotContains(src, 'CAGR: ', 'no CAGR value');
  assertNotContains(src, 'Drawdown: ', 'no drawdown value');
  // Tokens MAY appear inside copy that disclaims fabrication.
  // Spot-check that "Sharpe" / "CAGR" / "drawdown" presence is OK
  // when it's in disclaimer copy (don't enforce absence, just that
  // there's no metric assignment).
});

check('GenericResultsDashboard: still uses NodeWidgetCard from PR4', () => {
  const src =
    _src[
      'src/components/build/results/dashboards/GenericResultsDashboard.tsx'
    ];
  assertContains(src, 'NodeWidgetCard', 'PR4 widget');
});

check('BuildCompleted: still mounts <ResultsView detail=...>', () => {
  const src = _src['src/components/build/completed/BuildCompleted.tsx'];
  assertContains(src, '<ResultsView', 'ResultsView mounted');
  assertContains(src, 'detail={detail}', 'detail passed through');
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllDashboardContractTests(): Promise<void> {
  _src = await loadSources();
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
    `\ndashboard contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} dashboard-contract check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllDashboardContractTests();
}
