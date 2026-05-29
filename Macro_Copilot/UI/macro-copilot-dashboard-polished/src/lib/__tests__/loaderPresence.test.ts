/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// loaderPresence.test.ts — Stage 2 registry-derivation invariants.
// ----------------------------------------------------------------------------
// Stage 2 — Build the src/modules/ infrastructure.  This test asserts
// the loader barrel at ``src/modules/index.ts`` stays in sync with
// the actual module folders under ``src/modules/{primitives,workflows}/``.
//
// Doctrine reference
// ------------------
// - FM12 (loader-presence): every module folder MUST be imported in
//   the central loader and contribute exactly one entry to either
//   ALL_PRIMITIVE_MODULES or ALL_WORKFLOW_MODULES.
// - FP4 (derived registries): the central registries derive from
//   these arrays.
// - FP5 (pure-spec assembly): each module exports a pure value,
//   not a side effect.
//
// What this test covers TODAY (Stage 2)
// -------------------------------------
//   * ALL_PRIMITIVE_MODULES and ALL_WORKFLOW_MODULES exist and are
//     arrays (no surprise initial shape).
//   * Loader-barrel ↔ filesystem parity: every folder under
//     ``src/modules/{primitives,workflows}/`` is referenced from the
//     loader, and vice versa.  With ZERO folders today, the parity
//     check trivially passes — and the test catches the FIRST module
//     someone adds without wiring it into the loader.
//   * Lookup helpers (``getPrimitiveModule`` / ``getWorkflowModule``)
//     return ``undefined`` for unknown identifiers + the right spec
//     for any registered one (covered by per-module tests in Stage 3+;
//     here we just smoke that the helpers exist + work for the empty
//     case).
//   * Each registered module's ``toolName`` / ``templateId`` is unique
//     (no duplicate registrations).
//   * Surfaces match tier claims (FM3 / FM8): no module ships a
//     ``surfaces.X`` that isn't backed by a tier claim, and no claimed
//     capability tier lacks the matching surface.
//
// Same ``check`` shim as the other build-folder tests so the existing
// ``scripts/run_build_tests.mjs`` runner picks the file up once it's
// extended to walk additional roots (Stage 2 task).
// ============================================================================

import {
  ALL_PRIMITIVE_MODULES,
  ALL_WORKFLOW_MODULES,
  getPrimitiveModule,
  getWorkflowModule,
  ALL_SURFACE_TIERS,
  CAPABILITY_TIERS,
  RUNTIME_STATUS_TIERS,
  type SurfaceTier,
} from '@/modules';

interface NodeFs {
  readdirSync: (path: string) => string[];
  statSync: (path: string) => {
    isDirectory: () => boolean;
    isFile: () => boolean;
  };
  readFileSync: (path: string, encoding: string) => string;
  existsSync: (path: string) => boolean;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];

function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(
      `assertEqual failed: ${label}\n  expected: ${e}\n  actual:   ${a}`,
    );
  }
}

async function loadFs(): Promise<NodeFs> {
  // @ts-expect-error - node-only; esbuild --platform=node resolves it.
  return (await import('fs')) as NodeFs;
}

function cwd(): string {
  const _g = globalThis as unknown as NodeGlobal;
  return _g.process?.cwd?.() ?? '.';
}

// ---------------------------------------------------------------------------
// Filesystem discovery — list every module folder.
// ---------------------------------------------------------------------------
//
// A "module folder" is a directory under ``src/modules/primitives/``
// or ``src/modules/workflows/`` that contains a ``module.ts`` file.
// We deliberately do NOT count empty directories or ones that ship
// only THESIS.md — those would mean the scaffolding is in-flight, not
// that a module is live.

async function listModuleFolders(
  kind: 'primitives' | 'workflows',
): Promise<string[]> {
  const fs = await loadFs();
  const root = `${cwd()}/src/modules/${kind}`;
  let entries: string[] = [];
  try {
    entries = fs.readdirSync(root);
  } catch {
    // Directory doesn't exist yet — Stage 2 ships this empty.  An
    // empty list is the correct answer in that case.
    return [];
  }
  const folders: string[] = [];
  for (const name of entries) {
    const full = `${root}/${name}`;
    let s;
    try {
      s = fs.statSync(full);
    } catch {
      continue;
    }
    if (!s.isDirectory()) continue;
    if (!fs.existsSync(`${full}/module.ts`)) continue;
    folders.push(name);
  }
  return folders.sort();
}

