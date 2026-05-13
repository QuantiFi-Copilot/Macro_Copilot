#!/usr/bin/env node
// ============================================================================
// scripts/run_build_tests.mjs — PR8 build-folder test runner.
// ----------------------------------------------------------------------------
// Bundles every ``.test.ts`` file under ``src/components/build/**/__tests__/``
// via esbuild and runs the resulting ESM bundles with Node.  Replaces
// the ad-hoc bash loops that PR1–PR7 used per-PR and gives reviewers
// a single command:
//
//   node scripts/run_build_tests.mjs
//
// (or ``npm run test:build`` once the package.json script lands —
// see PR8.)
//
// What this runner does NOT do
// ----------------------------
// - It is NOT a unit-test framework.  Every test file already
//   self-contains a ``check()`` shim + assertion helpers (the
//   pattern PR1 established because the repo doesn't ship vitest /
//   jest / playwright).  This script just orchestrates them.
// - It is NOT a coverage tool.  ``tsc --noEmit`` already provides
//   the typed-coverage gate.
//
// Why a Node script rather than a test framework
// ----------------------------------------------
// Codex's PR8 brief: "do not add a heavy new testing dependency only
// for this PR unless the repo already supports it."  The repo ships
// no test runner today; the existing pattern (esbuild bundle →
// Node import + run) is intentionally lightweight.
// ============================================================================

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, basename } from 'node:path';
import { pathToFileURL } from 'node:url';

const PROJECT_ROOT = process.cwd();
const SRC_BUILD = join(PROJECT_ROOT, 'src/components/build');
const ESBUILD = join(PROJECT_ROOT, 'node_modules/.bin/esbuild');

const DEFINE_ENV =
  '--define:import.meta.env={"VITE_API_BASE_URL":"http://localhost:8000"}';
const ALIAS_AT = `--alias:@=${join(PROJECT_ROOT, 'src')}`;

// ----------------------------------------------------------------------------
// Discover every test file
// ----------------------------------------------------------------------------

/** Recursively find every ``*.test.ts`` under ``__tests__`` directories. */
function findTestFiles(root) {
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
      } else if (s.isFile() && name.endsWith('.test.ts')) {
        out.push(full);
      }
    }
  }
  walk(root);
  return out;
}

// ----------------------------------------------------------------------------
// Bundle a single .test.ts file with esbuild
// ----------------------------------------------------------------------------

function bundle(testPath, outDir) {
  const out = join(outDir, `${basename(testPath).replace('.test.ts', '')}.js`);
  const args = [
    testPath,
    '--bundle',
    '--platform=node',
    '--format=esm',
    '--target=es2022',
    '--log-level=error', // suppress esbuild's per-bundle "Done" status
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
// Run a bundled test file and report its console output
// ----------------------------------------------------------------------------

async function runBundle(bundlePath) {
  // Every PR1–PR8 test file exports ONE ``runAll*Tests`` function and
  // wires it to a ``import.meta.main`` guard.  We import the bundle
  // and call the exported function — that prints the per-file
  // "X passed, Y failed" summary the runner aggregates.
  const url = pathToFileURL(bundlePath).href;
  const mod = await import(url);
  for (const name of Object.keys(mod)) {
    if (typeof mod[name] === 'function' && name.startsWith('runAll')) {
      const res = mod[name]();
      // Some test files are async (``runAll*ContractTests`` reads
      // sources via dynamic ``import('fs')``).  Wait on them.
      if (res && typeof res.then === 'function') {
        await res;
      }
      return;
    }
  }
  throw new Error(`no runAll* export found in ${bundlePath}`);
}

// ----------------------------------------------------------------------------
// Orchestrator
// ----------------------------------------------------------------------------

async function main() {
  const testFiles = findTestFiles(SRC_BUILD).sort();
  if (testFiles.length === 0) {
    console.error('No .test.ts files found under src/components/build/');
    process.exit(2);
  }

  const tmpDir = mkdtempSync(join(tmpdir(), 'build-tests-'));
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

  // Run phase — sequential so the per-file summaries are readable.
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
  console.log('\nall build-folder tests passed');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
