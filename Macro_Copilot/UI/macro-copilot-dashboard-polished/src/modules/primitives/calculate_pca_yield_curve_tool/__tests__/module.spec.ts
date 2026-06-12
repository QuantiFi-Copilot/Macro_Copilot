/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// src/modules/primitives/calculate_pca_yield_curve_tool/__tests__/module.spec.ts
// ----------------------------------------------------------------------------
// Stage 3 per-module round-trip — calls assertStandardModuleInvariants
// from src/modules/__test-utils.ts.  Catches FM11 invariants 1-8 in
// one check.  Identical boilerplate across every module; per-module
// customisation belongs in additional ``check(...)`` blocks below.
// ============================================================================

import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = 'calculate_pca_yield_curve_tool';

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
// every new primitive claiming custom_build_surface MUST ship BOTH
// surfaces.buildExtended AND surfaces.buildCompact.
// ----------------------------------------------------------------------------

check('claims custom_build_surface tier', () => {
  if (!MODULE.tiers.includes('custom_build_surface')) {
    throw new Error(
      `tiers missing 'custom_build_surface'; Phase-1 pilot tools must claim it per rendering_density.md.  Got tiers=${JSON.stringify(MODULE.tiers)}`,
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

// ----------------------------------------------------------------------------
// Dual-view migration invariants (THESIS Q3) — the legacy rich-model
// BuilderCanvas route is RETIRED for this tool; the persisted-artifact
// path is RETAINED.  These checks pin both halves so a regression in
// either direction fails loudly.
// ----------------------------------------------------------------------------

check('persisted-artifact path retained (modelAdapter + surfaces.preview)', () => {
  if (MODULE.modelAdapter == null) {
    throw new Error(
      'MODULE.modelAdapter must be retained — the persisted-artifact RichModelWidget dispatcher reads it.',
    );
  }
  if (!MODULE.surfaces?.preview) {
    throw new Error(
      'surfaces.preview must be retained — the widgets/index.ts barrel registers it for persisted-artifact rendering.',
    );
  }
});

// NOTE — no mockups/ check for this module: the dual-view mockups are
// captured by the integrator post-merge (migration deliverable scope).
// Restore the pilot's mockups check when the PNGs land.

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
