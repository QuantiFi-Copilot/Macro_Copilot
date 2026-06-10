# Standard Module Rules — Frontend Factory

This file is the direct build/review contract for standard frontend modules in the Macro_Copilot platform. It is the frontend equivalent of the primitive factory's `STANDARD_TOOL_AND_YAML_RULES.md`.

A "standard module" is one that fully satisfies BUILD_GUIDE.md Stages 4 → 5 → 6 + FM1–FM12 + the rendering_density.md dual-view mandate + the methodology_exposure.md §5 standalone bridge. The factory only ships standard modules.

**Version:** v1
**Status:** load-bearing operational contract.

---

## 1. What a standard frontend module IS

Every standard module is a folder under `UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<tool_slug>_tool/` containing the **8-artifact slot list** from BUILD_GUIDE.md:

```
src/modules/primitives/<verb>_<tool_slug>_tool/
├── module.ts                            # FM7 pure-spec assembly
├── THESIS.md                            # FM10 — five-question template
├── surfaces/
│   ├── BuildExtended.tsx                # rendering_density.md §1 (REQUIRED)
│   ├── BuildCompact.tsx                 # rendering_density.md §1 (REQUIRED)
│   ├── <tool_slug>Shared.ts             # cross-surface helper
│   └── monitor/
│       └── <Widget>.tsx                 # IF monitor_surface claimed
├── mockups/
│   ├── Compact.png                      # human-authored input
│   └── Extended.png                     # human-authored input
└── __tests__/
    └── module.spec.ts                   # FM11 round-trip + dual-view contract
```

Plus the cross-references in shared frontend files (NOT in the module folder; the builder edits them):

- `src/modules/index.ts` — FM12 loader entry (alphabetical)
- `src/types/rates.ts` — TS type mirror of the backend Pydantic Output
- `src/services/ratesApi.ts` — `fetchDetail<X>` service helper + `<X>DetailParams` type

Plus the backend bridge endpoint (the builder ships if not already present):

- `api/routes/rates/detail.py` — route at `/api/v1/rates/detail/<tool_kind>` returning `<Tool>Output.model_dump()`

This shape is INVARIANT across every standard module the factory ships. Different modules differ in what fills the files, NOT in the file structure itself.

## 2. The `MODULE` pure-spec contract  *(FM7, FM8)*

`module.ts` exports a `PrimitiveModuleSpec` value. The reference shape (from the Phase-1 pilot `calculate_breakeven_inflation_simple_tool/module.ts`):

```ts
import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS, /* etc */ } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { <Widget> } from './surfaces/monitor/<Widget>';
import { <SHARED_OPTIONS> } from './surfaces/<tool_slug>Shared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity
  toolName: '<verb>_<tool_slug>_tool',

  // FM3 — tier set (REQUIRED for this factory)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata (mirror manifest one_liner)
  displayName: '<Title Case>',
  category: '<category>',
  oneLineSummary: '<PM-facing one-liner — mirrors the backend manifest one_liner>',

  // FM9 — standalone bridge pattern (REQUIRED for this factory)
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (REQUIRED per rendering_density.md §1)
  // ``build`` is kept === buildExtended for the legacy VirtualPrimitiveCanvas
  // dispatcher (transitional alias; see rendering_density.md §5.2 + the pilot
  // tools' inline comment).
  surfaces: {
    build:         BuildExtended,
    buildExtended: BuildExtended,
    buildCompact:  BuildCompact,
  },

  // FM5c — Monitor catalog widget (IF monitor_surface claimed)
  monitorWidgets: [
    {
      id:           '<widget_id>',
      label:        '<short label>',
      description:  '<one-line widget description>',
      category:     'data',
      defaultSize:  'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [ /* per backend Input fields */ ],
      component: <Widget>,
    },
  ],

  // FM5 — defaultParams mirror Stage 1A exposure decisions on the backend
  defaultParams: { /* sensible defaults */ },
};
```

**Invariants:**
- `toolName` MUST equal the folder name (FM1).
- `tiers` MUST include `'custom_build_surface'` and `'generic_runnable'`; should include `'monitor_surface'` for desk-canonical morning reads.
- `typedView: null` (FM9 standalone pattern).
- `richModel: false`.
- `surfaces.buildExtended` AND `surfaces.buildCompact` BOTH populated (rendering_density.md §1).
- `surfaces.build === BuildExtended` (transitional alias; the comment must explicitly document this).
- `module.ts` is a pure value export — no side-effect imports (FM7).

## 3. BuildExtended.tsx contract  *(rendering_density.md §2.1)*

