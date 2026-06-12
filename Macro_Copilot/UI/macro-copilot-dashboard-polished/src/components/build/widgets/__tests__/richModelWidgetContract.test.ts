/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// RichModelWidget contract test — structural assertions on the persisted-
// artifact widget contract (PR3).
// ----------------------------------------------------------------------------
// PR3's load-bearing invariant for ``RichModelWidget`` is that it
// stops re-running primitives to render persisted artifacts and
// instead loads them by hash via the new ``useArtifactPayload`` hook.
//
// A full hook-rendering integration test would need a DOM + React
// renderer in the test bundle (the project doesn't ship one today).
// Instead we lock the contract structurally:
//
//   1. The widget module imports ``useArtifactPayload`` from the new
//      hook file.
//   2. The widget module does NOT import ``runPrimitive`` (the
//      pre-PR3 path).
//   3. The render path mentions the persisted-snapshot identity
//      header so callers see WHICH snapshot the card reads from.
//
// These are grep-style assertions against the file's source bytes —
// crude but effective: any future regression that re-introduces
// ``runPrimitive`` into the persisted-widget path fails this test
// immediately, with a clear error message pointing at the file.
//
// Implementation note
// -------------------
// The project's ``tsconfig.app.json`` is browser-only (no
// ``@types/node``), so we access node's ``fs`` / ``path`` /
// ``process`` via ``globalThis`` casts.  esbuild bundles the test
// for ``--platform=node`` so the real modules ARE available at
// runtime; the casts only sidestep TypeScript's missing-types
// complaint.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodePath {
  resolve: (...paths: string[]) => string;
  dirname: (path: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}

