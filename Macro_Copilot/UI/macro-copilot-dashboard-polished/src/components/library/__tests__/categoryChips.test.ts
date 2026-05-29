// ============================================================================
// categoryChips.test.ts — lock the Stage 4f CATEGORY_ORDER derivation.
// ----------------------------------------------------------------------------
// Pre-Stage-4f the Library Category chip row hard-coded a 7-entry
// `CATEGORY_ORDER`, so every category Stage 1 added to the backend
// manifests (panels, scanners, spreads, economic_release_surprises,
// meeting_pricing, aggregates, panel_assembly) was silently dropped
// from the chip row.  Stage 4f changed `CategoryChips` to derive
// the order from `CATEGORY_LABELS` (pinned-priority first, then every
// remaining label key alphabetically).  Stage 4g locks that contract
// in a structural test so a future regression that re-introduces a
// closed hard-coded list fails loudly.
//
// The test reads the source file as bytes (same pattern as the
// build-folder structural locks — `regressionLock.test.ts`,
// `richModelWidgetContract.test.ts`).  This avoids needing a browser
// renderer for what is fundamentally a derivation-shape claim.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

import { CATEGORY_LABELS } from '@/types/library';

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];

function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
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

async function loadCategoryChipsSource(): Promise<string> {
  // @ts-ignore - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const candidates = [
    `${cwd}/src/components/library/CategoryChips.tsx`,
    `./src/components/library/CategoryChips.tsx`,
  ];
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, 'utf8');
    } catch {
      // try next
    }
  }
  throw new Error(
    `CategoryChips.tsx not found; tried:\n  ${candidates.join('\n  ')}`,
  );
}

let _src = '';

check('CategoryChips derives CATEGORY_ORDER from CATEGORY_LABELS (no closed hard-coded list)', () => {
  // The Stage 4f fix wires CATEGORY_ORDER from CATEGORY_LABELS keys.
  // The pre-Stage-4f hard-coded list lived alongside a `const`
  // declaration that listed all 7 names in array form.  Lock both
  // directions: the derivation expression MUST appear, and the
  // pre-Stage-4f closed shape MUST NOT.
  assertContains(
    _src,
    'Object.keys(CATEGORY_LABELS)',
    'derives from CATEGORY_LABELS keys',
  );
  // Pinned-priority list still lives in the file (it's the order
  // override), but it must be a SUBSET of CATEGORY_LABELS, not the
  // closed set.  Lock the pinned name.
  assertContains(_src, 'PINNED_CATEGORIES', 'pinned-priority constant exists');
  // The pre-Stage-4f comment / structure said "the 7 functional
  // categories"; that framing must be gone.
  assertNotContains(
    _src,
    'the 7 functional categories',
    'no pre-Stage-4f closed-list framing',
  );
});

check('Pinned categories appear in PINNED_CATEGORIES in canonical order', () => {
  // The pinned-priority list is the original visual-brief order.
  // Any reorder is an intentional product decision; the test locks
  // it so a stray sort() doesn't shuffle the chips silently.
  const pinned = [
    'snapshots',
    'curve_shape',
    'cross_market_rv',
    'forwards_classify',
    'screening',
    'rolling_analytics',
    'model_fits',
  ];
  // Find the PINNED_CATEGORIES literal.
  const match = _src.match(/PINNED_CATEGORIES\s*=\s*\[([\s\S]*?)\]/);
  if (!match) {
    throw new Error('PINNED_CATEGORIES literal not found in CategoryChips.tsx');
  }
  const body = match[1];
  const found = [...body.matchAll(/'([^']+)'/g)].map((m) => m[1]);
  for (let i = 0; i < pinned.length; i += 1) {
    if (found[i] !== pinned[i]) {
      throw new Error(
        `Pinned order drift at index ${i}: expected '${pinned[i]}', got '${found[i]}'`,
      );
    }
  }
});

check('Every CATEGORY_LABELS key is reachable as a chip', () => {
  // Stage 4f's contract: a new category in CATEGORY_LABELS reaches
  // the chip row without any further edit.  We simulate the
  // derivation here (same expression the source file uses) and
  // assert every CATEGORY_LABELS key appears in the computed order.
  const pinned = [
    'snapshots',
    'curve_shape',
    'cross_market_rv',
    'forwards_classify',
    'screening',
    'rolling_analytics',
    'model_fits',
  ];
  const pinnedSet = new Set(pinned);
  const allKeys = Object.keys(CATEGORY_LABELS);
  const order = [
    ...pinned.filter((c) => c in CATEGORY_LABELS),
    ...allKeys.filter((c) => !pinnedSet.has(c)).sort(),
  ];
  for (const key of allKeys) {
    if (!order.includes(key)) {
      throw new Error(
        `CATEGORY_LABELS key '${key}' not reachable via derived CATEGORY_ORDER`,
      );
    }
  }
  // Stage 1 categories the pre-Stage-4f hardcoded list dropped —
  // explicit smoke check that they're back.
  for (const k of [
    'panels',
    'scanners',
    'spreads',
    'economic_release_surprises',
    'meeting_pricing',
    'aggregates',
    'panel_assembly',
  ]) {
    assertTruthy(
      order.includes(k),
      `Stage 1 category '${k}' present in derived CATEGORY_ORDER`,
    );
  }
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllCategoryChipsTests(): Promise<void> {
  _src = await loadCategoryChipsSource();
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
    `\ncategory-chips coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} category-chips check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllCategoryChipsTests();
}
