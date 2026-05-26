# Frontend Module

> The contract every frontend module in the Macro Copilot platform must satisfy — what makes something a frontend module at all, what makes it *standard*, and the design principles that govern when to ship one (and when not to). The frontend's analog of [`../primitive/README.md`](../primitive/README.md). **Agent-agnostic by design**: the principles hold whether the agent is rates today or FX, credit, equities, commodities, options tomorrow.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing component contract. Changes require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** [FP1–FP13](../../00_thesis/03_frontend_thesis.md), P1 (built right), P3 (consistency by contract), P5 (honest disclosure), P8 (closed-family discipline — the tier set), P9 (finance-blind shared layer — frontend modules are finance-aware), P10 (single source of truth — module specs author the registries), P11 (domain isolation at the module-folder level).
**See also:** [`runbook.md`](runbook.md) — the procedure for adding a new module. [`tiers.md`](tiers.md) — full per-tier semantics. [`thesis_template.md`](thesis_template.md) — the THESIS.md template.

---

## What this folder is

This is the contract every frontend module must satisfy. A frontend module is the L6.1/L6.2 unit that owns the end-to-end UI of one named backend unit (a primitive, a workflow template). It is the cohesion boundary the platform's user-facing layer organises around.

This document is organised as **principles, not archetypes.** Modules vary widely in surface count and JSX shape (a snapshot primitive may have only a generic-builder surface; a rich-model primitive has a bespoke Build canvas; an event primitive ships Monitor + Ask + Build). The shapes are *emergent patterns* of how the principles manifest against each primitive's specific needs — not enumerative categories. A new module contributor reads the principles, applies them to their specific primitive, and the *shape* falls out as a consequence.

The 12 numbered principles (**FM1–FM12**) below are organised in the order they bind a contributor: definitional (FM1–FM3, *is this actually a module?*) → admission (FM4–FM6, *should it be built at all?*) → standardness (FM7–FM10, *what does "standard" mean operationally?*) → operational (FM11–FM12, *the build conventions every standard module follows*).

## What a frontend module *is* — the universal contract

Every primitive module is a folder under `src/modules/primitives/<tool_name>/` containing the following files. Required files MUST exist; optional files exist only when the corresponding tier is claimed.

```
src/modules/primitives/<tool_name>/
├── THESIS.md                       # REQUIRED — designed artefact (FM10)
├── module.ts                       # REQUIRED — pure spec: `export const MODULE: PrimitiveModuleSpec`
├── surfaces/                       # Per-surface JSX directory
│   ├── BuildSurface.tsx            # IF tiers ∋ custom_build_surface
│   ├── PreviewWidget.tsx           # IF tiers ∋ custom_preview_widget
│   ├── MonitorWidget.tsx           # IF tiers ∋ monitor_surface AND single-widget legacy shape
│   ├── monitor/                    # IF tiers ∋ monitor_surface AND multi-variant Stage 4d shape
│   │   └── <WidgetName>.tsx        # one file per MODULE.monitorWidgets[i].component
│   └── AskCard.tsx                 # IF tiers ∋ ask_surface
├── types.ts                        # OPTIONAL — bespoke wire shapes (most modules reuse shared types)
└── __tests__/
    └── module.spec.ts              # REQUIRED — round-trip + invariant tests (FM11)
```

Workflow modules follow the same shape at `src/modules/workflows/<template_id>/`, with `surfaces/ResultsDashboard.tsx` substituting for `surfaces/BuildSurface.tsx`.

This shape is invariant across every module in the platform regardless of tier set, kind (primitive vs workflow), agent, or domain. **Different modules differ in what fills the files, not in the file structure itself.**

The `MODULE` value is a pure spec:

```ts
// src/modules/primitives/calculate_cpi_surprise_tool/module.ts
import type { PrimitiveModuleSpec } from '@/modules/types';
import { MonitorWidget } from './surfaces/MonitorWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity. Folder name MUST equal toolName.
  toolName: 'calculate_cpi_surprise_tool',

  // FM3 — surface-tier capability declaration.
  tiers: ['generic_runnable', 'monitor_surface'],

  // FM7 — display metadata (sourced from the backend's tool metadata).
  displayName: 'CPI Surprise',
  category: 'event_signal',
  oneLineSummary: 'Per-release CPI surprise + rolling release-window z-score for one country.',

  // FM8 — surface references. Only present for claimed capability tiers.
  surfaces: {
    monitor: MonitorWidget,
  },

  // FM9 — typed view / rich model claims (drives contextDecoder).
  // Empty here: this module routes through the generic builder.
  typedView: null,
  richModel: null,

  // FM5 — defaults + paramHints + interpretation cards for the
  // generic builder surface. Sourced from backend's ToolCard +
  // module-specific UX choices.
  defaultParams: {
    country: 'US',
    lookback_releases: '60',
  },
  paramHints: {
    country: { control: 'enum' },
    lookback_releases: { control: 'lookback_slider', presets: [12, 24, 60, 120] },
  },
  interpretationCards: [
    {
      headline: 'Surprise convention',
      body: 'CPI YoY actual minus consensus_median, in percentage points. Positive = upside surprise.',
    },
  ],

  // FM6 — unsupported reason (required when paused/workflow_incompatible).
  // Null here: this module is generic_runnable.
  unsupportedReason: null,
};
```

The spec is a value, not a function. The module-spec collector at `src/modules/index.ts` imports every module's `MODULE` and exposes the full list as `ALL_PRIMITIVE_MODULES`.

## What a frontend module is *not*