function assertContains(haystack: string, needle: string, label: string): void {
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
// Source-file loading — dynamic imports keep TypeScript happy without
// installing @types/node for one file.
// ----------------------------------------------------------------------------

let _cachedSource: string | null = null;

async function loadRichModelWidgetSource(): Promise<string> {
  if (_cachedSource !== null) return _cachedSource;
  // @ts-ignore - node-only module; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  // Relative to the package root (the test runner cd's into the
  // ``UI/macro-copilot-dashboard-polished`` directory before bundling).
  const candidates = [
    `${cwd}/src/components/build/widgets/shared/RichModelWidget.tsx`,
    `./src/components/build/widgets/shared/RichModelWidget.tsx`,
  ];
  for (const p of candidates) {
    try {
      _cachedSource = fs.readFileSync(p, 'utf8');
      return _cachedSource;
    } catch {
      // try next
    }
  }
  throw new Error(
    `RichModelWidget.tsx not found; tried:\n  ${candidates.join('\n  ')}`,
  );
}

// All checks pull from a single shared source string so we only hit
// the filesystem once per run.  We populate the cache eagerly via a
// top-of-runner pre-fetch.
let _src = '';

check('RichModelWidget imports useArtifactPayload (PR3 plumbing)', () => {
  assertContains(
    _src,
    "import { useArtifactPayload }",
    'imports useArtifactPayload',
  );
  assertContains(
    _src,
    "from '@/hooks/useArtifactPayload'",
    'imports from the canonical hook path',
  );
});

check('RichModelWidget does NOT import runPrimitive (PR3 invariant)', () => {
  // Match any active import line — leading ``import`` keyword + the
  // identifier.  Comments / docstrings that mention runPrimitive
  // for historical context are fine, so we restrict the negation to
  // actual import statements.
  const importLines = _src
    .split('\n')
    .filter((line) => /^\s*import\b/.test(line));
  for (const line of importLines) {
    if (line.includes('runPrimitive')) {
      throw new Error(
        `RichModelWidget still imports runPrimitive — PR3 invariant violated.\n  Offending line: ${line.trim()}`,
      );
    }
  }
});

check('RichModelWidget surfaces persisted-snapshot identity in the body', () => {
  assertContains(_src, 'persisted ·', 'persisted-identity kicker present');
});

check('RichModelWidget renders typed error / loading / no-hash states', () => {
  assertContains(_src, 'LoadingState', 'loading state component');
  assertContains(_src, 'ErrorState', 'error state component');
  assertContains(_src, 'NoHashState', 'no-hash state component');
});

check('RichModelWidget propagates refetch via the error state', () => {
  assertContains(_src, 'onRetry={refetch}', 'wires refetch into error state');
});

check('RichModelWidget reads the artifact hash from node.artifact_hash first', () => {
  assertContains(
    _src,
    'node.artifact_hash',
    'reads node.artifact_hash (canonical hash source)',
  );
});

check('No accidental runPrimitive call survives anywhere in the function body', () => {
  // Stricter than the import-only check: assert no call expression
  // contains ``runPrimitive(`` either.  Comments can still mention
  // the name for historical context.
  assertNotContains(_src, 'runPrimitive(', 'no call-expression');
});

// ----------------------------------------------------------------------------
// PR1 (new plan) — adapter dispatch + no legacy Renderer prop
// ----------------------------------------------------------------------------

check('PR1: RichModelWidget consumes the per-tool adapter (adaptModelArtifact)', () => {
  assertContains(_src, 'adaptModelArtifact', 'uses adapter');
  assertContains(_src, 'getModelAdapter', 'uses metadata lookup');
  assertContains(
    _src,
    "from './persistedModelAdapters'",
    'canonical adapter path',
  );
});

check('PR1: RichModelWidget dispatches on all three adapter view variants', () => {
  assertContains(_src, "'persisted_series'", 'persisted_series branch');
  assertContains(_src, "'shape_mismatch'", 'shape_mismatch branch');
  assertContains(
    _src,
    "'pure_snapshot_unavailable'",
    'pure_snapshot_unavailable branch',
  );
});

check('PR1: RichModelWidget delegates to the per-type registry (NOT per-tool)', () => {
  // The widget MUST resolve the body via ``resolveArtifactTypeRenderer``
  // (per-type only).  Using the per-tool registry would recurse back
  // into this widget for ``(Series, calculate_pca_yield_curve_tool)``.
  assertContains(
    _src,
    'resolveArtifactTypeRenderer',
    'per-type renderer lookup',
  );
});

check('PR1: RichModelWidget renders the detail-unavailable callout', () => {
  // The "what's not in this saved snapshot" copy is the load-bearing
  // honest-fallback affordance.  Per the brief: "Do not hide missing
  // fields behind dashes."
  assertContains(
    _src,
    'Not in this saved snapshot',
    'detail-unavailable header',
  );
});

check('PR1: RichModelWidget no longer takes a Renderer prop', () => {
  // The legacy ``Renderer`` prop was the shape-mismatch vector
  // (passing the substrate-canonical payload body to a renderer
  // that expected the live ``*Output`` dict).  The new component
  // signature DOES NOT take Renderer; assert it.
  assertNotContains(
    _src,
    'Renderer: RichModelRenderer',
    'no legacy Renderer prop typedef',
  );
  assertNotContains(
    _src,
    '<Renderer output=',
    'no legacy Renderer invocation',
  );
});

// ----------------------------------------------------------------------------
// PR1 — each per-tool preview widget uses the new API
// ----------------------------------------------------------------------------

// Stage 4b — preview widgets now live in their owning module folder
// under ``src/modules/primitives/<tool_name>/surfaces/PreviewWidget.tsx``
// rather than the legacy ``src/components/build/widgets/<Name>PreviewWidget.tsx``.
// The PR1 contract assertions still apply (each preview widget must
// call ``RichModelWidget`` with the per-tool name + no Renderer prop);
// only the file-path resolution had to change.
const PREVIEW_WIDGETS_BY_TOOL: Array<{
  toolName: string;
  legacyLabel: string;
}> = [
  { toolName: 'calculate_pca_yield_curve_tool', legacyLabel: 'PcaPreviewWidget' },
  {
    toolName: 'calculate_rolling_regression_tool',
    legacyLabel: 'RollingRegressionPreviewWidget',
  },
  {
    toolName: 'calculate_yield_change_attribution_pca_tool',
    legacyLabel: 'AttributionPreviewWidget',
  },
  { toolName: 'calculate_half_life_tool', legacyLabel: 'HalfLifePreviewWidget' },
  {
    toolName: 'calculate_beta_adjusted_spread_tool',
    legacyLabel: 'BetaAdjustedSpreadPreviewWidget',
  },
];

async function loadPreviewWidgetSource(toolName: string): Promise<string> {
  // @ts-ignore - node-only.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const relPath = `src/modules/primitives/${toolName}/surfaces/PreviewWidget.tsx`;
  const candidates = [`${cwd}/${relPath}`, `./${relPath}`];
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, 'utf8');
    } catch {
      // try next
    }
  }
  throw new Error(
    `PreviewWidget for ${toolName} not found; tried:\n  ${candidates.join('\n  ')}`,
  );
}

