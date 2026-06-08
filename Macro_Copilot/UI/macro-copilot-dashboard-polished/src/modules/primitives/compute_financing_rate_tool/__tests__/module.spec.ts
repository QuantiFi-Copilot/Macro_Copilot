/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/compute_financing_rate_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Per-module round-trip test.  Asserts the dual-view contract
// (rendering_density.md §1), the standalone-bridge `typedView: null`
// contract (methodology_exposure.md §5), and the mockups-folder
// presence.  Identical boilerplate across every dual-view factory
// module — per-module customisation belongs in additional ``check(...)``
// blocks below.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'compute_financing_rate_tool';

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
// Dual-view rendering-density contract (rendering_density.md §1) +
// standalone-bridge `typedView: null` contract (methodology_exposure.md §5).
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; dual-view contract requires it.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check('surfaces.buildExtended is populated', () => {
  if (!MODULE.surfaces?.buildExtended) {
    throw new Error(
      'surfaces.buildExtended is missing.  Dual-view contract requires both buildExtended + buildCompact.',
    );
  }
});

check('surfaces.buildCompact is populated', () => {
  if (!MODULE.surfaces?.buildCompact) {
    throw new Error(
      'surfaces.buildCompact is missing.  Dual-view contract requires both buildExtended + buildCompact.',
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

check('claims monitor_surface tier and ships one widget', () => {
  if (!MODULE.tiers.includes('monitor_surface')) {
    throw new Error(
      `tiers missing 'monitor_surface'; got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
  const widgets = MODULE.monitorWidgets ?? [];
  if (widgets.length !== 1) {
    throw new Error(
      `monitorWidgets must declare exactly one widget; got ${widgets.length}`,
    );
  }
  if (widgets[0].id !== 'financing_rate') {
    throw new Error(
      `monitorWidgets[0].id must be 'financing_rate'; got '${widgets[0].id}'`,
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