The full canvas. Single-tool dispatch mounts this directly.

Required content (per `rendering_density.md §2.1`):

- **Controls strip** — every Pydantic Input field that is `expose: true` per the backend's `methodology_exposure.md §3` block. For most tools: instrument selector, tenor, lookback, field_name, optional advanced z-score overrides.
- **Output canvas** — main chart (with z-score band overlays for level/spread tools), KPI strip with the headline numbers + period changes + range stats, tabular detail as appropriate.
- **Methodology card** — sources from `current_metrics.methodology_label` (NOT hardcoded). Shows the methodology summary + conventions used + theoretical reference summary.
- **Provenance footer** — tool name + as-of-date + lineage info if available.

Size envelope: full BuildShell content area (~1024×720+).

Receives `BuildExtendedProps`: `{ toolName, params, decoded, askHandoff? }`.

Calls `fetchDetail<X>` from the shared helper `surfaces/<tool_slug>Shared.ts` (NOT directly).

## 4. BuildCompact.tsx contract  *(rendering_density.md §2.2)*

The grid card. Multi-tool dispatch mounts this inside a DAG visualization.

Required content (per `rendering_density.md §2.2`):

- **Tool identity chip** — kicker showing `<displayName>` + instrument selector summary (e.g. *"Breakeven Inflation · US 10Y"*)
- **Headline 3 KPIs** — the desk-canonical "first three numbers" a PM reads. For a level: current value + period change + z-score. For a spread: current spread + period change + percentile. For a regime classifier: current state + confidence + driver.
- **Sparkline** — small chart of the most-relevant time series
- **Methodology disclosure (compact form)** — one-line caveat OR `(i)` icon that surfaces the full card on hover
- **Expand affordance** — explicit button calling the `onExpand` prop
- **Tone cues** — sign-convention colouring (positive change in green/red per desk convention; |z| ≥ 1.5 amber; |z| ≥ 2.0 coral/mint)

Size envelope: ~400×280px at `size='small'`, up to ~600×420px at `size='medium'`.

Receives `BuildCompactProps`: `{ toolName, params, size, onExpand, callMeta? }`.

**What the compact view is NOT** (anti-patterns per `rendering_density.md §2.2`):

- It is NOT a shrink-to-fit of the extended view (curated headline metrics, NOT all KPIs)
- It does NOT carry the controls strip (editing happens via expand path)
- It does NOT mount its own modal (calls `onExpand`; shared infrastructure mounts the modal)
- It does NOT hide methodology entirely (must be reachable via tooltip / icon / inline caveat)
- It does NOT fetch a SMALLER endpoint than the extended view (both consume the SAME typed-detail endpoint; compact just renders less)

## 5. `<tool_slug>Shared.ts` contract  *(BUILD_GUIDE.md §Stage 6 6B)*

Co-locate every cross-surface concern:

- **Data hook** — `use<X>Data(params)` calls `fetchDetail<X>` from `ratesApi.ts`, returns shape-normalised data
- **Option enumerations** — `<X>_PAIR_OPTIONS`, `<X>_TENOR_OPTIONS`, etc.
- **KPI / descriptor builders** — `buildHeadlineKPIs(data)`, `buildToneCueForZScore(z)`, etc.
- **Caveat strings** — the inflation-compensation caveat, the index-family caveat, etc.
- **Anything that would be duplicated** across BuildExtended / BuildCompact / Monitor

The extended view, the compact view, AND the monitor widget all import from this shared file. Duplication is a P10 violation that the reviewer flags as `CHANGES REQUIRED`.

### 5.1 Shared compact-side primitives the agent MUST use (do NOT reinvent)

The repo ships a set of cross-tool primitives at `UI/macro-copilot-dashboard-polished/src/components/shared/build/`. The builder MUST import + compose these — NOT reinvent equivalents in the per-tool shared file:

- `components/shared/build/compact/BuildCompactShell` — the outer card container for `BuildCompact.tsx`. Use this; do NOT roll a custom card frame.
- `components/shared/build/compact/MiniChart` — the canonical sparkline used in compact views. Pass `points` + optional band overlays.
- `components/shared/build/elements/CountryCaveatBadge` — country-specific caveat chip (consumes `countryCaveats.ts` registry).
- `components/shared/build/elements/FreshnessPill` — the as-of-date pill shown in compact + extended.
- `components/shared/build/elements/InfoTooltip` — the `(i)` icon that surfaces methodology disclosure in the compact view.
- `components/shared/build/elements/ZScoreRegimeSlider` — the tone-cue z-score regime slider (Normal / Elevated / Extreme bands).
- `components/build/primitive/PrimitiveCanvasShell` + `PrimitiveMetrics` — extended-view shell + KPI strip layout primitives.

