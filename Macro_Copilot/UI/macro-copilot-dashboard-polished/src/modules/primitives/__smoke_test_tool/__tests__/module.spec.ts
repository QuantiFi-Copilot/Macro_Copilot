// ============================================================================
// __smoke_test_tool/__tests__/module.spec.ts — Stage 5 round-trip.
// ----------------------------------------------------------------------------
// The smoke module ships every capability surface AND the paused
// runtime tier.  The standard invariant helper covers tier-↔-surface
// consistency, FM6 unsupportedReason gating, FM11 toolName-↔-folder,
// and the rest.  This file is the same boilerplate every primitive
// module's round-trip test uses.
// ============================================================================

import { assertStandardModuleInvariants } from '@/modules/__test-utils';
import { MODULE } from '../module';

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];
function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

interface NodeGlobal {
  process?: { cwd?: () => string };
}

check('module satisfies the standard invariants', async () => {
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  await assertStandardModuleInvariants(MODULE, {
    folderName: '__smoke_test_tool',
    moduleFolderPath: `${cwd}/src/modules/primitives/__smoke_test_tool`,
  });
});

export async function runAll__smoke_test_toolTests(): Promise<void> {
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
  console.log(`\n__smoke_test_tool: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} __smoke_test_tool check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAll__smoke_test_toolTests();
}
