# Frontend Module — Runbook

> The step-by-step procedure for adding a new frontend module. Counterpart to [`../primitive/runbook.md`](../primitive/runbook.md). Follow this every time a backend primitive or workflow template ships and needs a frontend representation.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** procedural. Update when the contract in [`README.md`](README.md) changes.
**See also:** [`README.md`](README.md) — the contract every module satisfies; [`tiers.md`](tiers.md) — full per-tier semantics; [`thesis_template.md`](thesis_template.md) — the THESIS template.

---

## When to use this runbook

You are adding a frontend module when:
- A backend primitive shipped (or is about to ship) and needs a UI representation.
- A backend workflow template shipped (or is about to ship) and needs a results dashboard or status card.
- A name is being reserved (`deferred` tier) for a planned backend feature.

You are NOT using this runbook when:
- You're adding a Monitor widget for a tool that already has a module → edit the existing module folder.
- You're adding a typed view for a tool that already has a module → edit the existing module's `module.ts` (set `typedView`) + add the matching component in `src/components/shared/render/`.
- You're modifying a page shell → that's not a module; it's shared infrastructure / page-shell work.
- You're adding an operator → operators do not get modules (FP13).

## Pre-flight checklist

Before starting:

- [ ] **Confirm the backend artifact.** The primitive's `tool_name` exists in `_PRIMITIVE_SPECS` or `WORKFLOW_INCOMPATIBLE_TOOLS`, OR is being reserved for a deferred feature. Verified by `grep <tool_name> rates_agent/workflows/__init__.py`.
- [ ] **Identify the runtime-status tier.** See [`tiers.md`](tiers.md) to pick exactly one of `{generic_runnable, workflow_incompatible, paused, deferred}`.
- [ ] **Decide capability tiers.** Default to none. Add each only when justified (FM4). See [`tiers.md`](tiers.md) for when-to-claim guidance.
- [ ] **Read the backend's ToolCard.** Either via `GET /api/v1/tools/{tool_name}` against a running backend, or by reading the tool's `schemas.py` + `config.yaml`. The metadata sourcing (FM5) starts here.
- [ ] **Sketch the THESIS.md.** Answer the five questions roughly before writing code. The exercise frequently reveals that the tier set is wrong.

If the pre-flight reveals the module needs a tier that doesn't exist yet (a new tier proposal), STOP. File an ADR amending the closed family first; don't ship a module with a tier that isn't in the family.

## Step 1 — Create the folder

```bash
TOOL_NAME=calculate_cpi_surprise_tool   # example
mkdir -p src/modules/primitives/$TOOL_NAME/surfaces
mkdir -p src/modules/primitives/$TOOL_NAME/__tests__
```

Folder name MUST equal `tool_name` exactly (FM1). For workflows: `src/modules/workflows/<template_id>/` with the same internal shape.

## Step 2 — Write THESIS.md

Copy the template from [`thesis_template.md`](thesis_template.md) into `THESIS.md`. Answer all five questions in order:

1. What surfaces does this module ship?
2. What does the user read off each surface?
3. Why these surfaces and not others?
4. What would change the design?
5. Which backend doctrine does this module operationalise?

THESIS first. The exercise sharpens what surfaces matter and what tier set is correct.

## Step 3 — Write `module.ts`

The skeleton:

```ts
// src/modules/primitives/<tool_name>/module.ts
import type { PrimitiveModuleSpec } from '@/modules/types';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity
  toolName: '<tool_name>',

  // FM3 — tier claims
  tiers: [/* runtime-status tier + any capability tiers */],

  // FM5 — display metadata (mirrors backend ToolCard)
  displayName: '<Title Case>',
  category: '<category from manifest>',
  oneLineSummary: '<one sentence from methodology.what_it_does>',

  // FM5 — defaults + paramHints + interpretation cards
  defaultParams: { /* runnable form values */ },
  paramHints: { /* per-field control hints; omit fields that infer correctly */ },
  interpretationCards: [ /* optional rich-card content */ ],

  // FM9 — routing claims
  typedView: null,        // or 'spread' / 'cross_market' / ... per tiers.md
  richModel: false,       // true for rich-model primitives only

  // FM8 — surface refs (only populated for claimed capability tiers)
  surfaces: { /* monitor: ..., build: ..., preview: ..., ask: ... */ },

  // FM6 — unsupported reason (required for workflow_incompatible | paused | deferred)
  unsupportedReason: null,
};
```

