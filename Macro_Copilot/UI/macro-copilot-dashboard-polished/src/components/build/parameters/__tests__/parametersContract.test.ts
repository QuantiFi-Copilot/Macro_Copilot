/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// parametersContract.test.ts — PR5 structural invariants on the
// Parameters tab surface.
// ----------------------------------------------------------------------------
// Locks the PR5 mutation-correctness rules at the source level:
//
//   1. ``StageParameterEditor`` MUST mark every descriptor read-only
//      (node params aren't valid fork slots; making them editable
//      would produce silent no-op forks).
//   2. ``StageParameterEditor`` MUST NOT pretend node-param edits
//      flow into the override map — the canonical mutation surface
//      is ``WorkspaceSlotsPanel``.
//   3. ``WorkspaceSlotsPanel`` MUST exist and MUST source descriptors
//      from ``deriveSlotControlsFromCard`` (slot schema), not
//      ``deriveControlsForStage`` (node params).
//   4. ``ParametersView`` MUST mount ``WorkspaceSlotsPanel`` so
//      editable slots reach the user.
//   5. ``WorkspaceOverridesContext.apply`` continues to call
//      ``forkWorkspace`` with the substrate-shaped
//      ``{slot_overrides, slot_dict_overrides}`` body.
//
// Source-level structural checks via the same ``check`` shim PR3 /
// PR4 use; bundled with esbuild for node execution.
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}

function assertContains(
  haystack: string,
  needle: string,
  label: string,
): void {
  if (!haystack.includes(needle)) {
    throw new Error(`assertContains failed: ${label}\n  looking for: ${needle}`);
  }
}

function assertNotContains(
  haystack: string,
  needle: string,
  label: string,
): void {
  if (haystack.includes(needle)) {
    throw new Error(`assertNotContains failed: ${label}\n  found: ${needle}`);
  }
}

// ----------------------------------------------------------------------------
// Source loader
// ----------------------------------------------------------------------------

let _src: Record<string, string> = {};

async function loadSources(): Promise<Record<string, string>> {
  // @ts-ignore - node-only; esbuild --platform=node resolves it.
  const fs = (await import('fs')) as NodeFs;
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  const files = [
    'src/components/build/parameters/ParametersView.tsx',
    'src/components/build/parameters/StageParameterEditor.tsx',
    'src/components/build/parameters/WorkspaceSlotsPanel.tsx',
    'src/components/build/lib/workspaceOverridesContext.tsx',
  ];
  const out: Record<string, string> = {};
  for (const path of files) {
    const candidates = [`${cwd}/${path}`, `./${path}`];
    let loaded = false;
    for (const p of candidates) {
      try {
        out[path] = fs.readFileSync(p, 'utf8');
        loaded = true;
        break;
      } catch {
        // try next
      }
    }
    if (!loaded) {
      throw new Error(`Could not read source ${path}; cwd=${cwd}`);
    }
  }
  return out;
}

// ----------------------------------------------------------------------------
// Contract checks
// ----------------------------------------------------------------------------

check('StageParameterEditor: forces every descriptor read-only', () => {
  const src = _src['src/components/build/parameters/StageParameterEditor.tsx'];
  // The PR5 discipline is implemented as a ``.map`` that spreads in
  // ``readOnly: true``.  This grep locks that exact pattern so a
  // future refactor that re-introduces editable node-param controls
  // is caught immediately by the test suite.
  assertContains(
    src,
    'readOnly: true',
    'force-read-only mapping present',
  );
});

check('StageParameterEditor: reads node.params (still useful for context)', () => {
  // We don't WANT to delete deriveControlsForStage — the per-stage
  // detail inspector still uses it to populate the read-only stage
  // view.  Confirm the import is preserved so the inspector keeps
  // working.
  const src = _src['src/components/build/parameters/StageParameterEditor.tsx'];
  assertContains(
    src,
    'deriveControlsForStage',
    'preserves node-param derivation for the inspector',
  );
});

check('StageParameterEditor: surfaces a read-only banner', () => {
  const src = _src['src/components/build/parameters/StageParameterEditor.tsx'];
  assertContains(src, 'ReadOnlyBanner', 'read-only banner component');
});

