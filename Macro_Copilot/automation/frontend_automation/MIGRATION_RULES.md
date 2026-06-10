# Migration Rules — Frontend Factory

This file is the direct build/review contract for **migration-mode** dispatches — when the factory is converting a tool from the legacy `surfaces/ResultRenderer.tsx` typed-view pattern to the new dual-view + standalone-bridge pattern.

It is the migration-mode sibling of `STANDARD_MODULE_RULES.md`. Read both for migration entries; only `STANDARD_MODULE_RULES.md` for new-build entries.

**Version:** v1
**Status:** load-bearing operational contract.

---

## 1. Scope

Migration mode applies when the catalog entry has `build_mode: migration`.

The factory does NOT use migration mode for:
- Fresh-build tools (Stage-3 scaffold only — module.ts stub claims `tiers: ['generic_runnable']`, no surfaces). Those use `build_mode: new_build` (default).
- Rich-model tools (BuildSurface + PreviewWidget custom-canvas pattern). Defer to an individual design sprint.
- Tools shipping a custom output kind (e.g. `regime-state`) that does not fit the 3-KPI Compact contract. Defer to an individual design sprint.

The eligible migration targets at the time of this writing are the 6 sovereign-bonds typed-renderer tools listed in `surface_contract.md` §4.1 as `build_status: typed-renderer-built`. `classify_curve_move_tool` is excluded from migration mode because its categorical output (regime tag) does not fit the dual-view 3-KPI contract — handle it separately.

## 2. Why migration mode is different from new build

| Aspect | New build (`build_mode: new_build`) | Migration (`build_mode: migration`) |
|---|---|---|
| Pre-existing folder state | Stage-3 scaffold: stub `module.ts` (`tiers: ['generic_runnable']` only), v2 `THESIS.md` ("no bespoke surface today"), placeholder `__tests__/module.spec.ts` | Working module: `module.ts` with `typedView: '<string>'`, `surfaces: { resultRenderer: ResultRenderer }`, legacy `workspaceLabel`; populated `surfaces/ResultRenderer.tsx`; populated `surfaces/monitor/<Widget>.tsx` files |
| Files to **DELETE** | none | `surfaces/ResultRenderer.tsx` (per tool) |
| Files to **PRESERVE** | none | Existing `surfaces/monitor/<Widget>.tsx` files (their IDs are the backward-compat lock — see §6) |
| `module.ts.typedView` | already `null` | string → must change to `null` |
| `module.ts.surfaces` | `{ build, buildExtended, buildCompact }` | currently `{ resultRenderer: ResultRenderer }` → must change |
| Legacy `module.ts` fields | none | `workspaceLabel`, `unsupportedReason` (if present) → remove |
| `THESIS.md` | v2 stub → overwrite with 5-question | v2 stub → same overwrite |
| `__tests__/module.spec.ts` | placeholder → overwrite | legacy test shape → overwrite |
| Central `src/types/rates.ts` | needs NEW type | type ALREADY exists (e.g. `CurveSpreadOutput`) → verify field-by-field against Pydantic; do NOT duplicate |
| Central `src/services/ratesApi.ts` | needs NEW fetcher | fetcher ALREADY exists (e.g. `fetchDetailSpread`) → reuse; do NOT add a parallel `fetchDetail<X>` |
| Central `api/routes/rates/detail.py` | needs NEW route | route ALREADY exists (e.g. `/detail/spread`) → reuse; do NOT add a duplicate at a parallel slug |
| `src/lib/toolNames.ts` | may need alias | already present |
| Backward compat | not applicable | **CRITICAL** — existing dashboards that reference the monitor widgets MUST keep working |

## 3. Pre-migration sanity check (the orchestrator runs this before dispatch)

Beyond the standard 4-check pre-flight backend audit (see `PRE_FLIGHT_BACKEND_AUDIT.md` §2), migration mode requires:

### Check 5 — Legacy pattern detected

The tool's current `module.ts` must show the legacy typed-view pattern:

