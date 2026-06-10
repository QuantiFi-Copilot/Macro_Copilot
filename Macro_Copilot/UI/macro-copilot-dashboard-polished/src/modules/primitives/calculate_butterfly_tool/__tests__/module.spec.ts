/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/calculate_butterfly_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Migration dispatch per-module round-trip — calls
// assertStandardModuleInvariants from src/modules/__test-utils.ts (catches
// FM11 invariants 1-8 in one check) plus the dual-view rendering-density
// contract checks (rendering_density.md §11): every module claiming
// custom_build_surface MUST ship BOTH surfaces.buildExtended AND
// surfaces.buildCompact, AND typedView must be null under the standalone-
// bridge contract (methodology_exposure.md §5).  Mirrors the OIS / linker /
// ZCIS butterfly sibling tests file-for-file.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'calculate_butterfly_tool';

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
// every module claiming custom_build_surface MUST ship BOTH
// surfaces.buildExtended AND surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; dual-view-built tools must claim it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check('surfaces.buildExtended is populated', () => {
  if (!MODULE.surfaces?.buildExtended) {
    throw new Error(
      'surfaces.buildExtended is missing.  Phase-1 dual-view contract requires both buildExtended + buildCompact for every primitive claiming custom_build_surface.',
    );
  }
});

check('surfaces.buildCompact is populated', () => {
  if (!MODULE.surfaces?.buildCompact) {
    throw new Error(
      'surfaces.buildCompact is missing.  Phase-1 dual-view contract requires both buildExtended + buildCompact for every primitive claiming custom_build_surface.',
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

// ----------------------------------------------------------------------------
// Migration-mode backward-compat assertions (MIGRATION_RULES §5 + §8):
//   - manifest_typed_view tier PRESERVED (backend _PRIMITIVE_SPECS membership
//     unchanged; do NOT swap to generic_runnable per §8 anti-patterns)
//   - No monitor widgets (pre-migration module did not claim monitor_surface;
//     design_guardrails are explicit "do NOT add one")
//   - surfaces.build aliased to BuildExtended so build-page dispatch (legacy
//     VirtualPrimitiveCanvas) keeps routing correctly post-migration
// ----------------------------------------------------------------------------

check('manifest_typed_view tier preserved (backend reality)', () => {
  if (!MODULE.tiers.includes('manifest_typed_view')) {
    throw new Error(
      `tiers missing 'manifest_typed_view'; this tool is in _MANIFEST_ONLY_BUILD_TOOLS (NOT _PRIMITIVE_SPECS) on the backend; per MIGRATION_RULES §8 the tier set MUST mirror backend reality.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
    );
  }
});

check('no monitor widgets (migration design guardrail)', () => {
  const w = MODULE.monitorWidgets;
  if (Array.isArray(w) && w.length > 0) {
    throw new Error(
      `monitorWidgets must be empty for this migration (pre-migration module did not claim monitor_surface and design_guardrails are explicit).  Got ${w.length} widget(s).`,
    );
  }
});

check('surfaces.build aliased to buildExtended (legacy dispatcher)', () => {
  if (MODULE.surfaces?.build !== MODULE.surfaces?.buildExtended) {
    throw new Error(
      'surfaces.build must alias surfaces.buildExtended so the legacy VirtualPrimitiveCanvas dispatcher routes Build pages correctly post-migration.',
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
