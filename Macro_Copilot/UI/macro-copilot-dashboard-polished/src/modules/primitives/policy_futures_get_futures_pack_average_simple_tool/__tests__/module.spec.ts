/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/policy_futures_get_futures_pack_average_simple_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Per-module round-trip — calls assertStandardModuleInvariants from
// src/modules/__test-utils.ts (FM11 invariants 1-8 in one check) PLUS
// the dual-view + standalone-bridge assertions per the Phase-1+ contract.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'policy_futures_get_futures_pack_average_simple_tool';

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
// Dual-view rendering-density contract (rendering_density.md §11): every
// new primitive claiming custom_build_surface MUST ship BOTH
// surfaces.buildExtended AND surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; this tool must claim it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
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

check('default curve_family is a recognised policy-futures family', () => {
  const cf = MODULE.defaultParams?.curve_family;
  const RECOGNISED = new Set(['SOFR_FUT', 'EUR_SHORT_RATE_FUT', 'SONIA_FUT']);
  if (typeof cf !== 'string' || !RECOGNISED.has(cf)) {
    throw new Error(
      `defaultParams.curve_family must be a recognised policy-futures family; got ${JSON.stringify(cf)}.`,
    );
  }
});

check('default pack is a closed-Literal whites|reds', () => {
  // The backend Pydantic schema declares ``pack`` as a closed Literal
  // ``'whites' | 'reds'``; defaultParams must pick one so the very
  // first render against the typed-detail endpoint succeeds without
  // an interim "missing input" error.
  const pack = MODULE.defaultParams?.pack;
  if (pack !== 'whites' && pack !== 'reds') {
    throw new Error(
      `defaultParams.pack must be 'whites' or 'reds'; got ${JSON.stringify(pack)}.`,
    );
  }
});

check('default curve_family is a V1-executable family', () => {
  // ADR 0013 V1 scope: EUR_SHORT_RATE_FUT is admitted at the schema
  // layer but returns a controlled-error envelope from compute().  The
  // default must pick a curve_family that the compute layer actually
  // executes so the first-paint render succeeds.
  const cf = MODULE.defaultParams?.curve_family;
  const EXECUTABLE = new Set(['SOFR_FUT', 'SONIA_FUT']);
  if (typeof cf !== 'string' || !EXECUTABLE.has(cf)) {
    throw new Error(
      `defaultParams.curve_family must be V1-executable (SOFR_FUT / SONIA_FUT); got ${JSON.stringify(cf)}.  EUR_SHORT_RATE_FUT is schema-admitted but compute-refused per ADR 0013 V1 scope.`,
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