```bash
grep -q "typedView: '" UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/module.ts && \
grep -q "resultRenderer:" UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/module.ts && \
test -f UI/macro-copilot-dashboard-polished/src/modules/primitives/<verb>_<slug>_tool/surfaces/ResultRenderer.tsx
```

All three must return TRUE. If any returns FALSE, the catalog entry is misclassified (the tool is already migrated or never was on the legacy pattern) — mark `blocked` with reason `legacy_pattern_not_detected`.

This check is read-only — the orchestrator does NOT modify the legacy files before dispatch.

## 4. The migration procedure (what the builder does)

In strict order:

1. **Read the catalog entry's `legacy_migration` block** — it names every file to delete, every field to remove, and every monitor-widget ID to preserve.

2. **Read the mockups** at `mockups/Compact.png` + `mockups/Extended.png`. They are the visual contract.

3. **Read the reference frontend module** named in the catalog entry's `reference_frontend_module` field. For migration tools the reference is the parametric SIBLING that already ships dual-view (e.g. `calculate_ois_curve_spread_tool` is the migration reference for `calculate_curve_spread_tool`).

4. **Write the new surface files** (per `STANDARD_MODULE_RULES.md` §1):
   - `surfaces/BuildExtended.tsx` (NEW — full canvas; the reference module is your file-structure template)
   - `surfaces/BuildCompact.tsx` (NEW — 3-KPI compact card per `rendering_density.md` §2.2)
   - `surfaces/<tool_slug>Shared.ts` (NEW — cross-surface helper; data hook + KPI builders + tone helpers)

5. **PRESERVE the existing monitor widget files** at `surfaces/monitor/<Widget>.tsx`. Their **file paths** stay; their **import surface area** may shift (they should import shared helpers from the new `<tool_slug>Shared.ts` instead of duplicating logic).

   The builder MAY rewrite a monitor widget's internals to use the new shared helper, BUT the widget **identity** (the `id` in `MODULE.monitorWidgets[]`) MUST NOT change. Dashboards persist widget IDs.