Reading the reference Phase-1 pilot module (`calculate_breakeven_inflation_simple_tool/`) shows the canonical import pattern for these. If you find yourself writing a sparkline from scratch, a card border, or a z-score tone band — stop and look for the shared primitive first.

## 6. Monitor widget contract  *(IF monitor_surface claimed)*

Path: `surfaces/monitor/<Widget>.tsx`.

Monitor widgets are inherently compact (per `rendering_density.md §8`). They:

- Receive `MonitorWidgetProps`
- Call `fetchDetail<X>` (via the shared helper) with parameterised inputs from `MODULE.monitorWidgets[].paramFields`
- Render a desk-glanceable tile: current value + period change + percentile / z-score band

Registered in `MODULE.monitorWidgets[]` array inline in `module.ts`.

## 7. THESIS.md contract  *(FM10)*

Copy `docs_revamped/02_components/frontend_module/thesis_template.md` verbatim and answer all five questions:

1. **What surfaces does this module ship?** — one bullet per claimed tier. **Q1 MUST enumerate BOTH `buildExtended` and `buildCompact` explicitly** (rendering_density.md §11).
2. **What does the user read off each surface?** — one paragraph per surface naming the SPECIFIC decisions a PM makes. NOT generic ("the user sees the result"); concrete ("the PM reads the breakeven in bps, the 1-day change, the z-score regime").
3. **Why these surfaces and not others?** — explicit comparison to the generic alternative for each claimed surface.
4. **What would change the design?** — concrete shifts in user need or backend output.
5. **Which backend doctrine does this module operationalise?** — cite by ID (FM-numbers, P-numbers, rendering_density.md sections, methodology_exposure.md §5, BUILD_GUIDE.md stages).

A shallow THESIS is a `CHANGES REQUIRED` finding (reviewer flags lack of per-surface specificity in Q2 or missing ID citations in Q5).

## 8. `__tests__/module.spec.ts` contract  *(FM11)*

Mirror the Phase-1 pilot:

```ts
import { assertStandardModuleInvariants } from '../../../__test-utils';
import { MODULE } from '../module';

const FOLDER = '<verb>_<tool_slug>_tool';

check('module satisfies the standard invariants', async () => {
  await assertStandardModuleInvariants(MODULE, {
    folderName: FOLDER,
    moduleFolderPath: `${cwd()}/src/modules/primitives/${FOLDER}`,
  });
});

// rendering_density.md §11 — dual-view contract
check('claims custom_build_surface tier', () => { ... });
check('surfaces.buildExtended is populated', () => { ... });
check('surfaces.buildCompact is populated', () => { ... });
check('typedView is null (standalone-module pattern)', () => { ... });
check('mockups folder exists alongside the module', async () => {
  // assert mockups/Compact.png AND mockups/Extended.png exist
});
```

The shared helper `assertStandardModuleInvariants` covers FM11 invariants 1–8. The dual-view + mockups checks are inline per the pilot pattern.

## 9. Shared cross-references the builder MUST update

### `src/types/rates.ts`

Add the TS type mirror of the backend Pydantic Output. One TS type per Pydantic class (e.g. `<Tool>CurrentMetrics`, `<Tool>TimeSeriesRow`, `<Tool>Output`). Field names + types MUST match the Pydantic schema exactly — NO renaming to camelCase, NO type relaxation.

### `src/services/ratesApi.ts`

Add the service helper:

```ts
export type <X>DetailParams = { /* mirror Pydantic Input */ };

export async function fetchDetail<X>(params: <X>DetailParams): Promise<<Tool>Output> {
  const qs = new URLSearchParams({ /* serialise params */ });
  const res = await fetch(`/api/v1/rates/detail/<tool_kind>?${qs}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

### `src/modules/index.ts`

Add the loader entry in alphabetical position (FM12):

```ts
import { MODULE as <verb>_<tool_slug>_tool } from './primitives/<verb>_<tool_slug>_tool';
// ... insert in alphabetical position ...

export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  // ... insert in alphabetical position ...
  <verb>_<tool_slug>_tool,
];
```

## 10. Backend bridge endpoint  *(methodology_exposure.md §5)*

If the catalog entry's `pre_flight_backend_audit` indicates the typed-detail endpoint is NOT already present, the builder ADDS it to `api/routes/rates/detail.py`:

