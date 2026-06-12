/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/policy_futures_get_futures_strip_snapshot_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Stage 3 per-module round-trip — calls assertStandardModuleInvariants
// from src/modules/__test-utils.ts.  Catches FM11 invariants 1-8 in
// one check.  Identical boilerplate across every module; per-module
// customisation belongs in additional ``check(...)`` blocks below.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'policy_futures_get_futures_strip_snapshot_tool';

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

// ----------------------------------------------------------------------------
// Dual-view rendering-density contract (rendering_density.md §11):
// every primitive claiming custom_build_surface MUST ship BOTH
// surfaces.buildExtended AND surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; dual-view tools must claim it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check('surfaces.buildExtended is populated', () => {
  if (!MODULE.surfaces?.buildExtended) {
    throw new Error(
      'surfaces.buildExtended is missing.  Dual-view contract requires both buildExtended + buildCompact for every primitive claiming custom_build_surface.',
    );
  }
});

check('surfaces.buildCompact is populated', () => {
  if (!MODULE.surfaces?.buildCompact) {
    throw new Error(
      'surfaces.buildCompact is missing.  Dual-view contract requires both buildExtended + buildCompact for every primitive claiming custom_build_surface.',
    );
  }
});

check('typedView is null (standalone-module pattern)', () => {
  if (MODULE.typedView != null) {
    throw new Error(
      `typedView must be null for new modules under the standalone-bridge contract; got '${MODULE.typedView}'.  See methodology_exposure.md §5.`,
    );
  }
});

// NO mockups for this module (THESIS header: the shared-shell catalogue IS
// the design reference) — so instead of the pilot's mockups/ PNG check we
// assert the three canonical surface files exist (FM8 file contract).

check('canonical surface files exist alongside the module', async () => {
  try {
    const fs = await import('node:fs/promises');
    const path = `${cwd()}/src/modules/primitives/${FOLDER}/surfaces`;
    const entries = await fs.readdir(path);
    const required = [
      'BuildExtended.tsx',
      'BuildCompact.tsx',
      'policyFuturesStripSnapshotShared.ts',
    ];
    const missing = required.filter((r) => !entries.includes(r));
    if (missing.length > 0) {
      throw new Error(`surfaces/ missing required files: ${missing.join(', ')}`);
    }
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ERR_MODULE_NOT_FOUND') {
      return;
    }
    throw err;
  }
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
