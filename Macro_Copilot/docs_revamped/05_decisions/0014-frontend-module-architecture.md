# ADR 0014 — Frontend module architecture: per-primitive folders, pure-spec assembly, derived registries

**Status:** Accepted
**Date:** 2026-05-25
**Builds on:** ADR 0006 (inflation domain agents — first precedent for splitting an instrument family into its own sub-agent + MCP subprocess; the symmetric move on the frontend is splitting a primitive's surfaces into its own module folder), ADR 0013 (futures domain agents — second precedent for the same closed-family pattern at the backend; this ADR is the frontend counterpart).
**Operationalises principles:** P1 (built right — no half-wired surfaces; every module ships every surface it claims), P3 (consistency by contract — every frontend module looks like every frontend module), P5 (honest disclosure — workflow-incompatible / paused / deferred tiers surface honestly, no silent decode-error cards), P8 (closed-family discipline — the surface-tier set is closed; extensions are ADR-recorded), P9 (finance-blind shared layer — shared infrastructure is finance-blind, modules are finance-aware), P10 (single source of truth — central registries are derived from module specs, never hand-authored), P11 (domain isolation — a module's code lives in one folder, not scattered across page roots).
**Scope:** Records the architectural decision to reorganise the frontend codebase from surface-oriented folders (build/, library/, monitor/, ask/) into module-oriented folders (`src/modules/primitives/<tool_name>/`, `src/modules/workflows/<template_id>/`), with central registries derived from pure module specs. The decision binds all new primitive and workflow UI work from the merge of the first migration PR onward; existing code is migrated incrementally per the roadmap in [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md).

---

## Context

The frontend UI today is organised by **surface**, not by primitive. Build, Library, Monitor, Ask each own their entire vertical slice — including per-primitive logic. The result is that adding a new primitive on the frontend requires edits across 6–8 files in 5–6 folders, with no single canonical location anyone can point to and say *"that's the X primitive on the frontend."*

This has produced concrete failures the platform is now paying for:

- **Registry drift.** The backend ships 52 runnable primitives in `_PRIMITIVE_SPECS`; the frontend `RUNNABLE_PRIMITIVE_TOOLS` registry has 34. 18 backend-runnable primitives currently land on the orange *"Could not decode workspace context"* card when opened from Library or hand-off from Ask.
- **Missing tier vocabulary.** Backend distinguishes three states (`_PRIMITIVE_SPECS` ∋ runnable, `WORKFLOW_INCOMPATIBLE_TOOLS` ∋ shipped but output-shape-incompatible, neither ∋ paused/unbuilt). Frontend collapses all three into a binary "known / unknown", missing the workflow-incompatible tier entirely. `get_otr_history_tool` and `calculate_wirp_meeting_pricing_tool` are shipped primitives that the UI treats as unknown.
- **Hardcoded labels.** [`types/library.ts`](../../UI/macro-copilot-dashboard-polished/src/types/library.ts)'s `SUB_AGENT_LABELS` only carries `sovereign_bonds` and `ois`. The four newer sub-agents (`bond_futures`, `policy_futures`, `inflation_indexed_bonds`, `inflation_swaps`) render as raw snake_case in the UI.
- **PR5 invisible.** Backend's PR5 broadening of `pca_yield_curve` + `classify_curve_move` to any `curve_family` is not surfaced in the curve dropdowns: [`CurveAndTenor.ts`](../../UI/macro-copilot-dashboard-polished/src/components/build/model/controls/CurveAndTenor.ts) lists sovereign + OIS only.
- **Stale tests.** `routingCoverage.test.ts` and `genericBuilder.test.ts` assert old tool counts (17, 21) while the current registry holds 34, 38. The test snapshot was not updated when the 14 ADR-0013 factory-ported primitives were added.

The root cause across every item above is the same: **there is no contract that says "when a primitive ships on the backend, here is the matching frontend module that must ship with it, and here is what it owns."** The backend has that contract for primitives, operators, and workflows ([`../02_components/primitive/README.md`](../02_components/primitive/README.md), etc.). The frontend does not.

The platform's scale story depends on the catalogue growing to hundreds of primitives. Each new primitive today requires a non-trivial edit across the frontend; this ADR fixes the missing contract that turns each addition into a single-folder edit.

## Decision

Adopt **module-oriented organisation** for the frontend, mirroring the backend's per-primitive folder pattern. Specifically:

1. **Every backend primitive that needs a frontend representation gets exactly one module folder** at `src/modules/primitives/<tool_name>/` (the folder name MUST equal the backend's canonical `tool_name`, e.g. `src/modules/primitives/calculate_cpi_surprise_tool/`).

2. **Every backend workflow template that needs a frontend representation gets exactly one module folder** at `src/modules/workflows/<template_id>/`.

3. **A module owns the end-to-end UI** for its backend unit: Monitor widget, Build canvas, Ask card, persisted-artifact preview, the THESIS document, the wire types, and the tests. Page shells (`build/`, `library/`, `monitor/`, `ask/`, `layout/`) host modules' surfaces but contain no primitive-specific logic.

4. **Surface tiers are a closed family** (5 runtime-status + 4 capability = 9):
   - Runtime-status: `generic_runnable`, `workflow_incompatible`, `manifest_typed_view`, `paused`, `deferred`.
   - Capability: `custom_build_surface`, `custom_preview_widget`, `monitor_surface`, `ask_surface`.

   Each module declares which tiers it claims; the tier set is the closed family for the surface-capability vocabulary. Extensions to the tier set are ADR-recorded (Stage 4e added `manifest_typed_view` to disambiguate manifest-only typed-view tools — `calculate_butterfly_tool`, `scan_extremes_tool` — from genuinely workflow-incompatible tools).

5. **Central registries are derived, never hand-authored.** `RUNNABLE_PRIMITIVE_TOOLS`, `KNOWN_BACKEND_TOOLS`, `WORKFLOW_INCOMPATIBLE_TOOLS`, `UNSUPPORTED_KNOWN_TOOLS`, `MODEL_REGISTRY`, the Monitor widget catalogue, the node-renderer registry, the dashboard registry — all become functions over the module-spec list. No mutation, no side-effect registration; the assembly is pure.

6. **Modules export pure spec values, not side effects.** Each module's `module.ts` exports `MODULE: PrimitiveModuleSpec` (a value), and a single top-level loader at `src/modules/index.ts` imports every module's spec and collects them into one array. The central registries read that array.

7. **Every module ships a `THESIS.md`** — a required, designed artefact documenting which surfaces this module ships, why those and not others, what the user reads off each surface, what would change the design, and which backend doctrine the module operationalises.

8. **The contract is enforced operationally** via per-module round-trip tests, registry derivation tests, and CI checks that verify every backend-shipped primitive has a corresponding module folder.

The contract that every module satisfies is documented in [`../02_components/frontend_module/README.md`](../02_components/frontend_module/README.md). The procedure for adding a new module is documented in [`../02_components/frontend_module/runbook.md`](../02_components/frontend_module/runbook.md).

### What this ADR does NOT do

- Does not migrate existing primitives in this PR. The migration is incremental and tracked in [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md). Stage 1 closes the registry-coverage gap with static-registry edits (no module folders yet). Stage 2 implements the first module (`calculate_cpi_surprise_tool`) as the reference. Stage 3+ migrates the remainder, one module per PR.
- Does not change the page-shell APIs (`BuildShell`, `LibraryPage`, `MonitorPage`, `AskPage`). Their public contracts remain. They will be refactored *internally* to read from module specs instead of hardcoded entries.
- Does not change the backend in any way. The contract is one-directional: backend ships a unit; frontend ships the matching module.
- Does not affect operators. Operators have no user-facing surface of their own (they appear as DAG nodes rendered by shared infrastructure); they do not get module folders.
- Does not introduce a build-time code generator. The pure-spec assembly is plain TypeScript; the loader is a hand-maintained barrel import, exactly like the backend's `mcp_server.py` imports every tool.

## Alternatives considered

**Keep surface-oriented organisation; add stricter conventions.** Rejected. Convention without folder boundaries is unenforceable at the codebase scale we're heading toward. Every primitive at 100+ primitives means central registries become unauditable, and the "did I update all six places?" failure mode compounds. The backend learned this lesson with per-primitive folders; the frontend must too.

**Per-domain organisation** (mirror backend's `rates_agent/<sub_agent>/tools/<tool_name>/` shape exactly: `src/components/rates/sovereign_bonds/butterfly/`, etc.). Rejected. (a) Cross-domain primitives (`swap_spread` spans sovereign + OIS) become awkward to place. (b) Adds an extra nesting level that buys nothing — the manifest already tags each tool with its sub-agent. (c) When FX / credit agents land, the rates-only directory becomes one of many; flatter `modules/primitives/<tool_name>/` scales better. The backend nests by sub-agent because the backend has per-sub-agent MCP servers; the frontend has no such physical isolation requirement.

**Side-effect registration (`register.ts` mutates global registries on import).** Rejected (per Codex's review and adopted). (a) Import-order risk: a barrel that imports modules in different orders across test files vs production can produce divergent registry state. (b) Test-order risk: a unit test that imports `RUNNABLE_PRIMITIVE_TOOLS` without first having loaded the side-effect barrel sees stale state. (c) Tree-shaking confusion: bundlers cannot safely tree-shake side-effect imports. (d) Hidden mutations: a developer reading `toolNames.ts` cannot see where a registry entry came from. Pure-spec assembly avoids every one of these.

**Code generator** (a script reads each module's `module.ts` and generates static registries). Rejected for V1 because it adds a build step that has to live in CI, complicates local dev, and is overkill for a registry with O(100) entries. Pure-spec assembly is sufficient. A code generator becomes worth reconsidering if the registries grow into the thousands.

**Lazy-load surfaces via dynamic import on first render.** Considered and adopted as **optional per-module** (the module spec declares whether a given surface is statically imported or lazy). Mandatory lazy-loading was rejected because it bloats every module's spec and slows the first-paint of frequently-used surfaces. Heavy surfaces (rich-model builder, complex Monitor widgets) opt in to lazy; lightweight surfaces stay eager.

## Design

### Module-folder layout (the contract)

Every primitive module is a folder under `src/modules/primitives/<tool_name>/` containing:

```
src/modules/primitives/<tool_name>/
├── THESIS.md                ← REQUIRED. The designed artefact.
├── module.ts                ← REQUIRED. Pure spec: `export const MODULE: PrimitiveModuleSpec`.
├── surfaces/                ← Per-surface JSX. ONLY surfaces this module claims.
│   ├── BuildSurface.tsx     ← IF tiers ∋ custom_build_surface
│   ├── PreviewWidget.tsx    ← IF tiers ∋ custom_preview_widget
│   ├── MonitorWidget.tsx    ← IF tiers ∋ monitor_surface AND single-widget legacy shape
│   ├── monitor/             ← IF tiers ∋ monitor_surface AND Stage 4d multi-variant shape
│   │   └── <WidgetName>.tsx ← one file per MODULE.monitorWidgets[i].component
│   └── AskCard.tsx          ← IF tiers ∋ ask_surface
├── types.ts                 ← OPTIONAL. Bespoke wire shapes (most modules reuse shared types).
└── __tests__/
    └── module.spec.ts       ← REQUIRED. Round-trip + tier-claim consistency.
```

Workflow modules follow the same shape at `src/modules/workflows/<template_id>/`, substituting `ResultsDashboard.tsx` for `BuildSurface.tsx`.

The shape is invariant. Different modules differ in what fills the files, not in the file structure itself — directly mirroring backend's primitive doctrine.

### Pure-spec assembly

Each module exports a value:

```ts
// src/modules/primitives/calculate_cpi_surprise_tool/module.ts
import type { PrimitiveModuleSpec } from '@/modules/types';
import { MonitorWidget } from './surfaces/MonitorWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cpi_surprise_tool',
  tiers: ['generic_runnable', 'monitor_surface'],
  displayName: 'CPI Surprise',
  category: 'event_signal',
  oneLineSummary: 'Per-release CPI surprise + rolling release-window z-score for one country.',
  surfaces: {
    monitor: MonitorWidget,
  },
  // ...other declarative metadata...
};
```

The single loader collects all modules:

```ts
// src/modules/index.ts
import { MODULE as cpiSurprise } from './primitives/calculate_cpi_surprise_tool';
import { MODULE as pca } from './primitives/calculate_pca_yield_curve_tool';
// ...one import per module...

export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  cpiSurprise,
  pca,
  // ...
];

export const ALL_WORKFLOW_MODULES: ReadonlyArray<WorkflowModuleSpec> = [
  // ...
];
```

The central registries derive from that array:

```ts
// src/lib/toolNames.ts (the new shape)
import { ALL_PRIMITIVE_MODULES } from '@/modules';

export const RUNNABLE_PRIMITIVE_TOOLS: ReadonlySet<string> = new Set(
  ALL_PRIMITIVE_MODULES
    .filter(m => m.tiers.includes('generic_runnable'))
    .map(m => m.toolName),
);

export const WORKFLOW_INCOMPATIBLE_TOOLS: ReadonlySet<string> = new Set(
  ALL_PRIMITIVE_MODULES
    .filter(m => m.tiers.includes('workflow_incompatible'))
    .map(m => m.toolName),
);

// ...etc. for KNOWN_BACKEND_TOOLS, UNSUPPORTED_KNOWN_TOOLS, etc.
```

No mutation. Deterministic order. Tree-shakeable. Tests can import individual specs or the full array.

### Surface tiers (the closed family)

| Tier | Means |
|---|---|
| `generic_runnable` | Backend declares in `_PRIMITIVE_SPECS`. UI routes through `GenericPrimitiveBuilder` + `AutoRenderer`. |
| `custom_build_surface` | Bespoke Build canvas. Module ships `surfaces/BuildSurface.tsx`. |
| `custom_preview_widget` | Per-tool persisted-artifact card. Module ships `surfaces/PreviewWidget.tsx`. |
| `monitor_surface` | One or more Monitor bento widgets.  Module ships EITHER `surfaces/MonitorWidget.tsx` (single-widget legacy shape) referenced from `MODULE.surfaces.monitor`, OR `surfaces/monitor/<WidgetName>.tsx` per entry of `MODULE.monitorWidgets[]` (Stage 4d multi-variant shape — used when one backend tool ships multiple catalog widgets, e.g. `calculate_curve_spread_tool` → `curve_spreads` + `spread_chart`). |
| `ask_surface` | Bespoke Ask result/message card. Module ships `surfaces/AskCard.tsx`. |
| `workflow_incompatible` | Backend declares in `WORKFLOW_INCOMPATIBLE_TOOLS`. Cannot route through generic builder. Module surfaces honest treatment. Mutually exclusive with `generic_runnable`. |
| `manifest_typed_view` | (Stage 4e) Backend declares in `_MANIFEST_ONLY_BUILD_TOOLS`: typed-detail endpoint ships but no `_PRIMITIVE_SPECS` entry, so the workflow bridge can't dispatch the tool.  Build's typed view (`MODULE.typedView`) is the live surface; the generic builder is intentionally NOT offered.  Mutually exclusive with the other runtime tiers. |
| `paused` | Manifest-declared, no backend implementation yet. Module surfaces paused card with reason. Mutually exclusive with `generic_runnable`. |
| `deferred` | Reserved name; not yet built. Module exists to document intent; no surfaces. |

Validation rules (enforced by per-module test):
- Every primitive module MUST claim exactly one of `{generic_runnable, workflow_incompatible, paused, deferred}` — the runtime-status tier.
- A module with `generic_runnable` MUST NOT also claim `workflow_incompatible` or `paused`.
- A module with `paused` MUST NOT claim `generic_runnable`.
- Optional capability tiers (`custom_build_surface`, `custom_preview_widget`, `monitor_surface`, `ask_surface`) may be combined freely with any runtime-status tier.
- A module claiming a capability tier MUST ship the corresponding `surfaces/<Name>.tsx` file (test asserts file presence).

Full per-tier semantics live in [`../02_components/frontend_module/tiers.md`](../02_components/frontend_module/tiers.md).

### What stays where (the non-module architecture)

| Code | Lives in | Responsibility |
|---|---|---|
| Page shells (BuildShell, LibraryPage, MonitorPage, AskPage, AppShell, Sidebar, ChatDrawer, TopNav) | `src/components/layout/`, `src/components/build/`, etc. | Route URLs → choose layout → host modules' surfaces. MUST NOT contain primitive-specific logic. |
| Shared render shells (AutoRenderer, RichModelWidget, GenericPrimitiveBuilder, typed views like SpreadView / ButterflyView / YieldView / ScannerView / RegimeView, BuilderCanvas, OutputCanvas) | `src/components/build/` subfolders (today); future move to `src/components/shared/render/` | Reusable widget chrome that multiple modules slot into. Finance-blind in spirit (P9 analog): they shape pixels, not domain math. |
| DAG renderer + lineage parsing | `src/components/build/dag/` | Renders any workflow DAG node — primitives, operators, terminal artifacts — without knowing what they compute. |
| Realtime / WebSocket session | `src/context/CopilotContext.tsx` | Singleton WebSocket, message buffer reconciliation, turn lifecycle. |
| Services (REST clients) | `src/services/` | Thin wrappers over `/api/v1/...`. One file per backend route family. |
| Types (wire shapes) | `src/types/` | Mirrors backend Pydantic schemas. Modules import from here; bespoke per-module shapes go in the module's `types.ts`. |
| Hooks | `src/hooks/` | Cross-surface data hooks (e.g. `useRatesData`, `useWorkspaceDetail`, `useLibraryManifest`). |
| UI atoms | `src/components/ui/` | Reusable visual primitives (`Sparkline`, `Card`, `Modal`, etc.). |
| Central registries | `src/lib/` (toolNames, modelRegistry) | Derived from `ALL_PRIMITIVE_MODULES`. No hand-authored entries. |
| Module loader | `src/modules/index.ts` | Imports every module spec, exposes `ALL_PRIMITIVE_MODULES` + `ALL_WORKFLOW_MODULES`. Hand-maintained barrel (one import line per module). |

The non-module categories are documented in [`../02_components/frontend_infrastructure/`](../02_components/frontend_infrastructure/) and [`../02_components/frontend_registries/`](../02_components/frontend_registries/).

## Consequences

**Positive:**

- A new primitive on the backend produces a single-folder edit on the frontend. The folder name equals the backend's `tool_name`; `grep -r` from either side finds the same identifier.
- The 18 currently-missing primitives become surfaceable incrementally, one module at a time, without "registry catch-up" PRs that risk broad blast radius.
- The `workflow_incompatible` tier captures the three backend-declared cases (`classify_curve_move_tool`, `get_otr_history_tool`, `calculate_wirp_meeting_pricing_tool`) honestly. The user sees a real surface, not an orange decode-error card.
- The central registries become *derived* artefacts. A drift between backend and frontend becomes visible as either a missing module (the loader cannot import it) or a failed test (the module's tier-claim test fails). Silent drift is structurally impossible.
- Per-module THESIS docs force the question *"why these surfaces, why this layout?"* — an artefact the design discipline today has nowhere to land.
- Handoff becomes possible: a new engineer owns CPI surprise = a new engineer owns one folder.

**Negative / known trade-offs:**

- The migration is non-trivial. ~20 existing surfaces have to move from page-folders into module-folders. The work is split across many small PRs (one module per PR) per the roadmap; no big-bang refactor.
- A primitive that genuinely surfaces on N surfaces still requires N file additions in its module folder. Cohesion has a cost. The pay-off is that the cohesion now lives in one place.
- The loader at `src/modules/index.ts` is hand-maintained. A developer adding a new module must add the import. The per-module round-trip test catches the omission, but the discipline depends on the test running.
- Page shells need to be refactored to consume module specs instead of hardcoded entries. This is a one-time cost.

## Rollout plan

1. **Stage 0 — Documentation (THIS PR).** The frontend constitution (this ADR + 21 supporting docs across `00_thesis/`, `01_architecture/`, `02_components/`, `03_standards/`, `04_quality_and_evals/`, `06_roadmap/`). No code changes.
2. **Stage 1 — Registry catch-up + test repair.** Static-registry edits to add the 18 backend-runnable primitives + the 2 workflow-incompatible to the current `toolNames.ts`. Update the 3 failing tests with current expected counts. No module folders yet; this stage is a holding action that closes the immediate coverage gap before the module migration begins. Documented as the explicit Stage 1 in [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md).
3. **Stage 2 — Reference module.** Implement `calculate_cpi_surprise_tool` as the first module under the new architecture. Establishes the pattern under real load (it claims `generic_runnable` + `monitor_surface` + `ask_surface`, exercising multiple tiers).
4. **Stage 3+ — Incremental migration.** Migrate one primitive per PR. Order driven by priority in [`../06_roadmap/frontend_migration.md`](../06_roadmap/frontend_migration.md). Each migration removes that primitive's entries from the hand-authored portion of the central registries and replaces them with the module's spec.
5. **Stage N — Cleanup.** Once every primitive is a module, the hand-authored portions of the central registries are deleted; registries become pure derivations.

The bound: every PR that adds a new primitive on the backend after Stage 1 lands MUST land its frontend module in the same PR or a coupled follow-up. The "missed module" failure mode that produced the current 18-tool gap is closed by review discipline + the CI check that backend `_PRIMITIVE_SPECS` and frontend `ALL_PRIMITIVE_MODULES` agree.

## Rollback plan

If the module architecture proves problematic in practice (the bound is wrong; the surface-tier set is incomplete; the assembly pattern has unanticipated build-time consequences), rollback is a four-step procedure:

1. Stop migrating; pause Stage 3+.
2. Revert any in-flight module PRs.
3. Restore the hand-authored portions of central registries from git history.
4. Decide on the next architecture via a follow-up ADR. The migrated modules are not lost — they continue to work; only new work pauses.

Because each migration is one PR and one module, rollback granularity is one primitive. There is no committing point at which "we've gone too far to undo." This is by design.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial decision. Accepted. |