let _previewSrc: Record<string, string> = {};

check('PR1: PcaPreviewWidget calls RichModelWidget with toolName + no Renderer', () => {
  const src = _previewSrc['calculate_pca_yield_curve_tool'];
  assertContains(
    src,
    'toolName="calculate_pca_yield_curve_tool"',
    'passes tool name',
  );
  assertNotContains(src, 'Renderer=', 'no Renderer prop');
});

check('PR1: RollingRegressionPreviewWidget calls RichModelWidget with toolName + no Renderer', () => {
  const src = _previewSrc['calculate_rolling_regression_tool'];
  assertContains(
    src,
    'toolName="calculate_rolling_regression_tool"',
    'passes tool name',
  );
  assertNotContains(src, 'Renderer=', 'no Renderer prop');
});

check('PR1: AttributionPreviewWidget calls RichModelWidget with toolName + no Renderer', () => {
  const src = _previewSrc['calculate_yield_change_attribution_pca_tool'];
  assertContains(
    src,
    'toolName="calculate_yield_change_attribution_pca_tool"',
    'passes tool name',
  );
  assertNotContains(src, 'Renderer=', 'no Renderer prop');
});

check('PR1: HalfLifePreviewWidget exists + uses adapter via RichModelWidget', () => {
  const src = _previewSrc['calculate_half_life_tool'];
  assertContains(
    src,
    'toolName="calculate_half_life_tool"',
    'half-life tool name',
  );
  assertContains(src, 'RichModelWidget', 'uses RichModelWidget');
});

check('PR1: BetaAdjustedSpreadPreviewWidget exists + uses adapter via RichModelWidget', () => {
  const src = _previewSrc['calculate_beta_adjusted_spread_tool'];
  assertContains(
    src,
    'toolName="calculate_beta_adjusted_spread_tool"',
    'beta-adj tool name',
  );
  assertContains(src, 'RichModelWidget', 'uses RichModelWidget');
});

check('PR1: no preview widget retains stale "re-runs the tool" docstring', () => {
  for (const { toolName, legacyLabel } of PREVIEW_WIDGETS_BY_TOOL) {
    const src = _previewSrc[toolName];
    assertNotContains(src, 're-runs the tool', `${legacyLabel}: stale re-run comment`);
    assertNotContains(
      src,
      'until the backend ships a payload',
      `${legacyLabel}: stale backend comment`,
    );
  }
});

// ----------------------------------------------------------------------------
// Runner — async so it can await the one-shot source load.
// ----------------------------------------------------------------------------

export async function runAllRichModelWidgetContractTests(): Promise<void> {
  _src = await loadRichModelWidgetSource();
  // Eagerly load every preview-widget source for the PR1 checks
  // that need them.  Stage 4b — keyed by canonical tool name; the
  // file lives in the owning module's surfaces/ folder.
  for (const { toolName } of PREVIEW_WIDGETS_BY_TOOL) {
    _previewSrc[toolName] = await loadPreviewWidgetSource(toolName);
  }
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
    `\nRichModelWidget contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} RichModelWidget contract check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllRichModelWidgetContractTests();
}
