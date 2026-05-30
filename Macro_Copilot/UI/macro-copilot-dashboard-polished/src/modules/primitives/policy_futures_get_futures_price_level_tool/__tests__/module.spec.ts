/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/policy_futures_get_futures_price_level_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Dispatch-1 round-trip — calls assertStandardModuleInvariants from
// src/modules/__test-utils.ts (FM11 invariants 1-8) plus the dual-view
// rendering-density contract checks (rendering_density.md §1).
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'policy_futures_get_futures_price_level_tool';

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
// Dual-view rendering-density contract (per rendering_density.md §1):
// every new primitive MUST ship BOTH surfaces.buildExtended AND
// surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; dual-view contract requires it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check('claims monitor_surface tier', () => {
  if (!MODULE.tiers.includes('monitor_surface')) {
    throw new Error(
      `tiers missing 'monitor_surface'; catalog entry required_tiers includes monitor_surface per the desk-canonical front-STIR eligibility.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
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

check('typedView is null (standalone-bridge contract)', () => {
  if (MODULE.typedView != null) {
    throw new Error(
      `typedView must be null for modules under the standalone-bridge contract; got '${MODULE.typedView}'.  See methodology_exposure.md §5.`,
    );
  }
});

check('monitorWidgets carries the Policy Futures Price Level tile', () => {
  const widgets = MODULE.monitorWidgets ?? [];
  if (widgets.length === 0) {
    throw new Error(
      'monitorWidgets is empty; module claims monitor_surface and must populate the Monitor tile via the monitorWidgets array (Stage 4d multi-variant shape).',
    );
  }
  const ids = widgets.map((w) => w.id);
  if (!ids.includes('policy_futures_price_level')) {
    throw new Error(
      `monitorWidgets missing the 'policy_futures_price_level' entry; got ids=${JSON.stringify(ids)}.`,
    );
  }
});

check('mockups folder exists alongside the module', async () => {
  try {
    const fs = await import('node:fs/promises');
    const path = `${cwd()}/src/modules/primitives/${FOLDER}/mockups`;
    const entries = await fs.readdir(path);
    const required = ['Compact.png', 'Extended.png'];
    const missing = required.filter((r) => !entries.includes(r));
    if (missing.length > 0) {
      throw new Error(`mockups/ missing required PNGs: ${missing.join(', ')}`);
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