// ---------------------------------------------------------------------------
// Shape sanity — arrays exist + are correct type.
// ---------------------------------------------------------------------------

check('ALL_PRIMITIVE_MODULES exists and is an array', () => {
  assertTruthy(Array.isArray(ALL_PRIMITIVE_MODULES), 'is array');
});

check('ALL_WORKFLOW_MODULES exists and is an array', () => {
  assertTruthy(Array.isArray(ALL_WORKFLOW_MODULES), 'is array');
});

check('SurfaceTier closed family has exactly 9 members', () => {
  // Stage 4e: 5 runtime-status + 4 capability = 9.  Stage 4e added
  // ``manifest_typed_view`` to disambiguate manifest-only typed-view
  // tools (in backend ``_MANIFEST_ONLY_BUILD_TOOLS``) from genuinely
  // workflow-incompatible tools (in backend ``WORKFLOW_INCOMPATIBLE_TOOLS``).
  // Adding a new tier requires an ADR amending the closed family — see
  // docs_revamped/02_components/frontend_module/tiers.md §
  // "Closed-family extensions".
  assertEqual(ALL_SURFACE_TIERS.length, 9, 'tier count');
  assertEqual(RUNTIME_STATUS_TIERS.size, 5, 'runtime-status count');
  assertEqual(CAPABILITY_TIERS.size, 4, 'capability count');
});

// ---------------------------------------------------------------------------
// Loader-presence parity (FM12).
// ---------------------------------------------------------------------------

check('primitive modules: filesystem ↔ loader parity', async () => {
  const onDisk = await listModuleFolders('primitives');
  const inLoader = ALL_PRIMITIVE_MODULES.map((m) => m.toolName).sort();
  // Stage 2: both empty.  The assertion is meaningful from Stage 3+
  // when modules start landing.
  const missingFromLoader = onDisk.filter((n) => !inLoader.includes(n));
  const orphanedInLoader = inLoader.filter((n) => !onDisk.includes(n));
  if (missingFromLoader.length > 0 || orphanedInLoader.length > 0) {
    throw new Error(
      `FM12 violation — loader-presence parity broken:\n` +
        (missingFromLoader.length > 0
          ? `  Folders missing from src/modules/index.ts:\n    ${missingFromLoader.join('\n    ')}\n`
          : '') +
        (orphanedInLoader.length > 0
          ? `  Loader entries with no matching folder:\n    ${orphanedInLoader.join('\n    ')}\n`
          : '') +
        `  Fix: add an import line in src/modules/index.ts for each ` +
        `missing folder, or remove the orphaned entry.`,
    );
  }
});

check('workflow modules: filesystem ↔ loader parity', async () => {
  const onDisk = await listModuleFolders('workflows');
  const inLoader = ALL_WORKFLOW_MODULES.map((m) => m.templateId).sort();
  const missingFromLoader = onDisk.filter((n) => !inLoader.includes(n));
  const orphanedInLoader = inLoader.filter((n) => !onDisk.includes(n));
  if (missingFromLoader.length > 0 || orphanedInLoader.length > 0) {
    throw new Error(
      `FM12 violation — workflow loader-presence parity broken:\n` +
        (missingFromLoader.length > 0
          ? `  Folders missing from src/modules/index.ts:\n    ${missingFromLoader.join('\n    ')}\n`
          : '') +
        (orphanedInLoader.length > 0
          ? `  Loader entries with no matching folder:\n    ${orphanedInLoader.join('\n    ')}\n`
          : ''),
    );
  }
});

// ---------------------------------------------------------------------------
// Lookup-helper sanity.
// ---------------------------------------------------------------------------

check('getPrimitiveModule: returns undefined for unknown tool name', () => {
  assertEqual(
    getPrimitiveModule('totally_made_up_tool'),
    undefined,
    'unknown → undefined',
  );
  assertEqual(getPrimitiveModule(''), undefined, 'empty → undefined');
});

check('getWorkflowModule: returns undefined for unknown template id', () => {
  assertEqual(
    getWorkflowModule('totally_made_up_workflow'),
    undefined,
    'unknown → undefined',
  );
  assertEqual(getWorkflowModule(''), undefined, 'empty → undefined');
});

