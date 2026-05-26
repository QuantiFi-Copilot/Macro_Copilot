#!/usr/bin/env node
// ============================================================================
// scripts/run_module_tests.mjs — Stage 2 module + registry test runner.
// ----------------------------------------------------------------------------
// Sibling of ``scripts/run_build_tests.mjs`` but walks the Stage 2
// module-and-registry test paths:
//
//   * src/lib/__tests__/         — registry-derivation + loader-presence
//                                   tests (Stage 2's loaderPresence.test.ts +
//                                   pageShellBoundary.test.ts).
//   * src/modules/**/__tests__/  — per-module round-trip tests (Stage 3+).
//
// Same bundle-with-esbuild + import-and-call pattern as the build runner;
// just walks different roots.
//
// Why a separate runner rather than extending the existing one
// ------------------------------------------------------------
// The existing ``run_build_tests.mjs`` has a hard-coded ``SRC_BUILD``
// path and exits 2 when zero test files are found.  Both are correct
// for the build-folder runner.  This script:
//   * Walks MULTIPLE roots (the registry tests live in src/lib, the
//     module tests in src/modules).
//   * Tolerates an empty file set as success.  Stage 2 ships zero
//     module folders; ``test:modules`` correctly succeeds.  Once
//     Stage 3+ lands modules with their per-folder __tests__, the
//     runner picks them up automatically.
//
// Backwards compatibility
// -----------------------
// This file is NEW; it touches nothing in the existing runner.
// ``npm run test:build`` continues to invoke the unchanged
// ``run_build_tests.mjs`` against ``src/components/build`` exactly as
// before.
// ============================================================================

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const PROJECT_ROOT = process.cwd();
const SRC_ROOTS = [
  join(PROJECT_ROOT, 'src/lib/__tests__'),
  join(PROJECT_ROOT, 'src/modules'),
  // Stage 4g — page-shell structural tests that lock module-derivation
  // contracts (e.g. CategoryChips deriving CATEGORY_ORDER from
  // CATEGORY_LABELS instead of hard-coding a closed list).
  join(PROJECT_ROOT, 'src/components/library/__tests__'),
];
const ESBUILD = join(PROJECT_ROOT, 'node_modules/.bin/esbuild');

const DEFINE_ENV =
  '--define:import.meta.env={"VITE_API_BASE_URL":"http://localhost:8000"}';
const ALIAS_AT = `--alias:@=${join(PROJECT_ROOT, 'src')}`;

// ----------------------------------------------------------------------------
// File discovery — walk every root recursively, collect *.test.ts.
// ----------------------------------------------------------------------------

function findTestFiles(roots) {
  // Stage 3 extension — discovers BOTH ``*.test.ts`` (Stage 2 lib
  // tests) AND ``*.spec.ts`` (per-module round-trip tests, named
  // per the FM11 contract in
  // ``docs_revamped/02_components/frontend_module/README.md``).
  const out = [];
  function walk(dir) {
    let entries;
    try {
      entries = readdirSync(dir);
    } catch {
      return;
    }
    for (const name of entries) {
      const full = join(dir, name);
      const s = statSync(full);
      if (s.isDirectory()) {
        walk(full);
      } else if (
        s.isFile() &&
        (name.endsWith('.test.ts') || name.endsWith('.spec.ts'))
      ) {
        out.push(full);
      }
    }
  }
  for (const root of roots) walk(root);
  return out.sort();
}

// ----------------------------------------------------------------------------
// Bundle one .test.ts file with esbuild — identical to run_build_tests.mjs.
// ----------------------------------------------------------------------------

function bundle(testPath, outDir) {
  // Stage 4e fix — every per-module test is named ``module.spec.ts``,
  // so ``basename(testPath).replace('.test.ts','')`` produced the same
  // output filename for all 58 modules and they overwrote each other
  // (only the last bundled spec actually ran).  Use the relative path
  // from PROJECT_ROOT, slashes → underscores, AND handle both .test.ts
  // and .spec.ts suffixes, so every test compiles to a UNIQUE output.
  const rel = testPath.startsWith(PROJECT_ROOT + '/')
    ? testPath.slice(PROJECT_ROOT.length + 1)
    : testPath;
  const stem = rel
    .replace(/\.test\.ts$/, '')
    .replace(/\.spec\.ts$/, '')
    .replace(/[\/\\]/g, '_');
  const out = join(outDir, `${stem}.js`);
  const args = [
    testPath,
    '--bundle',
    '--platform=node',
    '--format=esm',
    '--target=es2022',
    '--log-level=error',
    ALIAS_AT,
    '--external:node:*',
    '--external:fs',
    '--external:path',
    DEFINE_ENV,
    `--outfile=${out}`,
  ];
  try {
    execFileSync(ESBUILD, args, { stdio: ['ignore', 'ignore', 'inherit'] });
  } catch (err) {
    console.error(`× bundle failed: ${testPath}`);
    throw err;
  }
  return out;
}

// ----------------------------------------------------------------------------
// Run a bundled test — same convention as run_build_tests.mjs.
// ----------------------------------------------------------------------------

async function runBundle(bundlePath) {
  const url = pathToFileURL(bundlePath).href;
  const mod = await import(url);
  for (const name of Object.keys(mod)) {
    if (typeof mod[name] === 'function' && name.startsWith('runAll')) {
      const res = mod[name]();
      if (res && typeof res.then === 'function') await res;
      return;
    }
  }
  throw new Error(`no runAll* export found in ${bundlePath}`);
}

// ----------------------------------------------------------------------------
// Orchestrator.
// ----------------------------------------------------------------------------

async function main() {
  const testFiles = findTestFiles(SRC_ROOTS);

  // Stage 2 reality: with zero modules, the only test files are the
  // Stage 2 registry tests in src/lib/__tests__/.  Empty-set tolerance
  // (return success) is correct for future stages where a sub-tree
  // might temporarily contain no tests.
  if (testFiles.length === 0) {
    console.log(
      '\nno module/registry test files found under:\n  ' +
        SRC_ROOTS.map((r) => r.slice(PROJECT_ROOT.length + 1)).join('\n  ') +
        '\n(empty set is a soft success — modules land per the Stage 3+ schedule)\n',
    );
    return;
  }

  const tmpDir = mkdtempSync(join(tmpdir(), 'module-tests-'));
  console.log(
    `\nfound ${testFiles.length} test file(s); bundling into ${tmpDir}`,
  );

  // Bundle phase.
  const bundles = [];
  for (const testPath of testFiles) {
    const rel = testPath.slice(PROJECT_ROOT.length + 1);
    process.stdout.write(`  · ${rel} … `);
    const t0 = Date.now();
    const out = bundle(testPath, tmpDir);
    process.stdout.write(`${Date.now() - t0}ms\n`);
    bundles.push({ src: rel, out });
  }

  // Run phase.
  let totalFailed = 0;
  console.log(`\nrunning ${bundles.length} bundle(s)…\n`);
  for (const { src, out } of bundles) {
    try {
      await runBundle(out);
    } catch (err) {
      totalFailed += 1;
      console.error(`\n× ${src}: ${(err && err.message) || err}\n`);
    }
  }

  if (totalFailed > 0) {
    console.error(`\n${totalFailed} test file(s) failed`);
    process.exit(1);
  }
  console.log('\nall module + registry tests passed');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
