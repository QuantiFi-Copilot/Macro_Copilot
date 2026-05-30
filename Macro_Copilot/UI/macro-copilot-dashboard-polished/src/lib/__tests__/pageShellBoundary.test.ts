/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// pageShellBoundary.test.ts — Stage 2 FP12 enforcement (grep-based).
// ----------------------------------------------------------------------------
// Stage 2 — Build the src/modules/ infrastructure.  This test enforces
// the FP12 page-shell minimality rule via a static grep over source
// files.  An ESLint custom rule would be the eventual home but the
// repo doesn't ship ESLint today; a test that walks the source tree
// is the lightest-weight way to encode the same invariant without
// introducing a new toolchain dependency.
//
// Doctrine reference
// ------------------
// FP12 — Page-shell minimality (docs_revamped/00_thesis/03_frontend_thesis.md):
//   "A page-shell file MAY import shared infrastructure and module-
//    spec lookups; it MAY NOT import a specific module's surface
//    file directly.  The router-shaped lookup is the only way a
//    shell touches a module."
//
// What this test asserts
// ----------------------
// For every .tsx / .ts file under the page-shell roots
// (``src/components/{build,library,monitor,ask,layout}``), any import
// from ``@/modules/...`` MUST be either:
//   * a TYPE-only import from the loader barrel (``import type``)
//   * a value import from the loader's lookup helpers
//     (``getPrimitiveModule`` / ``getWorkflowModule``) or top-level
//     re-exports (the barrel itself, ``ALL_PRIMITIVE_MODULES``, etc.)
//
// Direct imports from ``@/modules/primitives/<name>/...`` or
// ``@/modules/workflows/<name>/...`` are forbidden in page-shell files.
//
// What this test does NOT assert
// ------------------------------
// * Test files under ``__tests__/`` are exempt — tests legitimately
//   need to import specific module surfaces for assertions.
// * Storybook stories (``*.stories.tsx``) are exempt for the same
//   reason once they ship.
// * Files OUTSIDE the page-shell roots can import freely; this rule
//   is specifically about preventing per-primitive code from leaking
//   into the page shells.
//
// Stage 2 reality: ``src/modules/`` is empty.  No imports from it
// exist anywhere yet.  The test currently passes trivially; from
// Stage 4a onward (when refactored modules first start being imported)
// the rule binds in earnest.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
  readdirSync: (path: string) => string[];
  statSync: (path: string) => {
    isDirectory: () => boolean;
    isFile: () => boolean;
  };
  existsSync: (path: string) => boolean;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];

function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
}

async function loadFs(): Promise<NodeFs> {
  // @ts-ignore - node-only.
  return (await import('fs')) as NodeFs;
}

function cwd(): string {
  const _g = globalThis as unknown as NodeGlobal;
  return _g.process?.cwd?.() ?? '.';
}

// ---------------------------------------------------------------------------
// Page-shell roots (closed list — adding a new shell root requires
// updating this entry consciously so the FP12 rule binds the new
// shell too).
// ---------------------------------------------------------------------------

const PAGE_SHELL_ROOTS: ReadonlyArray<string> = [
  'src/components/build',
  'src/components/library',
  'src/components/monitor',
  'src/components/ask',
  'src/components/layout',
];

// ---------------------------------------------------------------------------
// File discovery.
// ---------------------------------------------------------------------------

async function walkSource(
  root: string,
): Promise<string[]> {
  const fs = await loadFs();
  const out: string[] = [];
  function rec(dir: string): void {
    let entries: string[];
    try {
      entries = fs.readdirSync(dir);
    } catch {
      return;
    }
    for (const name of entries) {
      const full = `${dir}/${name}`;
      let s;
      try {
        s = fs.statSync(full);
      } catch {
        continue;
      }
      if (s.isDirectory()) {
        // Skip test directories — tests legitimately need direct
        // module imports for assertions.
        if (name === '__tests__') continue;
        rec(full);
        continue;
      }
      if (!s.isFile()) continue;
      // Skip *.stories.tsx (Storybook) and *.test.ts (tests) by
      // suffix in case they live outside __tests__/ folders.
      if (name.endsWith('.stories.tsx')) continue;
      if (name.endsWith('.test.ts') || name.endsWith('.test.tsx')) continue;
      if (
        name.endsWith('.ts') ||
        name.endsWith('.tsx') ||
        name.endsWith('.js') ||
        name.endsWith('.jsx')
      ) {
        out.push(full);
      }
    }
  }
  rec(root);
  return out.sort();
}

// ---------------------------------------------------------------------------
// The forbidden-import pattern.
// ---------------------------------------------------------------------------
//
// Direct module-folder imports look like:
//   import X from '@/modules/primitives/calculate_foo_tool/surfaces/MonitorWidget';
//   import { Y } from '@/modules/workflows/event_study/surfaces/ResultsDashboard';
//
// Allowed imports from the loader barrel:
//   import { getPrimitiveModule } from '@/modules';
//   import type { PrimitiveModuleSpec } from '@/modules';
//   import { ALL_PRIMITIVE_MODULES } from '@/modules';
//
// The regex flags any ``from '@/modules/<something else>/'`` —
// anything past the bare ``@/modules`` import path.

const FORBIDDEN_IMPORT_RE =
  /\bfrom\s+['"]@\/modules\/(primitives|workflows)\//;

interface Violation {
  file: string;
  line: number;
  text: string;
}

async function findViolations(): Promise<Violation[]> {
  const fs = await loadFs();
  const root = cwd();
  const violations: Violation[] = [];
  for (const shellRoot of PAGE_SHELL_ROOTS) {
    const abs = `${root}/${shellRoot}`;
    if (!fs.existsSync(abs)) continue;
    const files = await walkSource(abs);
    for (const f of files) {
      const text = fs.readFileSync(f, 'utf8');
      const lines = text.split('\n');
      lines.forEach((line, idx) => {
        if (FORBIDDEN_IMPORT_RE.test(line)) {
          violations.push({
            file: f.slice(root.length + 1),
            line: idx + 1,
            text: line.trim(),
          });
        }
      });
    }
  }
  return violations;
}

// ---------------------------------------------------------------------------
// Checks.
// ---------------------------------------------------------------------------

check('FP12: no page-shell file imports a specific module surface', async () => {
  const violations = await findViolations();
  if (violations.length > 0) {
    const summary = violations
      .map((v) => `  ${v.file}:${v.line}\n    ${v.text}`)
      .join('\n');
    throw new Error(
      `FP12 violation — page-shell files imported a specific module ` +
        `surface directly (forbidden by the page-shell minimality rule).  ` +
        `Use 'getPrimitiveModule(toolName).surfaces.<key>' from '@/modules' ` +
        `instead:\n${summary}`,
    );
  }
});

check('page-shell roots all exist (closed list integrity)', async () => {
  // If a page-shell root is renamed without updating PAGE_SHELL_ROOTS
  // the FP12 enforcement silently goes blind.  This sanity check
  // catches the rename.  Empty directories are acceptable; a missing
  // path is the failure mode.
  const fs = await loadFs();
  const root = cwd();
  for (const shellRoot of PAGE_SHELL_ROOTS) {
    const abs = `${root}/${shellRoot}`;
    assertTruthy(fs.existsSync(abs), `shell root exists: ${shellRoot}`);
  }
});

// ---------------------------------------------------------------------------
// Runner.
// ---------------------------------------------------------------------------

export async function runAllPageShellBoundaryTests(): Promise<void> {
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
    `\npage-shell boundary coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} page-shell boundary check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllPageShellBoundaryTests();
}