check('WorkspaceSlotsPanel: derives from deriveSlotControlsFromCard', () => {
  const src = _src['src/components/build/parameters/WorkspaceSlotsPanel.tsx'];
  assertContains(
    src,
    'deriveSlotControlsFromCard',
    'imports slot-schema derivation',
  );
  // Must NOT pull from node params.
  assertNotContains(
    src,
    "from './lib/deriveControlsForStage'",
    'does NOT pull node-param derivation',
  );
});

check('WorkspaceSlotsPanel: gates on template_id + bound_slot_values', () => {
  const src = _src['src/components/build/parameters/WorkspaceSlotsPanel.tsx'];
  assertContains(
    src,
    'detail.template_id',
    'template_id presence checked',
  );
  assertContains(
    src,
    'detail.bound_slot_values',
    'bound_slot_values presence checked',
  );
});

check('WorkspaceSlotsPanel: renders an affected-stages chip strip via findStagesForSlot', () => {
  const src = _src['src/components/build/parameters/WorkspaceSlotsPanel.tsx'];
  assertContains(src, 'findStagesForSlot', 'maps slots to stages');
});

check('WorkspaceSlotsPanel: handles non-forkable workspaces explicitly', () => {
  const src = _src['src/components/build/parameters/WorkspaceSlotsPanel.tsx'];
  assertContains(src, 'NotForkableBanner', 'banner component present');
});

check('ParametersView: mounts WorkspaceSlotsPanel', () => {
  const src = _src['src/components/build/parameters/ParametersView.tsx'];
  assertContains(
    src,
    '<WorkspaceSlotsPanel',
    'editable slot surface is mounted',
  );
});

check('ParametersView: preserves PendingOverridesBar + StageList + StageParameterEditor', () => {
  // No regressions to the existing surfaces — the per-stage inspector
  // and the pending-overrides bar are still in the tree.
  const src = _src['src/components/build/parameters/ParametersView.tsx'];
  assertContains(src, 'PendingOverridesBar', 'pending bar mounted');
  assertContains(src, 'StageList', 'stage list mounted');
  assertContains(
    src,
    'StageParameterEditor',
    'stage details inspector mounted',
  );
});

check('WorkspaceOverridesContext: still posts to forkWorkspace with the correct shape', () => {
  // The pre-PR5 fork pipeline must remain intact — PR5 is a derivation-
  // source change, not a transport-layer change.
  const src =
    _src['src/components/build/lib/workspaceOverridesContext.tsx'];
  assertContains(src, 'forkWorkspace(', 'calls forkWorkspace');
  assertContains(src, 'slot_overrides:', 'sends slot_overrides');
  assertContains(src, 'slot_dict_overrides:', 'sends slot_dict_overrides');
  // After PR5, the context still navigates to the new workspace on
  // success — confirming the fork → new-workspace flow.
  assertContains(src, 'navigate(`/workspace/', 'navigates to new workspace');
});

check('WorkspaceOverridesContext: error path does NOT clear queued overrides', () => {
  // PR5 acceptance: "Failed fork does not wipe local edits."
  // We verify by reading the reducer dispatch surface — the apply
  // function should set ``error`` on failure but MUST NOT call
  // ``dispatch({type:'reset'})`` or similar in the catch.
  const src =
    _src['src/components/build/lib/workspaceOverridesContext.tsx'];
  // The catch block sets error + isApplying back to false.  It does
  // NOT call ``dispatch`` or ``resetAll`` (those are exposed to
  // callers for explicit reset, not auto-triggered by failures).
  const catchIdx = src.indexOf('catch (e: unknown)');
  if (catchIdx < 0) {
    throw new Error('catch block not found in apply function');
  }
  const finallyIdx = src.indexOf('finally', catchIdx);
  const catchBlock = src.slice(catchIdx, finallyIdx);
  assertNotContains(
    catchBlock,
    "dispatch({ type: 'reset'",
    'no auto-reset on failure',
  );
  assertNotContains(
    catchBlock,
    'resetAll(',
    'no resetAll call on failure',
  );
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllParametersContractTests(): Promise<void> {
  _src = await loadSources();
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\nparameters contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} parameters-contract check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllParametersContractTests();
}