check('getPrimitiveModule: resolves every registered module by name', () => {
  // Stage 2: zero registered modules; loop is a no-op.  From Stage
  // 3+ this catches a registered module that the helper can't find
  // (would happen if someone redefined ``ALL_PRIMITIVE_MODULES``
  // without updating the helper).
  for (const m of ALL_PRIMITIVE_MODULES) {
    const found = getPrimitiveModule(m.toolName);
    assertTruthy(found, `lookup: ${m.toolName}`);
    assertEqual(found?.toolName, m.toolName, `toolName: ${m.toolName}`);
  }
});

check('getWorkflowModule: resolves every registered module by templateId', () => {
  for (const m of ALL_WORKFLOW_MODULES) {
    const found = getWorkflowModule(m.templateId);
    assertTruthy(found, `lookup: ${m.templateId}`);
    assertEqual(found?.templateId, m.templateId, `templateId: ${m.templateId}`);
  }
});

// ---------------------------------------------------------------------------
// Uniqueness — no duplicate registrations.
// ---------------------------------------------------------------------------

check('ALL_PRIMITIVE_MODULES: every toolName is unique', () => {
  const seen = new Set<string>();
  for (const m of ALL_PRIMITIVE_MODULES) {
    if (seen.has(m.toolName)) {
      throw new Error(
        `Duplicate primitive module: ${JSON.stringify(m.toolName)} appears ` +
          `more than once in ALL_PRIMITIVE_MODULES.`,
      );
    }
    seen.add(m.toolName);
  }
});

check('ALL_WORKFLOW_MODULES: every templateId is unique', () => {
  const seen = new Set<string>();
  for (const m of ALL_WORKFLOW_MODULES) {
    if (seen.has(m.templateId)) {
      throw new Error(
        `Duplicate workflow module: ${JSON.stringify(m.templateId)} appears ` +
          `more than once in ALL_WORKFLOW_MODULES.`,
      );
    }
    seen.add(m.templateId);
  }
});

// ---------------------------------------------------------------------------
// Per-module shape sanity (FM3 / FM8) — light checks that work in any
// environment.  Per-module deep tests live in each module's own
// ``__tests__/module.spec.ts`` from Stage 3+.
// ---------------------------------------------------------------------------

check('every primitive module claims exactly one runtime-status tier', () => {
  for (const m of ALL_PRIMITIVE_MODULES) {
    const runtime = m.tiers.filter((t) =>
      (RUNTIME_STATUS_TIERS as ReadonlySet<SurfaceTier>).has(t),
    );
    if (runtime.length !== 1) {
      throw new Error(
        `FM3 violation in ${m.toolName} — tiers must contain exactly one ` +
          `runtime-status tier; got ${runtime.length}: ${JSON.stringify(runtime)}`,
      );
    }
  }
});

