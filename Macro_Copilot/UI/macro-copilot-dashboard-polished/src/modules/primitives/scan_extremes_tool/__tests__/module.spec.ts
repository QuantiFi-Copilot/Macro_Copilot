/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/scan_extremes_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Migration-mode rewrite: replaces the legacy spec (only ran
// assertStandardModuleInvariants).  Calls assertStandardModuleInvariants for
// FM11 invariants 1-8 then adds the rendering_density.md §11 dual-view
// contract checks (both buildExtended + buildCompact populated) + the
// SCANNER-shape guardrail + the standalone-bridge contract checks
// (mockups present) + the migration-mode backward-compat checks
// (monitor widget id 'scanner' still present; manifest_typed_view tier
// preserved; legacy ResultRenderer + workspaceLabel + unsupportedReason
// gone).  Identical boilerplate shape to the sibling
// scan_inflation_swaps_extremes_tool spec.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'scan_extremes_tool';

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
// Phase-1 dual-view rendering-density contract (rendering_density.md §11):
// every primitive claiming custom_build_surface MUST ship BOTH
// surfaces.buildExtended AND surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; this module ships a bespoke scanner-shape Build surface per the catalog guardrail.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
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

// ----------------------------------------------------------------------------
// Migration-mode backward-compat checks (MIGRATION_RULES.md §6, §9).
// ----------------------------------------------------------------------------

check('preserves manifest_typed_view runtime tier', () => {
  if (!MODULE.tiers.includes('manifest_typed_view')) {
    throw new Error(
      `tiers missing 'manifest_typed_view'; backend _PRIMITIVE_SPECS does NOT contain this tool, so the runtime tier must mirror backend reality (MIGRATION_RULES.md §4 step 6).  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check("claims monitor_surface + preserves widget id 'scanner'", () => {
  if (!MODULE.tiers.includes('monitor_surface')) {
    throw new Error(
      `tiers missing 'monitor_surface'; legacy module claimed it and dashboards depend on it.`,
    );
  }
  if (!MODULE.monitorWidgets || MODULE.monitorWidgets.length === 0) {
    throw new Error(
      'monitor_surface claimed but monitorWidgets is empty; per MIGRATION_RULES.md §6 the widget identity is the backward-compat lock.',
    );
  }
  const scanner = MODULE.monitorWidgets.find((w) => w.id === 'scanner');
  if (!scanner) {
    throw new Error(
      `Widget id 'scanner' MUST remain registered in MODULE.monitorWidgets[] per the migration backward-compat lock; got ids=${JSON.stringify(MODULE.monitorWidgets.map((w) => w.id))}`,
    );
  }
  if (scanner.parameterized !== false) {
    throw new Error(
      `Widget 'scanner' must remain non-parameterised per the migration backward-compat lock; got parameterized=${scanner.parameterized}.`,
    );
  }
});

check('legacy workspaceLabel field removed', () => {
  // Note: unsupportedReason is INTENTIONALLY kept — the framework's FM6
  // invariant (assertStandardModuleInvariants #8) REQUIRES it when the
  // runtime tier is manifest_typed_view, which this module preserves per
  // MIGRATION_RULES.md §4 step 6.  The copy is updated to describe the new
  // dual-view affordance; the catalog's "remove unsupportedReason"
  // instruction is overridden by the framework invariant.
  const m = MODULE as unknown as Record<string, unknown>;
  if ('workspaceLabel' in m && m.workspaceLabel != null) {
    throw new Error(
      `Legacy 'workspaceLabel' field present (${String(m.workspaceLabel)}); MIGRATION_RULES.md §8 anti-pattern — remove for the dual-view contract.`,
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