Boundary statements that prevent the most common misclassifications:

- **Not a page shell.** Page shells (`BuildShell.tsx`, `LibraryPage.tsx`, `MonitorPage.tsx`, `AskPage.tsx`) route URLs and host modules' surfaces. They do not own primitive logic. A "Build page" is not a module; `calculate_curve_spread_tool` is a module that contributes a Build surface.
- **Not a shared render shell.** AutoRenderer, RichModelWidget, GenericPrimitiveBuilder, typed views (Spread / Butterfly / Yield) are shared infrastructure — finance-blind chrome that multiple modules slot into. They live in `src/components/shared/`, not in any module folder.
- **Not an operator.** Operators (`align_series`, `conditional_aggregate`, etc.) have no user-facing surface of their own; they appear as DAG nodes rendered by the shared DAG renderer. Operators do not get modules.
- **Not an aggregate of unrelated primitives.** A module owns ONE primitive (FM2). A folder grouping three different primitives because their UIs look similar is wrong — each primitive's UI lives in its own module.
- **Not a tier-less general-purpose UI helper.** A module declares at least one runtime-status tier (`generic_runnable`, `workflow_incompatible`, `paused`, `deferred`). A folder under `src/modules/` that doesn't fit the tier vocabulary is misplaced — it likely belongs in `src/components/shared/` or `src/lib/`.

## How to use this document

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. That alone gives you the whole spec. The **Why**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read this file from top to bottom. Look up the specific principle by ID when it comes up. Cite by ID (`FM3`, `FM8`) in commit messages, PR comments, and code review, exactly the way the backend cites the PR-numbers from [`../primitive/README.md`](../primitive/README.md). The three namespaces are deliberately separate: **P-numbers are platform-wide; PR-numbers are backend-primitive-specific; FP-numbers are frontend-thesis-wide; FM-numbers are frontend-module-specific.** A frontend PR may cite all four.

## Quick index — the FM-numbers

| ID | Group | Principle | One-line rule |
|---|---|---|---|
| **FM1** | I | Module-name identity | The folder name equals the backend's canonical `tool_name` exactly. |
| **FM2** | I | One module, one backend unit | A module owns the UI of exactly one primitive or workflow template; no aggregates, no umbrella modules. |
| **FM3** | I | Surface-tier capability declaration | A module declares its surface tiers as a subset of the closed family; the file shape obeys the tier set. |
| **FM4** | II | Tier-set parsimony | Claim the smallest tier set that satisfies the use case; default to `generic_runnable` alone unless a bespoke surface is materially better. |
| **FM5** | II | Display-metadata sourcing | Display name, category, one-line summary, default params, interpretation cards mirror the backend `ToolCard` where possible; deliberate divergence is THESIS-documented. |
| **FM6** | II | Unsupported-reason surfacing | A module with `tier ∋ workflow_incompatible | paused | deferred` MUST provide an `unsupportedReason` that the UI surfaces verbatim. |
| **FM7** | III | Pure-spec assembly (no side effects) | `module.ts` exports a value, not a side effect. Per-tool registrations happen at the central loader, not at module-eval time. |
| **FM8** | III | Surface-file contract | Each claimed capability tier ships exactly the file the contract specifies, named exactly as specified, default-exporting a typed component. |
| **FM9** | III | Routing-claim disclosure | `typedView` + `richModel` fields on the spec drive `contextDecoder`'s typed-view and rich-model routing; modules claim them only when they ship the matching shared-infrastructure entry. |
| **FM10** | III | THESIS discipline | Every module ships a `THESIS.md` answering the five required questions; THESIS staleness vs the spec is a build error. |
| **FM11** | IV | Round-trip test | Every module ships `__tests__/module.spec.ts` proving folder-name ↔ spec ↔ tiers ↔ surfaces ↔ THESIS consistency. |
| **FM12** | IV | Loader-presence | Every module is imported in `src/modules/index.ts` and contributes exactly one entry to `ALL_PRIMITIVE_MODULES` or `ALL_WORKFLOW_MODULES`. |

---

## Group I — Definitional: is this actually a frontend module?

The first question. If the answer to any of FM1–FM3 is "no, this is not a frontend module," the conversation stops and the proposal goes to a different shape (a page-shell extension, a shared-infrastructure addition, or a non-component change).

### FM1 — Module-name identity

**Rule.** The folder name under `src/modules/primitives/` (or `src/modules/workflows/`) equals the backend's canonical `tool_name` (or `template_id`) EXACTLY. No short names, no friendlier aliases, no kebab-case translation.

A primitive module folder name matches `^(get|calculate|compute|scan|build|classify|policy_futures_get)_[a-z0-9_]+_tool$` — i.e., the same identifier that appears in the backend's `_PRIMITIVE_SPECS` or `WORKFLOW_INCOMPATIBLE_TOOLS` dictionary.

A workflow module folder name matches `^[a-z0-9_]+$` — the `template_id`, e.g. `event_study`, `regime_conditioned_relationship`.

**Why.** Identifier parity with the backend. `grep calculate_cpi_surprise_tool` from either repo half finds the same string. The "frontend has a different name for the thing" failure mode — the source of half the registry-drift today — is structurally impossible. The folder name *is* the identifier; the spec just re-asserts it.

**Verify.**
- `module.ts.toolName === <folder-name>`. The per-module round-trip test (FM11) asserts this.
- `ls src/modules/primitives/` produces a list that matches `_PRIMITIVE_SPECS.keys() ∪ WORKFLOW_INCOMPATIBLE_TOOLS.keys() ∪ deferred-reservations` exactly. The cross-side parity test (`tools/check_module_parity.py`) asserts this.