```python
@router.get("/detail/<tool_kind>", response_model=<Tool>Output)
def <tool_kind>_detail(
    <instrument_field>: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str | None = None,
    # ... mirror each Pydantic Input field, including expose:true overrides ...
    engine: Engine = Depends(get_engine),
) -> dict:
    """Standalone typed-detail endpoint for <tool_slug>."""
    params = <Tool>Input(...)
    config = load_tool_config(CONFIG_PATH)
    return calculate_<tool_slug>(engine=engine, params=params, config=config)
```

Route MUST mount at `/api/v1/rates/detail/<tool_kind>` (per-tool path, NOT `/api/v1/tools/{name}/run`).

## 11. Mockup-first workflow  *(BUILD_GUIDE.md §Stage 6 6D)*

The human commits `mockups/Compact.png` + `mockups/Extended.png` BEFORE the factory wakes. The pre-flight backend audit (`PRE_FLIGHT_BACKEND_AUDIT.md` Check 4) verifies both exist.

The builder reads them as IMAGES (Claude Opus vision) and renders TSX that visually matches.

The reviewer scores conformance against the same mockups.

**Mockup deviation requires documented justification in the BUILDER REPORT** — and even then, the reviewer is likely to flag the deviation as `STRUCTURAL:` and route to `human_required`.

## 12. Anti-patterns (auto-reject in review)

- A module claiming `custom_build_surface` that ships only `surfaces/BuildExtended.tsx` (no compact) OR only `surfaces/BuildCompact.tsx` (no extended)
- `MODULE.typedView != null` for a NEW module
- `methodology_label` hardcoded as a TSX string literal instead of read from `current_metrics.methodology_label`
- BuildCompact mounting its own modal instead of calling `onExpand`
- BuildCompact carrying a controls strip
- Duplicated logic between BuildExtended.tsx + BuildCompact.tsx + monitor/<Widget>.tsx that should live in `<tool_slug>Shared.ts`
- THESIS Q1 saying "the Build surface" (singular) instead of enumerating both views
- THESIS Q5 with no ID citations
- `src/modules/index.ts` loader entry inserted at the bottom instead of alphabetical position
- `src/types/rates.ts` types using camelCase instead of mirroring the Pydantic field names exactly
- The typed-detail endpoint route signature missing one of the Pydantic Input override fields
- The folder name not matching the backend MCP `tool.name` exactly
- A surface file using a custom prop shape instead of the canonical `BuildExtendedProps` / `BuildCompactProps` / `MonitorWidgetProps`

## 13. Links

- `docs_revamped/02_components/primitive/BUILD_GUIDE.md` — Stages 4 → 5 → 6 (the master spec)
- `docs_revamped/02_components/frontend_module/README.md` — FM1–FM12
- `docs_revamped/03_standards/rendering_density.md` — §1 dual-view mandate, §2 per-view contracts, §5 propagation
- `docs_revamped/03_standards/methodology_exposure.md` §5 — standalone bridge
- `docs_revamped/02_components/frontend_module/thesis_template.md` — THESIS template
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/` — the canonical reference module
- `UI/macro-copilot-dashboard-polished/src/modules/__test-utils.ts` — `assertStandardModuleInvariants`

---

## 14. Migration-mode addendum

This file describes the **new-build** / steady-state contract for a standard frontend module — the shape every tool ends up at after the factory ships it.

For the special case of **converting** an existing legacy typed-renderer tool (`surfaces/ResultRenderer.tsx` + `module.ts.typedView: '<string>'`) into a standard module, the catalog entry sets `build_mode: migration` and the builder + reviewer follow `MIGRATION_RULES.md` IN ADDITION to this file.

The end-state shape (per §1) is identical between `new_build` and `migration` tools. The DIFFERENCE is the pre-state of the folder and the procedure to get from pre-state to end-state. Specifically, migration mode:

- DELETES the legacy `surfaces/ResultRenderer.tsx` (rather than overwriting a Stage-3 stub)
- TRANSFORMS the existing `module.ts` (typedView → null, removes legacy fields, replaces surfaces keys) rather than overwriting a stub
- PRESERVES the existing monitor widget identities (the `id` and `paramFields` shape in `MODULE.monitorWidgets[]`) for backward compatibility — see `MIGRATION_RULES.md` §6
- REUSES the existing central type / service / route entries (does NOT add parallel entries)

When reading this file for a migration entry, treat §1 as the END-STATE specification; treat `MIGRATION_RULES.md` §4 (the migration procedure) as the path from legacy → end-state.

See also `PRE_FLIGHT_BACKEND_AUDIT.md` §11 — Check 5 (legacy pattern detection) — which the orchestrator runs as a migration-only pre-flight step.
