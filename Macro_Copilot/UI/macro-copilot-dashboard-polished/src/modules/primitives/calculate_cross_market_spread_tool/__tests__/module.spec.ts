/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/calculate_cross_market_spread_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Per-module round-trip — calls assertStandardModuleInvariants from
// src/modules/__test-utils.ts.  Catches FM11 invariants 1-8 in one check.
// Extends the standard boilerplate with the dual-view rendering-density
// contract checks (rendering_density.md §11): every primitive claiming
// custom_build_surface MUST ship BOTH surfaces.buildExtended AND
// surfaces.buildCompact, AND typedView MUST be null under the standalone-
// bridge contract.  Migration-mode extension (MIGRATION_RULES.md §6): the
// two pre-migration Monitor widget ids MUST remain registered verbatim —
// dashboards persist these ids.  Identical shape to the sovereign curve-
// spread sibling test (the migration reference).
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'calculate_cross_market_spread_tool';

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
// primitive claiming custom_build_surface MUST ship BOTH
// surfaces.buildExtended AND surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; the migrated dual-view module must claim it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
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

check('typedView is null (standalone-bridge pattern)', () => {
  if (MODULE.typedView != null) {
    throw new Error(
      `typedView must be null under the standalone-bridge contract; got '${MODULE.typedView}'.  See methodology_exposure.md §5.`,
    );
  }
});

check('legacy surfaces.resultRenderer is NOT populated', () => {
  const s = MODULE.surfaces as Record<string, unknown> | undefined;
  if (s && 'resultRenderer' in s && s.resultRenderer != null) {
    throw new Error(
      'surfaces.resultRenderer is populated; the migration must REMOVE the legacy typed-renderer surface (see MIGRATION_RULES §4).',
    );
  }
});

check('monitor widget ids preserved (backward-compat lock)', () => {
  const ids = (MODULE.monitorWidgets ?? []).map((w) => w.id);
  for (const requiredId of ['cross_market_spreads', 'cross_market_spread']) {
    if (!ids.includes(requiredId)) {
      throw new Error(
        `Monitor widget id '${requiredId}' is missing.  MIGRATION_RULES §6 requires the two pre-migration widget ids be preserved verbatim; got ids=${JSON.stringify(ids)}.`,
      );
    }
  }
});

check('parameterised monitor widget paramFields preserved', () => {
  const widget = (MODULE.monitorWidgets ?? []).find(
    (w) => w.id === 'cross_market_spread',
  );
  if (!widget) {
    throw new Error(
      "Monitor widget 'cross_market_spread' is missing (paramFields invariant check pre-condition).",
    );
  }
  const required = ['curve_family_1', 'curve_family_2', 'tenor', 'lookback_days'];
  const got = (widget.paramFields ?? []).map((p) => p.name);
  for (const name of required) {
    if (!got.includes(name)) {
      throw new Error(
        `Monitor widget 'cross_market_spread' is missing paramField '${name}'.  MIGRATION_RULES §6 requires paramField names + defaults preserved verbatim; got names=${JSON.stringify(got)}.`,
      );
    }
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