**Anti-patterns.**
- Folder `src/modules/primitives/pca/` with `module.ts` declaring `toolName: 'calculate_pca_yield_curve_tool'`.
- Folder `src/modules/primitives/cpi-surprise/` (kebab-case translation).
- Folder for a backend tool that does not exist (typo, removed, never landed).

**Exceptions.** None.

**Relates to.** P10, FP1, [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md).

### FM2 — One module, one backend unit

**Rule.** A module owns the UI of exactly one named backend unit (one primitive `tool_name`, or one workflow `template_id`). Modules do not aggregate multiple primitives. Modules do not share a folder.

If two primitives have *similar UI* (e.g. a sovereign yield level and a real yield level), they get TWO module folders, possibly each with a one-line `BuildSurface.tsx` that delegates to the same shared render shell. The folder-per-primitive boundary is the cohesion boundary; the shared rendering happens in `src/components/shared/`.

**Why.** Identity (FM1) implies cardinality. Two primitives sharing a module would require disambiguation in every consumer — *which* primitive is this module about? — and that's exactly the registry-drift mode the module pattern is designed to prevent. The folder owns one identifier; consumers know which.

The shared-rendering-shell pattern handles UI similarity without violating cardinality: two modules whose `BuildSurface.tsx` is a 5-line delegation to `<YieldSurfaceShell toolName={...} />` is normal.

**Verify.**
- A folder contains exactly one `module.ts` exporting exactly one `MODULE` value.
- `MODULE.toolName` is a single string.
- A primitive with a "variant" (e.g. sovereign and real yield) lives in two separate folders, not one.
- A module's surfaces import shared render shells from `src/components/shared/`; they do not import another module's surfaces.

**Anti-patterns.**
- Folder `src/modules/primitives/yield_levels/` with a `module.ts` exporting `{ TOOLS: ['get_yield_levels_tool', 'get_real_yield_level_tool'] }`.
- A `surfaces/` folder containing JSX for two distinct primitives behind a conditional.
- A "shared module" pattern where multiple primitives import from a common module folder.

**Exceptions.** None.

**Relates to.** FM1, FP1, FP2, the backend's PR2 (one primitive, one concept).

### FM3 — Surface-tier capability declaration

**Rule.** A module's `MODULE.tiers` is a non-empty subset of the closed family (Stage 4e: 5 runtime-status + 4 capability = 9):

```
SurfaceTier =
  | 'generic_runnable'         // runtime-status — claim ONE of these five
  | 'workflow_incompatible'    //
  | 'manifest_typed_view'      //  Stage 4e — manifest-only with typed-detail endpoint
  | 'paused'                   //
  | 'deferred'                 //
  | 'custom_build_surface'     // capability — claim any combination
  | 'custom_preview_widget'    //
  | 'monitor_surface'          //
  | 'ask_surface'              //
```

Exactly one runtime-status tier is claimed per module. Capability tiers are unconstrained except:
- `paused` and `deferred` modules MAY claim capability tiers (a paused module may still have a Monitor widget showing "this is paused").
- `workflow_incompatible` modules SHOULD claim `custom_build_surface` (so the user has a real Build affordance instead of just the paused card).
- `manifest_typed_view` modules MUST claim `custom_build_surface` (the typed view IS the live surface).
- `deferred` modules SHOULD NOT claim any capability tier (they have nothing to ship yet).

For each claimed capability tier, the corresponding file in `surfaces/` MUST exist (see FM8 for the file-naming contract).

**Why.** Predictability. A reader of `module.ts.tiers` knows immediately which surfaces this module provides. The closed family (per P8) means the tier vocabulary cannot drift; ADR-recorded extensions only. The mutually-exclusive runtime-status rule prevents nonsense ("paused but runnable").

**Verify.**
- `MODULE.tiers` is a subset of the nine values above.
- Exactly one of `{generic_runnable, workflow_incompatible, manifest_typed_view, paused, deferred}` is present.
- For each capability tier in `tiers`, the corresponding `surfaces/<Name>.tsx` exists and `MODULE.surfaces.<key>` references it (or, for `monitor_surface`, EITHER `surfaces/MonitorWidget.tsx` OR a non-empty `MODULE.monitorWidgets[]` array per the Stage 4d multi-variant shape).
- For each `surfaces/<Name>.tsx` present, the corresponding capability tier is in `tiers`.
- Lint / round-trip test asserts all four invariants (FM11).

**Anti-patterns.**
- `tiers: ['generic_runnable', 'paused']` — mutually exclusive.
- `tiers: ['custom_build_surface']` (no runtime status).
- `tiers: ['generic_runnable', 'monitor_surface']` with no `surfaces/MonitorWidget.tsx`.
- `surfaces/AskCard.tsx` present but `tiers` does not include `ask_surface`.
- A new tier added to the union literal without a corresponding ADR amending the closed family.

**Exceptions.** None.

**Relates to.** FP3, FP8, [`tiers.md`](tiers.md) (full per-tier semantics), [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md).

---

## Group II — Admission: should this module be built at all?

FM1–FM3 said the candidate *is* a module. Group II asks whether it *should* exist in its proposed shape. Most of the work here is **not building too much**.

### FM4 — Tier-set parsimony

**Rule.** A new module claims the smallest tier set that satisfies the actual use case. The default for any newly-shipped primitive is:

```
tiers: ['generic_runnable']
```

