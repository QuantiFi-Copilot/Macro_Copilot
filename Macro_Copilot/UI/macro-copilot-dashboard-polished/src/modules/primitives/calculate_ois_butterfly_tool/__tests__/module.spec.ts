/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/calculate_ois_butterfly_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Stage 3 per-module round-trip — calls assertStandardModuleInvariants
// from src/modules/__test-utils.ts.  Catches FM11 invariants 1-8 in
// one check.  Identical boilerplate across every module; per-module
// customisation belongs in additional ``check(...)`` blocks below.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'calculate_ois_butterfly_tool';

interface NodeGlobal { process?: { cwd?: () => string } }

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];

function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

function cwd(): string {
  const _g = globalThis as unknown as NodeGlobal;
  return _g.process?.cwd?.() ?? '.';
}

check('module satisfies the standard invariants', async () => {
  await assertStandardModuleInvariants(MODULE, {
    folderName: FOLDER,
    moduleFolderPath: `${cwd()}/src/modules/primitives/${FOLDER}`,
  });
});

export async function runAllModuleSpecTests(): Promise<void> {
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
  console.log(`\n${FOLDER}: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} ${FOLDER} check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllModuleSpecTests();
}
