/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/calculate_inflation_swap_rate_level_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Per-module round-trip — calls assertStandardModuleInvariants from
// src/modules/__test-utils.ts.  Catches FM11 invariants 1-8 in one
// check.  Plus the dual-view rendering-density contract pins
// (rendering_density.md §1) and the standalone-bridge typedView=null pin
// (methodology_exposure.md §5).
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'calculate_inflation_swap_rate_level_tool';

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
// Dual-view rendering-density contract (per
// docs_revamped/03_standards/rendering_density.md §1): every primitive
// claiming custom_build_surface MUST ship BOTH surfaces.buildExtended AND
// surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; dual-view tools must claim it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check('claims monitor_surface tier', () => {
  if (!MODULE.tiers.includes('monitor_surface')) {
    throw new Error(
      `tiers missing 'monitor_surface'; per the catalog this tool ships a Monitor tile.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
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

check('declares a monitor widget', () => {
  if (!MODULE.monitorWidgets || MODULE.monitorWidgets.length === 0) {
    throw new Error(
      'monitorWidgets is empty; claiming monitor_surface tier without a widget definition violates FM5c.',
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

check('default curve_family is a recognised ZCIS curve', () => {
  const cf = MODULE.defaultParams?.curve_family;
  const RECOGNISED = new Set([
    'USD_ZCIS',
    'EUR_ZCIS',
    'GBP_ZCIS',
  ]);
  if (typeof cf !== 'string' || !RECOGNISED.has(cf)) {
    throw new Error(
      `defaultParams.curve_family must be a recognised ZCIS family; got ${JSON.stringify(cf)}.`,
    );
  }
});

check('mockups folder exists alongside the module', async () => {
  // Mockup PNGs are committed in mockups/ per the mockup-first workflow.
  // At minimum we expect Compact.png + Extended.png.  Skip silently when
  // filesystem access isn't available (browser test env).
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