A bespoke capability tier (`custom_build_surface`, `custom_preview_widget`, `monitor_surface`, `ask_surface`) is added only when at least one of these is defensibly true *and stated in the THESIS*:

1. **The generic builder + AutoRenderer is materially worse for this primitive's output.** Specific examples: the primitive emits an event series whose surprise/z-score breakdown the AutoRenderer cannot lay out usefully; the primitive emits a categorical regime label the AutoRenderer cannot render at all.
2. **The desk reads this primitive at a glance every day** (justifies `monitor_surface`).
3. **The Ask result card for this primitive needs domain-specific framing the generic `AssistantResearchCard` cannot provide** (justifies `ask_surface`).
4. **The persisted-artifact preview card needs per-tool layout** beyond the generic per-type widget (justifies `custom_preview_widget`).

A new module that claims four capability tiers from day one without a THESIS-stated justification is a candidate for review.

**Why.** Tier parsimony preserves the platform's scale claim. Every capability tier is per-module JSX that someone has to maintain. Each one is justified when the generic surface is inadequate; each unjustified one is dead code waiting to drift. The backend's PR4 (parsimony) has the same shape: don't build a new primitive if composition suffices. Don't build a custom surface if the shared shell suffices.

**Verify.**
- THESIS.md answers Question 3 (*Why these surfaces and not others?*) with an explicit comparison to the generic alternative.
- The PR description names which generic alternative was considered for each capability tier and why it was rejected.

**Anti-patterns.**
- A new module's `tiers` includes `custom_build_surface` because "the generic builder is ugly." Ugly is a UX iteration on the generic builder, not a per-module override.
- A new module's `tiers` claims all four capability tiers from day one without an answered THESIS Question 3.
- A capability tier added "preemptively" because "we might need it later." Build when the use case is real.

**Exceptions.** Rich-model primitives (PCA, rolling regression, attribution, half-life, beta-adjusted spread) have an established bespoke surface pattern (`BuilderCanvas` + `OutputCanvas` + interpretation cards). They claim `custom_build_surface` from day one; the THESIS cites the rich-model precedent.

**Relates to.** FP3, backend PR4, [`tiers.md`](tiers.md), [`thesis_template.md`](thesis_template.md) (Question 3).

### FM5 — Display-metadata sourcing

**Rule.** The module's display metadata (`displayName`, `category`, `oneLineSummary`, `defaultParams`, `paramHints`, `interpretationCards`) mirrors the backend's `ToolCard` (returned by `GET /api/v1/tools/{name}`) where possible. Specifically:

- `displayName` derives from the backend `ToolCard.name` formatted to Title Case.
- `category` matches the backend manifest `category` field.
- `oneLineSummary` paraphrases the backend `ToolCard.methodology.what_it_does` (the one-paragraph description), trimmed to one sentence.
- `defaultParams` use the same values as the backend `ToolCard.input_fields[*].default` for the central methodology surface (the LLM-exposed knobs).
- `paramHints` choose controls based on the backend field type + name pattern (`curve_family` → curve dropdown, `tenor` → tenor dropdown, etc.); the helper `inferFieldControl` in `modelRegistry.ts` is the source of truth for field-name → control mapping.
- `interpretationCards` are module-specific UX additions (NOT mirrored from the backend); the THESIS explains why each card is necessary.

Deliberate divergence from the backend metadata is allowed but MUST be THESIS-documented.

**Why.** Single source of truth (P10). The backend already declares what a primitive is, what it expects, what it returns. The frontend should not re-declare it in subtly different words. Drift between the two — even cosmetic — degrades the user's trust ("which is right?"). When divergence is genuinely necessary (e.g. the backend's one-paragraph description is too dense for a UI card), the THESIS records the choice so future readers know it was intentional.

**Verify.**
- `displayName` is the title-case of the backend `ToolCard.name` (with manual adjustments only when documented).
- `defaultParams` keys match the backend's `input_fields` names.
- `defaultParams` values are runnable (the form, on first load with no URL overrides, produces a real backend response when submitted).
- THESIS.md explains every deliberate divergence in Question 2 or 3.