Fill in only the fields relevant to the tier set. Linter + tests will catch missing or inconsistent fields.

## Step 4 — Write surface files (one per capability tier)

For each capability tier in `MODULE.tiers`, create the corresponding file:

### `custom_build_surface` → `surfaces/BuildSurface.tsx`

```tsx
import type { FC } from 'react';
import type { BuildSurfaceProps } from '@/modules/types';

const BuildSurface: FC<BuildSurfaceProps> = (props) => {
  // ... JSX ...
};

export default BuildSurface;
```

For rich-model primitives, the typical body delegates to the shared `BuilderCanvas`:

```tsx
import { BuilderCanvas } from '@/components/shared/render/BuilderCanvas';

const BuildSurface: FC<BuildSurfaceProps> = (props) => (
  <BuilderCanvas toolName={props.toolName} initialParams={props.initialParams} />
);
```

For workflow-incompatible primitives with a typed-detail endpoint, the body delegates to the typed view:

```tsx
import { RegimePrimitiveView } from '@/components/shared/render/typed/RegimePrimitiveView';

const BuildSurface: FC<BuildSurfaceProps> = (props) => (
  <RegimePrimitiveView params={props.params} />
);
```

### `custom_preview_widget` → `surfaces/PreviewWidget.tsx`

```tsx
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from '@/components/shared/render/RichModelWidget';

const PreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget {...props} toolName="<tool_name>" />
);

export default PreviewWidget;
```

### `monitor_surface` → `surfaces/MonitorWidget.tsx`

```tsx
import type { FC } from 'react';
import type { MonitorWidgetProps } from '@/modules/types';

const MonitorWidget: FC<MonitorWidgetProps> = (props) => {
  // ... JSX rendering the bento card ...
};

export default MonitorWidget;
```

### `ask_surface` → `surfaces/AskCard.tsx`

```tsx
import type { FC } from 'react';
import type { AskCardProps } from '@/modules/types';

const AskCard: FC<AskCardProps> = (props) => {
  // ... JSX rendering the chat-result card ...
};

export default AskCard;
```

After creating each surface, update `MODULE.surfaces`:

```ts
import BuildSurface from './surfaces/BuildSurface';
import MonitorWidget from './surfaces/MonitorWidget';

// ... in MODULE:
surfaces: {
  build: BuildSurface,
  monitor: MonitorWidget,
},
```

## Step 5 — Write `__tests__/module.spec.ts`

The standard test boilerplate:

```ts
import { describe, it, expect } from 'vitest';
import { assertStandardModuleInvariants } from '@/modules/__test-utils';
import { MODULE } from '../module';

describe('module spec', () => {
  it('satisfies the standard module invariants', () => {
    assertStandardModuleInvariants(MODULE, {
      folderName: '<tool_name>',
      // optional: extra per-module assertions
    });
  });

  // Add per-module assertions for any module-specific invariants
  // (e.g., 'category is event_signal', 'defaultParams.country is US').
});
```

The helper `assertStandardModuleInvariants` is implemented in `src/modules/__test-utils.ts` and asserts FM1–FM12 invariants 1–8. Invariant 9 (loader presence) is asserted by a separate top-level test in `src/modules/__tests__/loader.spec.ts` that runs over all modules.

## Step 6 — Register in the central loader

Open `src/modules/index.ts` and add the import + array entry in alphabetical position:

```ts
// ...existing imports...
import { MODULE as calculate_cpi_surprise_tool } from './primitives/calculate_cpi_surprise_tool';
// ...existing imports...

export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  // ...existing entries...
  calculate_cpi_surprise_tool,
  // ...existing entries...
];
```

Keep the imports + the array entries alphabetically sorted by `toolName`. The linter / formatter enforces this; out-of-order entries fail CI.

## Step 7 — Verify locally

Run the per-module + parity checks:

```bash
# Per-module round-trip
npm run test:modules -- src/modules/primitives/<tool_name>

# Full registry derivation tests
npm run test:build

# Cross-side parity check (backend vs frontend module set)
python tools/check_module_parity.py
```

All three MUST pass before opening the PR. If any fail:
- Round-trip failure → tier ↔ files ↔ THESIS mismatch.
- Build-test failure → registry derivation broken.
- Parity failure → backend has a tool with no module, or this module references a non-existent backend tool.

