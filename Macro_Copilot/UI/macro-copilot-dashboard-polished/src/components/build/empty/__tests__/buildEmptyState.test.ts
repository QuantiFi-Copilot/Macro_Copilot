// ============================================================================
// buildEmptyState.test.ts — PR3 entrypoint-routing contract.
// ----------------------------------------------------------------------------
// Locks the load-bearing PR3 invariants on the empty-state grid:
//
//   1. Every tile is either ACTIVE (clickable) or honestly disabled
//      with a ``soonReason`` caption — never a half-shipped path.
//   2. Active tiles either ``builderTool`` (deep-link to the model
//      builder canvas) OR carry a non-empty ``promptSeed`` (composer
//      seed → supervisor routing).  Never both, never neither.
//   3. The custom-DAG tile is disabled with an explicit reason — it
//      is NOT a runnable composer.  PR3 also asserts its
//      ``promptSeed`` is empty (the pre-PR3 placeholder was dead +
//      misleading).
//   4. The CategoryTile component renders ``disabled`` on
//      ``isSoon`` tiles so the click handler can't fire.
//
// These checks run against the source files via the same fs-shim
// pattern PR1 / PR7 / PR2 use.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

import { BUILD_CATEGORIES } from '../categoryDefinitions';
import type { BuildEmptyCategory } from '../../lib/buildTypes';

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

function assertFalsy(value: unknown, label: string): void {
  if (value) throw new Error(`assertFalsy failed: ${label}`);
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
      // try next candidate
    }
  }
  throw new Error(`Could not read ${relativePath}; cwd=${cwd}`);
}

// ----------------------------------------------------------------------------
// Per-tile routing invariants
// ----------------------------------------------------------------------------

check('categories: at least 4 entries', () => {
  // Sanity — the empty state ships with 6 tiles in Mockup A.  Don't
  // need to assert exactly 6, but require enough to populate the
  // grid.
  assertTruthy(BUILD_CATEGORIES.length >= 4, 'enough tiles');
});

check('categories: every entry has stable id + label', () => {
  const seen = new Set<string>();
  for (const c of BUILD_CATEGORIES) {
    assertTruthy(c.id, `id present for ${c.label}`);
    assertTruthy(c.label, `label present for ${c.id}`);
    assertFalsy(seen.has(c.id), `id ${c.id} is unique`);
    seen.add(c.id);
  }
});

check('categories: soon tiles carry a soonReason', () => {
  for (const c of BUILD_CATEGORIES) {
    if (c.soon) {
      assertTruthy(
        c.soonReason && c.soonReason.length >= 16,
        `${c.id} has a meaningful soonReason`,
      );
    }
  }
});

check('categories: every active tile is routable (builderTool OR promptSeed)', () => {
  // A tile is "active" when ``soon !== true``.  Active tiles must
  // have a real route — either a deep-link to a builder, or a
  // non-empty composer seed.  Catches half-wired tiles that disable
  // themselves but still pretend to be clickable.
  for (const c of BUILD_CATEGORIES) {
    if (c.soon) continue;
    const hasBuilder = typeof c.builderTool === 'string' && c.builderTool.length > 0;
    const hasSeed = typeof c.promptSeed === 'string' && c.promptSeed.length > 0;
    assertTruthy(
      hasBuilder || hasSeed,
      `${c.id} has a builderTool or non-empty promptSeed`,
    );
  }
});

check('categories: build_custom_dag is disabled with empty seed', () => {
  const dag = BUILD_CATEGORIES.find((c: BuildEmptyCategory) => c.id === 'build_custom_dag');
  assertTruthy(dag, 'tile exists');
  assertEqual(dag!.soon, true, 'soon=true');
  assertEqual(dag!.promptSeed, '', 'promptSeed empty (pre-PR3 dead text removed)');
  assertTruthy(
    (dag!.soonReason || '').toLowerCase().includes('phase 2') ||
      (dag!.soonReason || '').toLowerCase().includes('tier 2'),
    'soonReason references the deferring milestone',
  );
});

check('categories: builderTool values look like tool_name (snake_case)', () => {
  for (const c of BUILD_CATEGORIES) {
    if (!c.builderTool) continue;
    assertTruthy(
      /^[a-z][a-z0-9_]+_tool$/.test(c.builderTool),
      `${c.id}'s builderTool "${c.builderTool}" matches the registered-tool naming convention`,
    );
  }
});

// ----------------------------------------------------------------------------
// CategoryTile source contract — disabled when soon, dispatches when active
// ----------------------------------------------------------------------------

check('CategoryTile: disables itself when isSoon', async () => {
  const src = await loadSource(
    'src/components/build/empty/CategoryTile.tsx',
  );
  if (!src.includes('disabled={isSoon}')) {
    throw new Error(
      'CategoryTile must pass ``disabled={isSoon}`` to its <button> so disabled tiles never fire onSelect',
    );
  }
  if (!src.includes('onSelect(category)')) {
    throw new Error('CategoryTile must dispatch onSelect on click');
  }
});

check('BuildEmptyState: routes builderTool tiles to /workspace?builder=…', async () => {
  const src = await loadSource(
    'src/components/build/empty/BuildEmptyState.tsx',
  );
  if (!src.includes('category.builderTool')) {
    throw new Error(
      'BuildEmptyState must branch on category.builderTool to deep-link the builder canvas',
    );
  }
  if (!src.includes('builder=')) {
    throw new Error(
      'BuildEmptyState must navigate to /workspace?builder=… for builderTool tiles',
    );
  }
  if (!src.includes('composerRef.current?.seed')) {
    throw new Error(
      'BuildEmptyState must seed the composer for non-builderTool active tiles',
    );
  }
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllBuildEmptyStateTests(): Promise<void> {
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
    `\nbuild-empty-state coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} build-empty-state check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllBuildEmptyStateTests();
}
