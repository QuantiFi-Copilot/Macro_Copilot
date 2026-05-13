/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// richModelWidgetContract.test.ts — structural assertions on the persisted-
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
  // @ts-expect-error - node-only module; esbuild --platform=node resolves it.
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
  assertContains(
    _src,
    '/artifacts/',
    'footer mentions the /artifacts endpoint origin',
  );
});

check('RichModelWidget renders typed error / loading / no-hash states', () => {
  assertContains(_src, 'LoadingState', 'loading state component');
  assertContains(_src, 'ErrorState', 'error state component');
  assertContains(_src, 'NoHashState', 'no-hash state component');
});

check('RichModelWidget propagates refetch via the error state', () => {
  assertContains(_src, 'onRetry={refetch}', 'wires refetch into error state');
});

check(
  'RichModelWidget exposes the (legacy compat) output dict to the Renderer',
  () => {
    assertContains(
      _src,
      'data.payload',
      'passes the payload body through to the Renderer for back-compat',
    );
  },
);

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
// Runner — async so it can await the one-shot source load.
// ----------------------------------------------------------------------------

export async function runAllRichModelWidgetContractTests(): Promise<void> {
  _src = await loadRichModelWidgetSource();
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