check('every primitive module: tiers ↔ surfaces consistency', () => {
  const map: Record<string, 'build' | 'preview' | 'monitor' | 'ask'> = {
    custom_build_surface: 'build',
    custom_preview_widget: 'preview',
    monitor_surface: 'monitor',
    ask_surface: 'ask',
  };
  // Stage 5: surfaces.resultRenderer also satisfies custom_build_surface.
  // Dual-view (rendering_density.md §5.2): buildExtended + buildCompact are
  // the new Build-surface field names; both satisfy custom_build_surface.
  const surfaceKeyToTier: Record<string, string> = {
    build: 'custom_build_surface',
    buildExtended: 'custom_build_surface',
    buildCompact: 'custom_build_surface',
    resultRenderer: 'custom_build_surface',
    preview: 'custom_preview_widget',
    monitor: 'monitor_surface',
    ask: 'ask_surface',
  };
  for (const m of ALL_PRIMITIVE_MODULES) {
    const tierSet = new Set<SurfaceTier>(m.tiers);
    const surfaces = (m.surfaces ?? {}) as Record<string, unknown>;
    // capability tier ⇒ surface populated
    //
    // Stage 4d relaxation for ``monitor_surface``: satisfied by
    // EITHER ``surfaces.monitor`` OR non-empty ``monitorWidgets``.
    // Stage 5 relaxation for ``custom_build_surface``: satisfied by
    // EITHER ``surfaces.build`` (full Build experience) OR
    // ``surfaces.resultRenderer`` (payload renderer).  Mirrors the
    // relaxation in ``src/modules/__test-utils.ts``.
    for (const t of tierSet) {
      if (!(CAPABILITY_TIERS as ReadonlySet<SurfaceTier>).has(t)) continue;
      const key = map[t];
      if (!key) continue;
      if (key === 'monitor') {
        const hasSurface = surfaces.monitor != null;
        const hasWidgets =
          Array.isArray(m.monitorWidgets) && m.monitorWidgets.length > 0;
        if (!hasSurface && !hasWidgets) {
          throw new Error(
            `${m.toolName}: claims 'monitor_surface' but neither ` +
              `surfaces.monitor nor monitorWidgets is populated`,
          );
        }
        continue;
      }
      if (key === 'build') {
        const hasBuild = surfaces.build != null;
        const hasRenderer = surfaces.resultRenderer != null;
        if (!hasBuild && !hasRenderer) {
          throw new Error(
            `${m.toolName}: claims 'custom_build_surface' but neither ` +
              `surfaces.build nor surfaces.resultRenderer is populated`,
          );
        }
        if (hasBuild && hasRenderer) {
          throw new Error(
            `${m.toolName}: surfaces.build AND surfaces.resultRenderer are ` +
              `both populated — they are mutually exclusive`,
          );
        }
        continue;
      }
      if (!surfaces[key]) {
        throw new Error(
          `${m.toolName}: claims '${t}' but surfaces.${key} is empty`,
        );
      }
    }
    // surface populated ⇒ matching capability tier claimed
    for (const [key, comp] of Object.entries(surfaces)) {
      if (comp == null) continue;
      const tier = surfaceKeyToTier[key];
      if (!tier || !tierSet.has(tier as SurfaceTier)) {
        throw new Error(
          `${m.toolName}: surfaces.${key} populated but '${tier}' not in tiers`,
        );
      }
    }
    // Stage 4d reverse: monitorWidgets ⇒ monitor_surface tier
    const widgetsPopulated =
      Array.isArray(m.monitorWidgets) && m.monitorWidgets.length > 0;
    if (widgetsPopulated && !tierSet.has('monitor_surface')) {
      throw new Error(
        `${m.toolName}: monitorWidgets populated but 'monitor_surface' not in tiers`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// Runner — same shim as the build-folder tests.
// ---------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Stage D — dual-view rendering-density contract (rendering_density.md §1 + §11).
// ----------------------------------------------------------------------------
// SHARED, loader-level enforcement: any module that has ADOPTED the dual-view
// Build contract (ships EITHER surfaces.buildExtended OR surfaces.buildCompact)
// MUST ship BOTH.  This guarantees no future module can land with an
// asymmetric dual-view (one half of the contract) — the multi-tool DAG page
// relies on buildCompact being present whenever buildExtended is, and the
// single-tool canvas relies on the reverse.
//
// Legacy modules that ship neither dual-view field (only the pre-contract
// surfaces.build / surfaces.resultRenderer) are an explicit carve-out per
// rendering_density.md §9 and are NOT flagged here — they fall back to the
// generic artifact-type card in the DAG.
check('dual-view contract: adopting either build view requires BOTH', () => {
  const offenders: string[] = [];
  for (const m of ALL_PRIMITIVE_MODULES) {
    const s = (m.surfaces ?? {}) as Record<string, unknown>;
    const hasExtended = s.buildExtended != null;
    const hasCompact = s.buildCompact != null;
    // Only modules that adopted at least one dual-view field are bound.
    if (!hasExtended && !hasCompact) continue;
    if (!hasExtended || !hasCompact) {
      offenders.push(
        `${m.toolName} (buildExtended=${hasExtended}, buildCompact=${hasCompact})`,
      );
    }
  }
  if (offenders.length > 0) {
    throw new Error(
      'Dual-view contract violation (rendering_density.md §1 + §11) — these ' +
        'modules ship one Build view but not both:\n  ' +
        offenders.join('\n  '),
    );
  }
});

export async function runAllLoaderPresenceTests(): Promise<void> {
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
  console.log(`\nloader-presence coverage: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} loader-presence check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllLoaderPresenceTests();
}
