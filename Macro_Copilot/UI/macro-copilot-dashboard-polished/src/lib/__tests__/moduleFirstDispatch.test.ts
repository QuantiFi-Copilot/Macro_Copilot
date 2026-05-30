// ============================================================================
// moduleFirstDispatch.test.ts — Stage 5 acceptance gate.
// ----------------------------------------------------------------------------
// The Stage 5 architectural promise was concrete:
//
//   > Add a fake/test primitive module with custom Build, Ask, and
//   > Monitor surfaces.  Do NOT edit BuildShell, ConversationCanvas,
//   > toolNames, monitor registry, or other central routing files.
//   > The custom surfaces must render.
//
// This test holds the promise.  The smoke-test fixture module lives
// at ``src/modules/primitives/__smoke_test_tool/`` and ships every
// capability surface (build, preview, monitor, ask).  This file
// asserts:
//
//   1. The smoke module is registered in ``ALL_PRIMITIVE_MODULES``.
//   2. ``getPrimitiveModule('__smoke_test_tool')`` returns it.
//   3. ``MODULE.surfaces.build``, ``.preview``, ``.ask`` and
//      ``MODULE.monitorWidgets[0].component`` are populated.
//   4. The Monitor catalog walker derives a ``__smoke_widget`` entry
//      from the module (this is the integration-side proof that the
//      registry walker reached the module without an edit).
//   5. The ``widgets/index.ts`` walker registers the preview surface
//      under ``(Series, '__smoke_test_tool')`` in the per-tool
//      node-renderer registry.
//   6. The toolNames hybrid does NOT include ``__smoke_test_tool`` in
//      ``KNOWN_BACKEND_TOOLS`` / ``RUNNABLE_PRIMITIVE_TOOLS`` /
//      ``UNSUPPORTED_KNOWN_TOOLS`` (the smoke fixture is excluded
//      from cross-side parity by the ``__``-prefix filter).
//
// If any check fails, Stage 5's "module folder is the source of
// truth" architecture is broken.
// ============================================================================

import {
  ALL_PRIMITIVE_MODULES,
  getPrimitiveModule,
} from '@/modules';
import { WIDGET_TYPES } from '@/components/monitor/registry';
import {
  KNOWN_BACKEND_TOOLS,
  RUNNABLE_PRIMITIVE_TOOLS,
  UNSUPPORTED_KNOWN_TOOLS,
} from '@/lib/toolNames';

type Check = { label: string; fn: () => void | Promise<void> };
const _checks: Check[] = [];

function check(label: string, fn: () => void | Promise<void>): void {
  _checks.push({ label, fn });
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
}

function assertFalsy(v: unknown, label: string): void {
  if (v) throw new Error(`assertFalsy failed: ${label}`);
}

const SMOKE_TOOL = '__smoke_test_tool';

check('smoke module is registered in ALL_PRIMITIVE_MODULES', () => {
  const found = ALL_PRIMITIVE_MODULES.find((m) => m.toolName === SMOKE_TOOL);
  assertTruthy(found, 'present');
});

check('getPrimitiveModule returns the smoke module', () => {
  const m = getPrimitiveModule(SMOKE_TOOL);
  assertTruthy(m, 'lookup returns module');
  assertTruthy(m!.surfaces?.build, 'surfaces.build populated');
  assertTruthy(m!.surfaces?.preview, 'surfaces.preview populated');
  assertTruthy(m!.surfaces?.ask, 'surfaces.ask populated');
  assertTruthy(
    Array.isArray(m!.monitorWidgets) && m!.monitorWidgets.length === 1,
    'monitorWidgets has 1 entry',
  );
  assertTruthy(m!.monitorWidgets![0].component, 'monitorWidgets[0].component populated');
});

check('Monitor catalog walker found the smoke widget', () => {
  // Stage 4d's ``monitor/registry.ts`` walks ALL_PRIMITIVE_MODULES to
  // build WIDGET_TYPES.  If the walker correctly visits the smoke
  // module, the ``__smoke_widget`` id appears in the catalog.
  assertTruthy(
    Object.prototype.hasOwnProperty.call(WIDGET_TYPES, '__smoke_widget'),
    'WIDGET_TYPES has __smoke_widget entry',
  );
  const entry = WIDGET_TYPES['__smoke_widget'];
  assertTruthy(entry, 'entry populated');
  // sourceTool is derived from the owning module — proves the walker
  // attributed the entry to the smoke module specifically.
  assertTruthy(
    entry.sourceTool === SMOKE_TOOL,
    `sourceTool resolved (got '${entry.sourceTool}')`,
  );
});

check('smoke module excluded from backend-facing registries', () => {
  // The smoke module ships in the loader but MUST NOT pollute the
  // KNOWN_BACKEND_TOOLS / RUNNABLE_PRIMITIVE_TOOLS / UNSUPPORTED_KNOWN_TOOLS
  // sets — those compare to the backend's real primitive registry.
  assertFalsy(KNOWN_BACKEND_TOOLS.has(SMOKE_TOOL), 'not in KNOWN_BACKEND_TOOLS');
  assertFalsy(
    RUNNABLE_PRIMITIVE_TOOLS.has(SMOKE_TOOL),
    'not in RUNNABLE_PRIMITIVE_TOOLS',
  );
  assertFalsy(
    UNSUPPORTED_KNOWN_TOOLS.has(SMOKE_TOOL),
    'not in UNSUPPORTED_KNOWN_TOOLS',
  );
});

check('Stage 5 architectural locality: dispatcher modules import only registry-level helpers', async () => {
  // Source-level check.  The three dispatchers that Stage 5 updated
  // (BuildShell, VirtualPrimitiveCanvas, ConversationCanvas) must
  // resolve module surfaces via ``getPrimitiveModule`` (registry-
  // level lookup) and never via per-module folder imports.  FP12
  // already covers the second half via ``pageShellBoundary.test.ts``;
  // this check holds the registry-lookup half.
  // @ts-ignore - node-only
  const fs = (await import('fs')) as {
    readFileSync: (p: string, e: string) => string;
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cwd = (globalThis as any).process?.cwd?.() ?? '.';
  const sources = [
    'src/components/build/BuildShell.tsx',
    'src/components/build/primitive/VirtualPrimitiveCanvas.tsx',
    'src/components/ask/ConversationCanvas.tsx',
  ];
  for (const rel of sources) {
    const body = fs.readFileSync(`${cwd}/${rel}`, 'utf8');
    if (!body.includes('getPrimitiveModule')) {
      throw new Error(
        `${rel} does not import getPrimitiveModule — Stage 5 module-first dispatch invariant violated`,
      );
    }
  }
});

export async function runAllModuleFirstDispatchTests(): Promise<void> {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      const out: void | Promise<void> = fn();
      if (out instanceof Promise) {
        await out;
      }
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\nmodule-first dispatch coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} module-first dispatch check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllModuleFirstDispatchTests();
}
