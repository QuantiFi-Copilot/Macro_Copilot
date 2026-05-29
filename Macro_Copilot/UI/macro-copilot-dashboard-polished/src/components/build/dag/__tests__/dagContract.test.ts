/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// dagContract.test.ts — PR6 source-level invariants for the DAG view.
// ----------------------------------------------------------------------------
// Locks the PR6 architectural rules at the source level:
//
//   1. ``DagView`` consumes the ``buildDagModel`` normalization layer
//      (no graph parsing inside JSX).
//   2. ``DagView`` renders the SVG edge layer + the warning banner.
//   3. ``DagView`` no longer uses the pre-PR6 single-row
//      ``EdgeConnector`` for adjacency rendering.
//   4. ``DagInspector`` reads ``model.edges`` for inputs/outputs.
//   5. ``BuildCompleted`` still mounts ``<DagView detail=...>`` so the
//      tab integration is preserved.
//
// Source-level structural checks via the same shim PR3 / PR4 / PR5
// use.  Bundled with esbuild for node execution.
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
    'src/components/build/dag/DagView.tsx',
    'src/components/build/dag/DagInspector.tsx',
    'src/components/build/dag/DagEdgesLayer.tsx',
    'src/components/build/dag/DagWarningsBanner.tsx',
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

check('DagView: consumes buildDagModel from the normalization layer', () => {
  const src = _src['src/components/build/dag/DagView.tsx'];
  assertContains(src, 'buildDagModel', 'imports buildDagModel');
  assertContains(src, "from './lib/buildDagModel'", 'canonical path');
});

check('DagView: renders DagEdgesLayer (SVG overlay)', () => {
  const src = _src['src/components/build/dag/DagView.tsx'];
  assertContains(src, '<DagEdgesLayer', 'edge layer mounted');
});

check('DagView: renders DagWarningsBanner', () => {
  const src = _src['src/components/build/dag/DagView.tsx'];
  assertContains(src, '<DagWarningsBanner', 'warnings banner mounted');
});

check('DagView: renders DagInspector for selection-driven details', () => {
  const src = _src['src/components/build/dag/DagView.tsx'];
  assertContains(src, '<DagInspector', 'inspector mounted');
  assertContains(src, 'selectedNodeId', 'maintains selection state');
});

check('DagView: no longer imports the pre-PR6 EdgeConnector', () => {
  // The single-row pre-PR6 EdgeConnector is incompatible with the
  // branch-aware grid layout (it was meant for adjacent nodes in a
  // flat strip).  Re-introducing it would be a regression.
  const src = _src['src/components/build/dag/DagView.tsx'];
  assertNotContains(src, "from './EdgeConnector'", 'no EdgeConnector import');
});

check('DagView: uses absolute positioning + a relative canvas wrapper', () => {
  const src = _src['src/components/build/dag/DagView.tsx'];
  // Coordinate-driven placement (each card has left/top from
  // ``nodeCoords``) — confirms we're branch-aware, not flat-CSS-flex.
  assertContains(src, 'nodeCoords', 'reads pixel coords');
  // Tailwind ``absolute`` class on the card wrapper + ``relative`` on
  // the canvas; coordinates come through inline ``left:`` / ``top:``.
  assertContains(src, "'absolute z-", 'absolute card positioning');
  assertContains(src, 'left: coord.x', 'inline left from coord');
  assertContains(src, 'top: coord.y', 'inline top from coord');
});

check('DagInspector: reads model.edges for inputs/outputs', () => {
  const src = _src['src/components/build/dag/DagInspector.tsx'];
  assertContains(
    src,
    'model.edges.filter((e) => e.to === node.node_id)',
    'incoming filter',
  );
  assertContains(
    src,
    'model.edges.filter((e) => e.from === node.node_id)',
    'outgoing filter',
  );
});

check('DagInspector: surfaces artifact identity (hash + type + units)', () => {
  const src = _src['src/components/build/dag/DagInspector.tsx'];
  assertContains(src, 'artifact_type', 'shows artifact type');
  assertContains(src, 'node.artifact_hash', 'reads hash');
});

check('DagEdgesLayer: emits SVG markers for normal + back + selected arrows', () => {
  const src = _src['src/components/build/dag/DagEdgesLayer.tsx'];
  assertContains(src, 'dag-arrow-normal', 'normal arrowhead');
  assertContains(src, 'dag-arrow-back', 'back-edge arrowhead');
  assertContains(src, 'dag-arrow-selected', 'selected arrowhead');
});

check('DagEdgesLayer: supports the showLabels toggle', () => {
  const src = _src['src/components/build/dag/DagEdgesLayer.tsx'];
  assertContains(src, 'showLabels', 'reads the toggle');
});

check('DagWarningsBanner: imports describeWarning for honest copy', () => {
  const src = _src['src/components/build/dag/DagWarningsBanner.tsx'];
  assertContains(src, 'describeWarning', 'uses honest warning copy');
});

check('BuildCompleted: still mounts <DagView detail=...>', () => {
  // PR6 must not regress the tab integration.
  const src = _src['src/components/build/completed/BuildCompleted.tsx'];
  assertContains(src, '<DagView', 'DagView still in tab tree');
  assertContains(src, 'detail={detail}', 'detail still passed through');
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllDagContractTests(): Promise<void> {
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
    `\ndag contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} dag-contract check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllDagContractTests();
}
