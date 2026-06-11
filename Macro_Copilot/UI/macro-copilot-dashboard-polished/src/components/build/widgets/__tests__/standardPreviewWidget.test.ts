/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// standardPreviewWidget.test.ts — contract for the shared persisted-slug
// card used by the standard dual-view primitive tools (Codex-flag fix).
// ----------------------------------------------------------------------------
// Source-level invariants (same pattern as widgetContract.test.ts — the
// repo ships no vitest, so widgets are locked by reading their source).
//
// The two load-bearing invariants:
//
//   1. DETERMINISM (P4).  StandardPreviewWidget renders the SAVED artifact
//      read-only by hash (PayloadShell → useArtifactPayload).  It must NOT
//      live-refetch the typed-detail endpoint the live ``buildCompact``
//      uses (``fetchDetail`` / ``services/ratesApi``) — that would pull
//      today's numbers into a saved workspace.
//
//   2. WIRING.  widgets/index.ts registers StandardPreviewWidget ONLY for
//      modules that ship ``buildCompact`` and have NO bespoke ``preview``
//      — so rich-model preview surfaces are never overridden and operator
//      nodes (no owning module) keep the generic per-type widget.
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
// Source loader
// ----------------------------------------------------------------------------

let _sourceCache: Record<string, string> | null = null;

async function loadSources(): Promise<Record<string, string>> {
  if (_sourceCache) return _sourceCache;
  // @ts-ignore - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const files: Record<string, string[]> = {
    'StandardPreviewWidget.tsx': [
      `${cwd}/src/components/build/widgets/shared/StandardPreviewWidget.tsx`,
      `./src/components/build/widgets/shared/StandardPreviewWidget.tsx`,
    ],
    'index.ts': [
      `${cwd}/src/components/build/widgets/index.ts`,
      `./src/components/build/widgets/index.ts`,
    ],
  };
  const out: Record<string, string> = {};
  for (const [key, candidates] of Object.entries(files)) {
    let loaded = false;
    for (const p of candidates) {
      try {
        out[key] = fs.readFileSync(p, 'utf8');
        loaded = true;
        break;
      } catch {
        // try next
      }
    }
    if (!loaded) throw new Error(`Could not read source ${key}; cwd=${cwd}`);
  }
  _sourceCache = out;
  return out;
}

let SRC: Record<string, string> = {};

// ----------------------------------------------------------------------------
// Invariant 1 — determinism (read-only by hash; no live refetch)
// ----------------------------------------------------------------------------

check('StandardPreviewWidget loads the saved payload via PayloadShell', () => {
  const src = SRC['StandardPreviewWidget.tsx'];
  assertContains(src, "import { PayloadShell }", 'imports PayloadShell');
  assertContains(src, 'artifact_hash', 'reads the artifact hash (by-hash load)');
});

check('StandardPreviewWidget does NOT live-refetch (P4 determinism)', () => {
  const src = SRC['StandardPreviewWidget.tsx'];
  assertNotContains(src, 'fetchDetail', 'no live typed-detail fetch');
  assertNotContains(src, 'services/ratesApi', 'no ratesApi (live) import');
});

check('StandardPreviewWidget resolves tool identity from the registry', () => {
  const src = SRC['StandardPreviewWidget.tsx'];
  assertContains(src, 'getPrimitiveModule', 'reads module display metadata');
});

// ----------------------------------------------------------------------------
// Invariant 2 — wiring (only standard tools; never overrides preview/operators)
// ----------------------------------------------------------------------------

check('index.ts wires StandardPreviewWidget into the registry walker', () => {
  const src = SRC['index.ts'];
  assertContains(
    src,
    "import { StandardPreviewWidget }",
    'imports the shared widget',
  );
  assertContains(
    src,
    'StandardPreviewWidget as NodeRenderer',
    'registers it as a NodeRenderer',
  );
});

check('walker skips modules that already declare a bespoke preview', () => {
  const src = SRC['index.ts'];
  assertContains(
    src,
    'if (m.surfaces?.preview) continue',
    'rich-model previews win (not overridden)',
  );
});

check('walker only applies to modules that ship buildCompact', () => {
  const src = SRC['index.ts'];
  assertContains(
    src,
    'if (!m.surfaces?.buildCompact) continue',
    'standard dual-view tools only (operators excluded)',
  );
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllStandardPreviewTests(): Promise<void> {
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
    `\nStandardPreviewWidget contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} StandardPreviewWidget check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllStandardPreviewTests();
}