6. **Transform `module.ts`** (per the catalog entry's `legacy_migration.module_ts_changes`):
   - Remove the import line for `./surfaces/ResultRenderer`
   - Remove the `surfaces.resultRenderer` entry
   - Add imports for the new files: `BuildExtended`, `BuildCompact`, the shared helper
   - Set `typedView: null` (was `'<kind>'`)
   - Remove `workspaceLabel` field if present
   - Remove `unsupportedReason` block if present
   - Add `richModel: false`
   - Replace `surfaces: { resultRenderer: ResultRenderer }` with `surfaces: { build: BuildExtended, buildExtended: BuildExtended, buildCompact: BuildCompact }`
   - **PRESERVE** the `monitorWidgets[]` array verbatim — each widget's `id`, `label`, `paramFields`, `component` import path stay identical
   - PRESERVE the `tiers` array IF the existing array is `[manifest_typed_view, ...]` (these tools are not in `_PRIMITIVE_SPECS` on the backend; do not change to `generic_runnable`)
   - The reference twin's `tiers` field is the target shape for tools that already had `generic_runnable`

7. **Overwrite `THESIS.md`** with the 5-question template per `STANDARD_MODULE_RULES.md` §7. The previous v2 stub ("no bespoke surface today") is discarded. Q1 enumerates BOTH `buildExtended` and `buildCompact` AND any preserved monitor widgets.

8. **Overwrite `__tests__/module.spec.ts`** with the dual-view contract test (`assertStandardModuleInvariants` + explicit `surfaces.buildExtended` / `surfaces.buildCompact` / `typedView === null` checks per `STANDARD_MODULE_RULES.md` §8).

9. **DELETE `surfaces/ResultRenderer.tsx`** (the legacy typed-view dispatch).

10. **Verify central files DID NOT GROW unnecessarily**:
    - `src/types/rates.ts` — the tool's `<X>Output` type ALREADY exists (e.g. `CurveSpreadOutput`). Do NOT add a parallel type. Open the type and verify each field matches the Pydantic Output exactly; if a Pydantic field has been added since the type was authored, ADD it to the existing TS type — do not duplicate the whole type.
    - `src/services/ratesApi.ts` — the tool's `fetchDetail<X>` ALREADY exists (e.g. `fetchDetailSpread`). Do NOT add a parallel fetcher. Verify the params type matches the Pydantic Input; if a field is missing, ADD it to the existing fetcher signature.
    - `api/routes/rates/detail.py` — the typed-detail endpoint ALREADY exists at the catalog entry's `endpoint_slug` (e.g. `/detail/spread`). Do NOT add a parallel route. Verify the route signature mirrors the Pydantic Input; if a field is missing, ADD it.
    - `src/modules/index.ts` — the loader entry ALREADY exists (the tool was working). Verify it's still alphabetical.
    - `src/lib/toolNames.ts` — the alias / `KNOWN_BACKEND_TOOLS` entry ALREADY exists. Do NOT add a duplicate.

11. **Run the pre-commit gate** (per `TESTING_POLICY.md`):
    ```bash
    cd UI/macro-copilot-dashboard-polished
    npm run test:modules
    npm run test:build
    npm run typecheck
    ```
    ALL THREE must exit 0. If anything fails, fix and re-run until green.

12. **Emit the BUILDER REPORT** (per `CLAUDE_BUILDER_PROMPT.md`'s standard format) with an EXTRA migration-mode section listing:
    - Files DELETED (specifically `surfaces/ResultRenderer.tsx`)
    - Files PRESERVED (the monitor widget paths + their unchanged IDs)
    - Central files VERIFIED unchanged-in-name (the existing type/service/route paths)
    - Any field added to a central file (e.g. a Pydantic field that was missing from the TS mirror)

## 5. Backward compatibility — the load-bearing rule

A migration is only successful if **every existing surface that used the legacy tool keeps working**.

Concretely:

- **Build pages** that landed users on the typed-view (e.g. the Ask→Build handoff for "what is the 2s10s spread") now route through the dual-view. They MUST still render correctly. The `module.ts.surfaces.build === BuildExtended` alias (per `STANDARD_MODULE_RULES.md` §2) carries the dispatch.

- **Monitor dashboards** that mount the tool's widgets (e.g. `CurveSpreadsWidget`, `SpreadChartWidget`) MUST still mount them correctly. The widget files stay at the same paths; their `id` in `MODULE.monitorWidgets[]` stays identical.

- **Ask cards** that linked to the tool stay routed via `KNOWN_BACKEND_TOOLS` (no change required — the tool name is preserved).

If the reviewer cannot verify backward compatibility (e.g. the widget IDs were renamed; the monitor widget file was deleted; the Pydantic Output shape changed and the TS type was not updated to match), the reviewer emits `STRUCTURAL: <description>` and the orchestrator routes to `human_required`.


### 5.1 The three-surface module (Compact != Monitor)

A common confusion: the `surfaces/BuildCompact.tsx` and the `surfaces/monitor/<X>Widget.tsx` are **TWO DIFFERENT FILES** that serve **TWO DIFFERENT SURFACES**.  Migration entries that have a `monitor_widgets_to_preserve` list MUST keep BOTH files distinct.

The framework (from `docs_revamped/02_components/surface_contract.md` §3.4 + `docs_revamped/03_standards/rendering_density.md` §8):

| | `surfaces/BuildCompact.tsx` | `surfaces/monitor/<X>Widget.tsx` |
|---|---|---|
| **Surface it serves** | Build (multi-tool DAG node) | Monitor (bento board card) |
| **Cardinality** | exactly ONE per tool (`MODULE.surfaces.buildCompact`) | 0..N per tool (`MODULE.monitorWidgets[]` array) |
| **Affordance** | expand-back-to-Extended via `onExpand` prop | none — it is the terminal monitor view |
| **Size envelope** | ~400 x 280 px at `size='small'`, ~600 x 420 px at `size='medium'` | Monitor bento sizes (small/medium/tall) |
| **Data fetch** | always per-widget via `fetchDetail<X>` | two modes: shared `RatesDataContext` for pre-aggregated grids OR per-widget `fetchDetail<X>` for parameterised |
| **Param editing** | NOT editable inline (params inherited from parent DAG node) | inline via `paramFields` schema |
| **Dashboard persistence** | none (DAG nodes are ephemeral) | dashboards persist `id` + `paramFields` values → widget `id` is the backward-compat lock |

A monitor-eligible tool ships **THREE distinct surface files**:

1. `surfaces/BuildExtended.tsx` — Build, full canvas
2. `surfaces/BuildCompact.tsx` — Build, DAG node (different from Monitor widget!)
3. `surfaces/monitor/<X>Widget.tsx` — Monitor bento card (one or more)

Migration entries with `monitor_widgets_to_preserve` MUST NOT collapse Compact + Monitor into the same file or share the same `id`.  The Compact view's component is exported as `BuildCompact`; the Monitor widget's component is exported as `<X>Widget` and named in `MODULE.monitorWidgets[].component`.

### 5.2 Monitor eligibility (when does a tool deserve a monitor widget?)

Per `surface_contract.md` §3.4 (load-bearing rule):

> A tool gets a Monitor widget **only** if a desk user would park it on their board and read it through the trading day.  Single-fire event reads (CPI / NFP / PPI releases), one-off scenario calculations, and rich-model fits do NOT belong on Monitor.  They are Ask / Build affairs.

For migration entries: PRESERVE the existing `monitor_surface` claim if the legacy tool already had it.  Migration does NOT re-evaluate the eligibility decision — the decision was made when the original tool was built.  For new-build entries (`build_mode: new_build`), the catalog author makes the call; the factory follows.

## 6. The monitor widget identity rule

For every entry in the catalog's `legacy_migration.monitor_widgets_to_preserve`:

- The widget file at `path:` MUST still exist on disk after the migration
- The widget's `id:` MUST still appear in `MODULE.monitorWidgets[]` array exactly as named
- The widget's `paramFields:` MUST keep the same field `name:`s and default values (so persisted dashboards don't break on hydration)
- The widget's `component:` import path MUST resolve to the same file
- The widget's runtime behaviour (data source, rendered KPIs) SHOULD match the prior behaviour — if a refactor is needed to import from the new `<tool_slug>Shared.ts`, the visible output stays equivalent

The reviewer's section H Check H4 (see `CLAUDE_REVIEWER_PROMPT.md`) verifies this rule. A widget identity drift is a `STRUCTURAL:` finding.

## 7. `legacy_migration` catalog block — the schema

A migration catalog entry MUST carry this nested block under the top-level entry:

```yaml
- id: <tool_id>
  status: todo
  build_mode: migration                          # REQUIRED
  build_order: <N>
  backend_tool_name: <name>
  # ... standard fields ...
  legacy_migration:
    current_typed_view: '<typedView string>'     # e.g. 'spread', 'yield', 'cross_market'
    files_to_delete:
      - UI/.../<tool>/surfaces/ResultRenderer.tsx
    module_ts_changes:
      remove_fields: [typedView, workspaceLabel, unsupportedReason]
      remove_surface_keys: [resultRenderer]
      set_typed_view: null
      set_rich_model: false
      add_surface_keys: [build, buildExtended, buildCompact]
    monitor_widgets_to_preserve:
      - id: '<widget_id_1>'
        path: UI/.../<tool>/surfaces/monitor/<Widget1>.tsx
        invariant: "Widget id '<widget_id_1>' MUST remain registered in MODULE.monitorWidgets[]; paramFields unchanged"
      - id: '<widget_id_2>'
        path: UI/.../<tool>/surfaces/monitor/<Widget2>.tsx
        invariant: "Widget id '<widget_id_2>' MUST remain registered; paramFields unchanged"
    central_files_already_have:
      - "src/types/rates.ts:<Tool>Output  (verify field-by-field match against backend Pydantic Output)"
      - "src/services/ratesApi.ts:fetchDetail<X>  (reuse; verify params type matches Pydantic Input)"
      - "api/routes/rates/detail.py:<endpoint_slug>  (reuse; verify signature mirrors Pydantic Input)"
      - "src/modules/index.ts:<tool>_tool entry  (verify still alphabetical)"
      - "src/lib/toolNames.ts:KNOWN_BACKEND_TOOLS  (verify entry present)"
    backward_compat_assertions:
      - "Build pages that landed via typedView='<typedView>' now route through surfaces.build === BuildExtended"
      - "Monitor dashboards mounting widget id '<widget_id_1>' (or '<widget_id_2>') continue to render"
      - "Ask handoff via KNOWN_BACKEND_TOOLS unchanged"
  reference_frontend_module: UI/.../<dual_view_sibling>/
  target_frontend_folder: UI/.../<tool>/
  required_tiers: [<tier_set>]                   # IMPORTANT: if existing tiers include manifest_typed_view, KEEP it; do NOT swap to generic_runnable
  design_guardrails:
    - "Mirror <sibling>'s dual-view shape (the parametric sibling — same data shape)"
    - "PRESERVE the existing monitor widgets at surfaces/monitor/; only their imports may shift to use the new <tool_slug>Shared.ts helper"
    - "DELETE surfaces/ResultRenderer.tsx after writing the new surfaces"
    - "Standalone-bridge: typedView → null; surfaces.{build, buildExtended, buildCompact}"
    - "Reuse the existing typed-detail endpoint at <endpoint_slug>; do NOT add a parallel route"
    - "Per Option (c) shell-density precedent (Batch 1 fdac7d2): keep shell-standard density on Compact (3 KPIs, single identity); document any density deviation in THESIS"
```

`pre_flight_backend_audit` block stays standard (the 4 checks). The Check 5 legacy-pattern detection is implicit in `build_mode: migration` — the orchestrator runs it automatically before dispatch.

## 8. Anti-patterns (auto-flag in review)

- A migration build that does NOT delete `surfaces/ResultRenderer.tsx` (the legacy file shadows the new dispatch path; running tests may still pass but a Build page may render the old view)
- A migration build that adds a parallel `<X>Output` type alongside the existing one in `src/types/rates.ts` (duplicate types confuse the dispatcher; bug surface in the centralised type imports)
- A migration build that adds a parallel `fetchDetail<X>` alongside the existing one in `src/services/ratesApi.ts`
- A migration build that adds a parallel `/detail/<slug>` route alongside the existing one in `api/routes/rates/detail.py`
- A migration build that changes a monitor widget's `id` (breaks every dashboard that mounted it)
- A migration build that drops a monitor widget entirely without replacement (breaks every dashboard that mounted it)
- A migration build that removes the `monitor_surface` tier when the tool was previously claiming it
- A migration build that swaps `manifest_typed_view` → `generic_runnable` (the backend's `_PRIMITIVE_SPECS` membership is unchanged; tier set should mirror backend reality)
- A migration build that keeps `module.ts.typedView` as a string (the whole point of migration is to remove the typed-view dispatch in favour of the standalone-bridge contract)
- A migration build that keeps `module.ts.workspaceLabel` (legacy field, not part of the dual-view contract)
- A migration build that keeps `module.ts.unsupportedReason` (legacy field; the dual-view + standalone-bridge IS the supported surface)
- A migration build that fails to update `THESIS.md` from the v2 stub to the full 5-question template (Q1 must enumerate the new buildExtended + buildCompact; Q2 must describe what the PM reads off each)
- A migration build that leaves the `__tests__/module.spec.ts` in legacy shape (no dual-view contract checks)

## 9. Reviewer checklist for migration (mirror of Section H in `CLAUDE_REVIEWER_PROMPT.md`)

After every migration dispatch, the reviewer verifies:

- **H1**: `git diff --name-only --diff-filter=D` includes `surfaces/ResultRenderer.tsx` for this tool — the legacy file was deleted.
- **H2**: `module.ts` no longer contains `typedView: '<string>'`; it now has `typedView: null` (or omits the field).
- **H3**: `module.ts` no longer contains `workspaceLabel:` or `unsupportedReason:`.
- **H4**: For each entry in `legacy_migration.monitor_widgets_to_preserve`:
  - the widget file at `path:` is unchanged in path (`git diff --name-only --diff-filter=D` does NOT include it)
  - the widget's `id` STILL appears in `MODULE.monitorWidgets[]`
  - the widget's `paramFields` field names + default values are unchanged
- **H5**: `src/types/rates.ts` was MODIFIED (verified type matches Pydantic) but NOT GROWN by a duplicate type. The `<X>Output` type appears exactly ONCE in the file.
- **H6**: `src/services/ratesApi.ts` was MODIFIED (verified fetcher matches Pydantic) but NOT GROWN by a duplicate fetcher. The `fetchDetail<X>` function appears exactly ONCE.
- **H7**: `api/routes/rates/detail.py` was MODIFIED (verified route mirrors Pydantic) but NOT GROWN by a duplicate route. The endpoint at `<endpoint_slug>` is defined exactly ONCE.
- **H8**: The `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/<tool_slug>Shared.ts` files all exist and are NEW (not existing files being modified).
- **H9**: `THESIS.md` Q1 enumerates BOTH `buildExtended` AND `buildCompact` (the migration target), PLUS any preserved monitor widgets.
- **H10**: `__tests__/module.spec.ts` calls `assertStandardModuleInvariants` AND the dual-view checks (per `STANDARD_MODULE_RULES.md` §8).
- **H11**: `module.ts.tiers` is consistent with the catalog entry's `required_tiers`. If the catalog says `[manifest_typed_view, ...]`, the module keeps that tier (do not swap to `generic_runnable`).
- **H12**: All three gates pass: `npm run test:modules`, `npm run test:build`, `npm run typecheck` — exit 0.

A failure on any of H1–H7 is a `STRUCTURAL:` finding (the migration didn't honestly migrate; the legacy pattern is partially in place AND the new pattern is partially in place — worst of both worlds).

A failure on H8–H10 is an actionable concrete finding (apply via Dispatch 3 fix pass).

A failure on H11 is `STRUCTURAL:` (the tier-set was changed in a way that violates backend reality).

A failure on H12 is auto-block per the standard pre-commit gate rule.

## 10. surface_contract.md row update

After a migration commits, the orchestrator updates `docs_revamped/02_components/surface_contract.md` §4.1 (Sovereign bonds) to flip the tool's row:

| Before | After |
|---|---|
| `build_archetype: typed-result-renderer` | `typed-result-renderer` (unchanged — the architecture is still typed-result-renderer; the tier just transitions to dual-view) |
| `build_status: typed-renderer-built` | `dual-view-built` |
| `monitor_status: built (<X>Widget)` | `built (<X>Widget)` (unchanged — preserved) |

Plus add a §11 changelog entry referencing this factory commit and the migration mode.

If the table format has drifted from this contract, record the intended row in `status_notes` and move on — do NOT fail the commit on a docs edit.

## 11. Links

- `docs_revamped/02_components/primitive/BUILD_GUIDE.md` — Stages 4 → 5 → 6 (the master spec)
- `docs_revamped/02_components/frontend_module/README.md` — FM1–FM12
- `docs_revamped/03_standards/rendering_density.md` §1 — dual-view mandate
- `docs_revamped/03_standards/methodology_exposure.md` §5 — standalone bridge contract
- `automation/frontend_automation/STANDARD_MODULE_RULES.md` — the new-build / steady-state contract (read alongside this file for migrations)
- `automation/frontend_automation/PRE_FLIGHT_BACKEND_AUDIT.md` §11 — Check 5 legacy-pattern detection
- `automation/frontend_automation/CLAUDE_BUILDER_PROMPT.md` "Migration mode" — the builder's migration-specific instructions
- `automation/frontend_automation/CLAUDE_REVIEWER_PROMPT.md` "H. Migration-mode review" — the reviewer's migration-specific checks
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_ois_curve_spread_tool/` — the canonical migration reference for `calculate_curve_spread_tool` (dual-view spread sibling)
- `UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/` — the canonical migration reference for `get_yield_levels_tool` (dual-view snapshot sibling)