## Step 8 — Manual smoke test

Spin up the dev server + a backend, then:

1. **Library:** open `/library`, search for the tool, click "Open in Build" from the detail drawer. Confirm the right canvas mounts (generic builder / typed view / rich-model / unsupported card per the tier).
2. **Build (direct URL):** open `/workspace?context=<encoded>` with a hand-built context. Confirm the same canvas.
3. **For `generic_runnable` modules:** click Run on the canvas. Confirm a real result lands without errors.
4. **For `monitor_surface` modules:** navigate to `/rates`, add the widget from the catalog modal. Confirm it renders against live data.
5. **For `ask_surface` modules:** prompt Ask with something that routes to this tool. Confirm the bespoke AskCard renders, not the generic AssistantResearchCard.
6. **For `workflow_incompatible | paused | deferred` modules:** confirm the unsupported card renders with the per-module `unsupportedReason.reason` text.

If any smoke test reveals a routing or rendering bug, fix BEFORE opening the PR.

## Step 9 — Open the PR

PR title:
- For a new primitive's module: `feat(modules): add <tool_name> frontend module (<tier list>)`
- For a deferred reservation: `chore(modules): reserve <name> (deferred — <reason>)`
- For a workflow module: `feat(modules): add <template_id> workflow module`

PR description MUST include:

1. **Backend artifact.** Link to the backend PR that landed (or is landing) the primitive / workflow. Confirm `_PRIMITIVE_SPECS` (or analog) entry.
2. **Tier set.** Enumerate every claimed tier with a one-line justification each.
3. **Surface list.** List every surface file created.
4. **THESIS summary.** A two-paragraph summary of THESIS.md (NOT a copy of the full file; the file lives in the module folder).
5. **Manual-smoke screenshots.** One screenshot per surface confirmed in Step 8.
6. **Test output.** Paste the `npm run test:modules` + `npm run test:build` + `check_module_parity.py` output showing all green.

## Common failures + how to fix

| Failure | Cause | Fix |
|---|---|---|
| Round-trip test: "folder name does not match toolName" | Folder name and `MODULE.toolName` disagree | Rename the folder OR update `toolName`. The folder is the source of truth (FM1). |
| Round-trip test: "missing surface file for claimed tier" | `tiers ∋ monitor_surface` but `surfaces/MonitorWidget.tsx` doesn't exist | Either create the file OR remove the tier from `tiers`. |
| Round-trip test: "THESIS surfaces don't match tier set" | THESIS Question 1 lists surfaces that don't match `MODULE.tiers` | Update THESIS Question 1 to enumerate exactly the tiers in `MODULE.tiers`. |
| Build test: "RUNNABLE_PRIMITIVE_TOOLS expected N, got N+1" | Registry-derivation test snapshot needs update | Update the snapshot count in the test (the snapshot is a tripwire; bumping it intentionally on new modules is correct). |
| Parity check: "backend has X, frontend doesn't" | A backend primitive shipped without a matching frontend module | Add the module in THIS PR or the same coupled PR. The bound is that backend + frontend ship together. |
| Parity check: "frontend has X, backend doesn't" | This module's `toolName` doesn't exist on the backend | Either the backend PR hasn't landed yet (wait), OR the toolName is misspelled (fix). |
| Manual smoke: "Open in Build" lands on decode-error card | The module is registered but `contextDecoder` doesn't route correctly | Check the `typedView` / `richModel` / runtime-tier claims. The decoder priority is: `richModel` > `typedView` > `generic_runnable` > `workflow_incompatible` > `paused` > decode-error. |

## What this runbook does NOT cover

- **Migrating an existing primitive from page-folder organisation to a module folder.** That's [`../../06_roadmap/frontend_migration.md`](../../06_roadmap/frontend_migration.md) — same shape with extra steps for moving legacy code.
- **Adding a new tier to the closed family.** That requires an ADR. See [`tiers.md`](tiers.md) § *Closed-family extensions*.
- **Refactoring shared infrastructure** (AutoRenderer, the DAG renderer, etc.). That's shared-infrastructure work, not module work. See [`../frontend_infrastructure/README.md`](../frontend_infrastructure/README.md).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial runbook. 9 steps from pre-flight to PR open, common failures table, scope exclusions. |