**Anti-patterns.**
- `displayName: 'PCA'` for `calculate_pca_yield_curve_tool` (drops the curve context the backend's name carries).
- `defaultParams` that don't actually run (`curve_family: 'BAD_FAMILY'` → 400 on submit).
- `interpretationCards` that contradict the backend's `methodology.what_it_does` (a P10 violation; one of the two is wrong).

**Exceptions.** Rich-model interpretation cards (PCA's "PC1 = Level" etc.) are intentionally rich and don't map 1:1 to a backend field. The THESIS documents these as design choices.

**Relates to.** P10, P5, the backend's PR10 (provenance reachability).

### FM6 — Unsupported-reason surfacing

**Rule.** A module whose `tiers` includes `workflow_incompatible`, `paused`, or `deferred` MUST provide an `unsupportedReason: UnsupportedKnownReason` value on its spec:

```ts
type UnsupportedKnownReason = {
  label: string;          // short user-facing label
  reason: string;         // one-sentence "why this isn't running through the normal path"
  whatWorksNow: string;   // where the user CAN reach this tool today (Ask, Library, typed endpoint)
};
```

For `workflow_incompatible` modules, the `reason` MUST quote (or paraphrase) the backend's `WORKFLOW_INCOMPATIBLE_TOOLS[toolName]` rationale verbatim. The mirror is mechanical — the backend declares why; the frontend repeats.

For `paused` modules, the `reason` describes what's missing (no `_PRIMITIVE_SPECS` entry, no backend implementation) and the `whatWorksNow` points the user at any available alternative (a sibling primitive, the Ask path, etc.).

For `deferred` modules, the `reason` says the name is reserved; the `whatWorksNow` is "this primitive is not yet available."

**Why.** Honest disclosure (FP7, P5). A primitive's runtime status is part of its contract with the user; presenting a paused primitive as if it works (or hiding its existence entirely) is a trust failure. The `UnsupportedKnownReason` is the structural slot for honesty; it surfaces in the UnsupportedKnownCard at every consumer (Build, Ask handoff, Library detail drawer).

**Verify.**
- Every module with `tiers ∋ workflow_incompatible | paused | deferred` has a non-null `unsupportedReason`.
- The `reason` field is a complete sentence (not "TBD" / "TODO").
- For `workflow_incompatible`, the `reason` aligns with the backend's published reason (cross-side test asserts this).
- The user's experience opening such a module from Library or Ask is the unsupported card with the reason rendered verbatim.

**Anti-patterns.**
- A `paused` module with `unsupportedReason: null`.
- An `unsupportedReason.reason` of `"TODO: write reason"`.
- A `workflow_incompatible` module whose `reason` says "this is paused" (the wrong category).
- A `deferred` module without an `unsupportedReason` so the user gets a 404 rather than the reservation card.

**Exceptions.** `generic_runnable` modules have `unsupportedReason: null` and the field is omitted from the spec for clarity.

**Relates to.** FP7, P5, P6, [`tiers.md`](tiers.md).

---

## Group III — Standardness: what does "standard" mean operationally?

A frontend module is *standard* if and only if FM7–FM10 all hold. This is the operational definition.

### FM7 — Pure-spec assembly (no side effects)

**Rule.** `module.ts` exports a pure value (`export const MODULE: PrimitiveModuleSpec = { ... };`). It MUST NOT call mutating functions on global state at module-eval time. Specifically:
- No `registerToolRenderer(...)` call at top level.
- No `MODELS.push(...)` or analog.
- No `import './register'` that runs a side effect.

Per-tool registrations against runtime registries (the node-renderer registry, etc.) happen at one centralised place: the central loader at `src/modules/index.ts` reads `ALL_PRIMITIVE_MODULES` and performs the registrations as a single explicit phase.

**Why.** Determinism + testability. A module spec read in isolation (e.g. by a test, by a build tool, by `tools/check_module_parity.py`) returns the same value regardless of what else has been imported. Side-effect imports break this. The backend's MCP server pattern is the same shape: tool registration happens at one explicit point (`mcp_server.py`), not at each tool's import.

**Verify.**
- `grep -E "^(import|export)" src/modules/primitives/<name>/module.ts` shows only declarations + the `export const MODULE` line.
- A module's `module.ts` has no top-level function calls except `import` statements and the `export const MODULE` assignment.
- The per-module round-trip test (FM11) imports `MODULE` and asserts content without any setup hook.

**Anti-patterns.**
- A `register.ts` co-located in the module folder that mutates a global registry.
- Module's `module.ts` ending with `setupRenderer(MODULE);`.
- Module imports that have side effects via transitive paths.

**Exceptions.** None.

**Relates to.** FP5, FP4, [`../frontend_registries/README.md`](../frontend_registries/README.md).

### FM8 — Surface-file contract

**Rule.** Each claimed capability tier in `MODULE.tiers` corresponds to exactly one file in `surfaces/` with a fixed name and a fixed default-exported component shape:

| Tier | File | Default export type |
|---|---|---|
| `custom_build_surface` | `surfaces/BuildSurface.tsx` | `React.FC<BuildSurfaceProps>` |
| `custom_preview_widget` | `surfaces/PreviewWidget.tsx` | `NodeRenderer` (per `nodeRendererRegistry.ts`) |
| `monitor_surface` | `surfaces/MonitorWidget.tsx` (single-widget legacy shape) OR one or more `surfaces/monitor/<WidgetName>.tsx` files referenced from `MODULE.monitorWidgets[]` (Stage 4d multi-variant shape) | `React.FC<MonitorWidgetProps>` per the legacy shape; `ComponentType<any>` per the Stage 4d shape (typed loosely while the catalog walker validates the inline `MonitorWidgetMeta` metadata) |
| `ask_surface` | `surfaces/AskCard.tsx` | `React.FC<AskCardProps>` |

For workflow modules:

| Tier | File | Default export type |
|---|---|---|
| `custom_build_surface` | `surfaces/ResultsDashboard.tsx` | `React.FC<WorkflowDashboardProps>` |

The Prop interfaces (`BuildSurfaceProps`, etc.) are declared in `src/modules/types.ts` and are the SAME shape for every module claiming that tier. A surface that needs additional data MUST receive it through the standard props (which carry the workspace context, the tool ToolCard, run results, etc.) — not via custom props.

The `MODULE.surfaces` field references each surface by key:

```ts
MODULE.surfaces = {
  build:   BuildSurface,    // if custom_build_surface
  preview: PreviewWidget,   // if custom_preview_widget
  monitor: MonitorWidget,   // if monitor_surface AND single-widget legacy shape
  ask:     AskCard,         // if ask_surface
};
```

Each key is present if and only if the corresponding tier is claimed. For `monitor_surface`, the Stage 4d multi-variant shape uses `MODULE.monitorWidgets: ReadonlyArray<MonitorWidgetMeta>` instead — each entry declares its own `id`, `label`, `description`, `category`, `defaultSize`, `allowedSizes`, `parameterized`, optional `paramFields`, and a `component` reference. The central `src/components/monitor/registry.ts` walker derives `WIDGET_TYPES` from `ALL_PRIMITIVE_MODULES.flatMap(m => m.monitorWidgets ?? [])`, so a module that ships multiple variants from the same backend tool (e.g. `calculate_curve_spread_tool` → `curve_spreads` + `spread_chart`) gets multiple catalog entries from a single module declaration.

**Why.** Predictability. Every page shell can ask `MODULE.surfaces.monitor` and either get a component or `undefined` — no per-module branching. The fixed prop shapes mean shells pass the same data to every module's surface; modules can't request bespoke props that break the shell.

**Verify.**
- File names match the contract exactly (no `BuildView.tsx`, `BuildCanvas.tsx`, `MainSurface.tsx`).
- Default exports are React functional components with the typed prop interfaces.
- `MODULE.surfaces.<key>` is a static reference (not a function returning a component); lazy loading is opt-in via the framework, not via per-module wrapping.
- The per-module round-trip test asserts file presence + import resolvability.

**Anti-patterns.**
- `surfaces/MyBuildView.tsx` instead of `surfaces/BuildSurface.tsx`.
- A surface component that takes custom props beyond the standard interface.
- A surface declared in `MODULE.surfaces` but no matching `surfaces/<Name>.tsx` file.
- A surface file present but not declared in `MODULE.surfaces`.

**Exceptions.** A surface MAY be wrapped in `React.lazy(() => import(...))` and assigned to `MODULE.surfaces.<key>` for lazy loading. Lazy loading is opt-in per surface; the wrapping is at the assignment site, not inside the surface file itself.

**Relates to.** FM3, FM7, FP3.

### FM9 — Routing-claim disclosure

**Rule.** A module that routes through one of the existing typed primitive views (Spread, CrossMarket, Butterfly, Yield, Scanner, Regime, Forward) declares it explicitly:

```ts
MODULE.typedView = 'spread';   // | 'cross_market' | 'butterfly' | 'yield' | 'regime' | 'scanner' | 'forward'
```

A module that uses the rich-model builder declares it:

```ts
MODULE.richModel = true;
```

The two are mutually exclusive: a module either has a typed view, or a rich model, or a custom build surface, or routes through the generic builder. The contextDecoder's `TOOL_TO_VIEW` and `hasModelMetadata` lookups are *derived* from these fields across all modules.

A module that claims `typedView: 'spread'` MUST be a spread-shaped primitive (curve-spread or cross-market spread that the typed `SpreadPrimitiveView` can render against the backend's `/detail/spread` endpoint, etc.). The shared-infrastructure typed views are finance-blind (FP13); they expect specific output shapes. Mismatched claims surface as silent rendering bugs.

**Why.** Single declaration site. The current `TOOL_TO_VIEW` and `MODELS` registries in `lib/` were hand-authored across multiple file edits; the module-level declaration is one edit per module. The `hasModelMetadata` check resolves to a derived set; the `TOOL_TO_VIEW` lookup resolves to a derived map.

**Verify.**
- A module's `typedView` is one of the seven values (or `null`).
- A module's `richModel` is `true` or `false`.
- Not both: `(typedView !== null) && richModel === false` OR `richModel === true && typedView === null` OR both null.
- A module claiming `typedView: 'spread'` produces a working `/detail/spread`-backed canvas (manually verified in PR).
- A module claiming `richModel: true` ships interpretation cards + default params suitable for the rich-model builder.

**Anti-patterns.**
- A module with both `typedView: 'butterfly'` and `richModel: true`.
- A module claiming `typedView: 'scanner'` for a primitive whose output is not scanner-shaped.
- A module that uses the rich-model builder but doesn't set `richModel: true`.
- Hand-editing `TOOL_TO_VIEW` directly in `contextDecoder.ts` after Stage N of the migration.

**Exceptions.** During the migration's intermediate stages, `TOOL_TO_VIEW` may continue to be hand-authored alongside module declarations. By Stage N every routing decision derives from module specs.

**Relates to.** FM3, FP4, the contextDecoder in [`../../../UI/macro-copilot-dashboard-polished/src/components/build/primitive/contextDecoder.ts`](../../../UI/macro-copilot-dashboard-polished/src/components/build/primitive/contextDecoder.ts).

### FM10 — THESIS discipline

**Rule.** Every module ships `THESIS.md` in its root, answering the five required questions from [`thesis_template.md`](thesis_template.md) in the exact order specified. The THESIS is a designed artefact, not a generic README:

1. **What surfaces does this module ship?** Enumerate every tier claimed, with one sentence per surface.
2. **What does the user read off each surface?** One paragraph per surface naming the specific decisions a PM makes from it.
3. **Why these surfaces and not others?** Explain the alternatives considered and rejected (could this be a generic surface? Why a bespoke widget?).
4. **What would change the design?** List the specific shifts in user need or backend output that would force a new tier claim or surface rewrite.
5. **Which backend doctrine does this module operationalise?** Cite the relevant P-numbers, PR-numbers, ADRs, and TD entries.

THESIS staleness vs the spec is a build error: if `MODULE.tiers` includes a tier that THESIS doesn't enumerate, the round-trip test (FM11) fails.

**Why.** Designed artefacts. The backend has methodology decisions captured in `config.yaml` + `methodology.what_it_does`; the frontend's analog is THESIS. It forces UX decisions into a citable document, creates an audit trail for choices that today live only in PR descriptions, and gives future engineers a basis for reasoning about *why* a module looks the way it does.

**Verify.**
- File exists at `src/modules/primitives/<name>/THESIS.md` (or workflow analog).
- The five questions appear in order with non-empty answers.
- Question 1's tier enumeration matches `MODULE.tiers` exactly.
- Question 5 cites at least one P-number, PR-number, or ADR.
- The per-module round-trip test asserts file presence + Question-1 / tier consistency.

**Anti-patterns.**
- THESIS.md is a generic "this is a module" readme with no question structure.
- THESIS lists surfaces that the module doesn't claim, or vice versa.
- Question 4 answer is "N/A" or empty (every design has things that would change it; surfacing them is the point).
- Question 5 is empty (every module operationalises some backend doctrine; if you cannot cite any, the module's purpose is unclear).

**Exceptions.** `deferred` modules have a minimal THESIS: one paragraph describing what the reservation is for + a stubbed five-question template marking Questions 1–4 as "N/A — deferred reservation" and Question 5 citing the reservation rationale.

**Relates to.** FP10, [`thesis_template.md`](thesis_template.md).

---

## Group IV — Operational: the build conventions every standard module follows

FM7–FM10 define what *standard* means. FM11–FM12 are the operational conventions that keep the catalogue coherent — testable in CI, the floor every module sits on.

### FM11 — Round-trip test

**Rule.** Every module ships `__tests__/module.spec.ts` that asserts the following invariants:

1. **Identity:** `MODULE.toolName === <folder name>`.
2. **Closed tier family:** `MODULE.tiers` is a subset of the eight closed-family values.
3. **Mutually-exclusive runtime status:** exactly one of `{generic_runnable, workflow_incompatible, paused, deferred}` is present.
4. **Capability-tier file presence:** for each capability tier, the corresponding `surfaces/<Name>.tsx` is importable.
5. **Capability-tier spec presence:** for each `surfaces/<Name>.tsx`, the corresponding tier and `MODULE.surfaces.<key>` are present.
6. **THESIS presence:** `THESIS.md` exists.
7. **THESIS consistency:** the surfaces enumerated in THESIS Question 1 match `MODULE.tiers`.
8. **UnsupportedReason presence:** if `tiers ∋ workflow_incompatible | paused | deferred`, `MODULE.unsupportedReason` is non-null and has all three fields non-empty.
9. **Loader inclusion:** `src/modules/index.ts` imports this module's `MODULE` and includes it in `ALL_PRIMITIVE_MODULES` (or `ALL_WORKFLOW_MODULES`).

The standard test boilerplate is documented in [`../../03_standards/frontend_test_patterns.md`](../../03_standards/frontend_test_patterns.md). A helper `assertStandardModuleInvariants(MODULE, folder)` provides one-line coverage of invariants 1–8; invariant 9 is asserted by a single loader-presence test that runs once across all modules.

**Why.** Mechanical enforcement. Without the round-trip, a developer can ship a module whose spec, files, and THESIS disagree, and the failure surfaces at production runtime ("the Monitor widget is in the registry but the file doesn't exist"). The round-trip catches it at CI time.

**Verify.**
- `npm run test:modules` exits clean.
- Every module folder has `__tests__/module.spec.ts`.
- A module without the test fails the broader parity check (the `check_module_parity` script asserts every folder has the test).

**Anti-patterns.**
- A module without the round-trip test.
- A test that asserts only a subset of the invariants.
- A test that imports `MODULE` but skips the import-resolvability assertion on surface files.
- Disabling the test temporarily to ship a half-wired module.

**Exceptions.** None.

**Relates to.** FP11, FM1, FM3, FM6, FM8, FM10, FM12, [`../../03_standards/frontend_test_patterns.md`](../../03_standards/frontend_test_patterns.md).

### FM12 — Loader-presence

**Rule.** Every module is imported in `src/modules/index.ts` and contributes exactly one entry to either `ALL_PRIMITIVE_MODULES` (for primitive modules) or `ALL_WORKFLOW_MODULES` (for workflow modules). The loader is a flat hand-maintained barrel:

```ts
// src/modules/index.ts
import { MODULE as calculate_cpi_surprise_tool } from './primitives/calculate_cpi_surprise_tool';
import { MODULE as calculate_nfp_surprise_tool } from './primitives/calculate_nfp_surprise_tool';
// ...one import per primitive module, alphabetised by toolName...

import { MODULE as event_study } from './workflows/event_study';
// ...one import per workflow module, alphabetised by template_id...

export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  calculate_cpi_surprise_tool,
  calculate_nfp_surprise_tool,
  // ...
];

export const ALL_WORKFLOW_MODULES: ReadonlyArray<WorkflowModuleSpec> = [
  event_study,
  // ...
];

export function getPrimitiveModule(toolName: string): PrimitiveModuleSpec | undefined {
  return ALL_PRIMITIVE_MODULES.find((m) => m.toolName === toolName);
}

export function getWorkflowModule(templateId: string): WorkflowModuleSpec | undefined {
  return ALL_WORKFLOW_MODULES.find((m) => m.templateId === templateId);
}
```

The import order is alphabetical by tool name (or template id). New modules MUST be inserted in alphabetical position, not appended at the bottom. (Diff-friendliness; reviewer can grep linearly.)

**Why.** Single source of truth. The loader is the ONE place that knows the full list of modules. Tests, registries, and parity checks read from it. A module not listed here is invisible to the runtime; a module listed twice produces a duplicate-key error at registry derivation time.

**Verify.**
- A new module's PR includes both the new folder AND the new import line in `src/modules/index.ts`.
- `tools/check_module_parity.py` asserts the loader's tool-name list equals `_PRIMITIVE_SPECS.keys() ∪ WORKFLOW_INCOMPATIBLE_TOOLS.keys() ∪ deferred-reservations`.
- The loader file is alphabetically sorted by tool name (linter / formatter enforces this).
- A duplicate import name produces a compile error.

**Anti-patterns.**
- A module folder exists but no import in the loader.
- A loader import for a folder that doesn't exist (will fail at build time, but is the same class of error).
- Non-alphabetical insertion ("just put it at the end").
- A loader split across multiple files (the loader is intentionally one barrel — splitting hides modules behind sub-loaders).

**Exceptions.** None.

**Relates to.** FP4, FP5, FM7, FM11.

---

## How a module exists in the broader UI

A standard module participates in the platform's UI through these mechanical paths, all derived from its spec:

| Surface | Path |
|---|---|
| Library catalogue | Backend manifest → LibraryPage. (No frontend module action required — the module appears in the Library because its tool exists in the manifest. The module's role is enabling the "Open in Build" action via the central registries.) |
| Library detail drawer | When the user clicks the drawer's "Open in Build" CTA, contextDecoder resolves via the central registries (derived from module specs) → routes to the appropriate Build canvas. |
| Build empty state | The Library "Open in Build" path lands here when no slug is bound. Modules with `tiers ∋ generic_runnable` render via `GenericPrimitiveBuilder`; `custom_build_surface` modules render their `surfaces/BuildSurface`; `workflow_incompatible`/`paused`/`deferred` render the unsupported card with the module's `unsupportedReason`. |
| Build completed (slug-bound) | The workspace replay produces a DAG; per-node rendering uses the node-renderer registry, where per-tool entries come from `custom_preview_widget` tier claims. |
| Monitor bento grid | Modules with `tiers ∋ monitor_surface` contribute to the WIDGET_TYPES catalogue (derived). The user can add the widget from the catalog modal. |
| Ask result card | Modules with `tiers ∋ ask_surface` contribute a per-tool result card the chat dispatcher renders for results from this tool. Modules without this tier render through the generic `AssistantResearchCard`. |
| DAG node | Always rendered by the shared DAG renderer using the module's display metadata (`displayName`) and the workflow's lineage. Modules do NOT ship DAG-node JSX (FP13 — the DAG renderer is finance-blind). |

Every path is mechanical: the module's spec determines participation; the page shell does the routing. No per-page code edits.

## Common patterns

The 50-primitive backend produces a handful of recurring module shapes. They are not enumerated as archetypes (FM4 says default to the smallest tier set); they are observed patterns:

| Pattern | Tier set | Common surfaces |
|---|---|---|
| **Snapshot primitive** (`get_yield_levels_tool`, `calculate_curve_spread_tool`) | `generic_runnable` + maybe `monitor_surface` | Generic builder; optional Monitor widget |
| **Scanner primitive** (`scan_extremes_tool`, `scan_bond_futures_extremes_tool`) | `generic_runnable` + `monitor_surface` | Scanner-typed Build view + Monitor widget |
| **Rich-model primitive** (`calculate_pca_yield_curve_tool`, `calculate_rolling_regression_tool`) | `generic_runnable` + `custom_build_surface` + `custom_preview_widget` | BuilderCanvas + per-tool preview |
| **Event-signal primitive** (`calculate_cpi_surprise_tool`, `calculate_nfp_surprise_tool`) | `generic_runnable` + `monitor_surface` + `ask_surface` | Generic builder + Monitor widget + Ask card |
| **Workflow-incompatible primitive** (`get_otr_history_tool`, `calculate_wirp_meeting_pricing_tool`) | `workflow_incompatible` + maybe `custom_build_surface` | Unsupported card + optional typed canvas via typed-detail endpoint |
| **Paused primitive** (`scan_ois_extremes_tool` today) | `paused` | Unsupported card with `whatWorksNow` pointer |
| **Deferred reservation** (`attribution_decomposition`, `cross_sectional_screen`) | `deferred` | Reservation card |

These patterns are documented as references, not contracts. New modules pick the smallest pattern that fits; bespoke combinations are fine when the THESIS justifies them.

## Open questions

1. **Lazy-loading policy for heavy surfaces.** The current contract allows opt-in `React.lazy` wrapping at the assignment site. Should we mandate it for surfaces above a certain JSX size? Today: opt-in only. Revisit when first-paint metrics show specific surfaces costing more than the rest.

2. **Per-workflow archetype-specific tiers.** Workflows currently share the primitive tier set. As workflow surfaces multiply, do they need a dedicated tier vocabulary (`active`, `paused`, `deferred`, plus workflow-specific capabilities)? Today: reuse primitive tiers with `templateId` instead of `toolName`. Revisit when N ≥ 5 workflow modules exist.

3. **Module aliases for backend renames.** If a backend renames a `tool_name`, the frontend module folder moves and every import updates. Should the module spec carry an `aliases: string[]` field so old URLs keep working? Today: no. Backend renames are rare and explicit; aliases at the module level would be a workaround for a backend churn problem.

4. **Per-module storybook stories.** Should every module ship a `stories.tsx` for visual regression? Today: no. Storybook is opt-in. Revisit if visual regression becomes a recurring failure mode.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1 | 2026-05-25 | Initial frontend module contract. 12 numbered principles (FM1–FM12) covering module identity, admission, standardness, and operational conventions. | [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md) |
