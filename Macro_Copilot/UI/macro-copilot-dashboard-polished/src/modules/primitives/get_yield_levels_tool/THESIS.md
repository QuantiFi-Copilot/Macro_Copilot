# THESIS — `get_yield_levels_tool`

> Migration dispatch — converted from the legacy typed-renderer pattern (`typedView: 'yield'` + `surfaces/ResultRenderer.tsx`) to the new dual-view + standalone-bridge contract per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 + [`methodology_exposure.md`](../../../../../docs_revamped/03_standards/methodology_exposure.md) §5.  The existing `yield_level` Monitor widget is preserved verbatim — its `id` and `paramFields` are the backward-compat lock (MIGRATION_RULES §6).

**Version:** v3 (migration to dual-view + standalone-bridge)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_yield_levels_tool` (generic_runnable; backend `_PRIMITIVE_SPECS` entry; standalone bridge via `/api/v1/rates/detail/yield`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) (Build dual-view) — committed alongside the module per the user's mockup-first workflow.  Monitor tile is unchanged from the pre-migration shape (no separate mockup needed; Monitor surface is inherently compact per `rendering_density.md §8`).

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view Build contract (NEW in this migration):
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link, OR via the click-to-expand modal from a compact card).  Controls strip (curve / tenor / lookback / field), KPI strip with 9 metrics (YIELD / 1D / 5D / 1M / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS), main chart with ±2σ / ±1.5σ z-score envelope bands, top-right Z-Score / Percentile / Market cards, stretch-context panel, methodology card with config-derived disclosure, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations (e.g. *"compare UST 10Y vs Bund 10Y vs JGB 10Y"*).  3 headline KPIs (YIELD / 1D CHANGE / Z-SCORE), mini-chart with ±2σ z-score envelope, sovereign-vs-OIS caveat in the footer, click-to-expand affordance calling the shared `onExpand` prop.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/YieldLevelWidget.tsx`](surfaces/monitor/YieldLevelWidget.tsx)) — PRESERVED VERBATIM from the pre-migration shape; widget id `yield_level` and paramFields (`curve_family`, `tenor`, `lookback_days`) unchanged.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE sovereign yield observation.  They read, in order: (a) the title row identifying the curve + tenor + as-of date (e.g. "UST 10Y Yield"); (b) the top-right Z-Score (252D) / Percentile (252D) / Market cards for the at-a-glance "is this stretched?" read; (c) the 9-cell KPI strip for the full numeric picture (current yield + 1d/5d/1m changes in bps + z-score + percentile + 252d high/low + observations); (d) the main chart with z-score band overlays to see the historical context for the current observation; (e) the stretch-context panel for an interpretation paragraph (regime + direction + bucket); (f) the methodology card with series / field / z-score model / cleaning / disclosure rows; (g) the lineage footer to confirm freshness + provider chain.  The controls strip lets them swap curve_family / tenor / lookback / field.

### Compact Build view

The at-a-glance grid card.  A PM sees this when a multi-tool prompt contains this primitive among others.  They read THREE numbers + a sparkline: current yield (in %), 1-day change (in bps + percent subtext, tone-coloured for tightening/easing), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the lookback-window history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The footer carries the sovereign-vs-OIS caveat (technical-basis disclosure).  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

### Monitor view (preserved widget)

The desk bento card.  A PM mounts this on their morning-briefing board parameterised on (curve_family, tenor, lookback_days).  They read: short market tag + tenor + z-score chip in the header; current yield in big type; 1d change + 252d percentile; a 252d range strip showing where the current observation sits between trailing-low and trailing-high.  Same canonical-three-numbers read as the compact Build view, optimised for the bento-grid footprint.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current yield + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a sovereign yield snapshot.  Alternatives considered + rejected: 252d percentile (already conveyed by the z-score regime + chart bands), weekly / monthly change (lower-frequency reads; the 1d change is the actionable signal), 252d high/low (range context, not headline data).  The chosen three answer "where are yields now / how much did they move today / is this stretched?" in one glance.  Per the Batch 1 Option (c) precedent (commit fdac7d2): shell-standard density on Compact — 3 KPIs, single identity row, footer caveat.

**Why reuse the existing `/detail/yield` endpoint** rather than add a parallel one: per MIGRATION_RULES §4 step 10 — the typed-detail endpoint already exists at `api/routes/rates/detail.py:660`, the `fetchDetailYield` service helper already exists at `src/services/ratesApi.ts:129`, and the `YieldLevelOutput` TS type already exists at `src/types/rates.ts:185`.  All three match the Pydantic Input / Output exactly; no fields need to be added.  The migration is purely a frontend-surface change.

**Why preserve the legacy `yield_level` widget verbatim** rather than refactor: per MIGRATION_RULES §6 + the catalog's `monitor_widgets_to_preserve` block — the widget `id` is the backward-compat lock for dashboards that persisted it.  Refactoring the widget's internals to import from `yieldLevelShared.ts` was considered but rejected for V1: keeping the widget functionally identical minimises the migration's blast radius.  Future PRs can re-platform the widget onto the shared helper without changing behaviour.

**Why NOT advanced z-score override controls** (e.g. `z_score_window_days`): the sovereign `YieldLevelInput` Pydantic schema does NOT accept those fields — they're real_yield_level-only Phase-1 exposures.  Adding them to the Build extended view would silently fail (the backend would ignore the query params).  The z-score model (252-day rolling, sample stdev, ddof=1) is documented in the methodology card.

---

## 4. What would change the design?

- **The backend ships `current_metrics.methodology_label`** (the standalone-bridge wire-honesty field per PR10) → the Disclosure row in the methodology card switches from a config-derived literal to a wire-sourced string.  One-line edit in [`yieldLevelShared.ts`](surfaces/yieldLevelShared.ts) — see the `buildMethodologyRows` Disclosure row.
- **A backend exposure of `z_score_window_days` / `z_score_min_periods` / `z_score_ddof`** lands (matching the real_yield_level Phase-1 exposure decisions) → add three Advanced controls to the BuildExtended controls strip; add the params to the `useYieldLevel` hook + `YieldDetailParams`.  No shell changes.
- **A new sovereign curve_family** lands in the backend universe → add the entry to `FAMILY_REGISTRY` in [`yieldLevelShared.ts`](surfaces/yieldLevelShared.ts) (short label / long label / flag).  No shell or test changes; the controls dropdown auto-includes it.
- **Backend output schema gains a per-day z-score series** (instead of a single rolling z) → the main chart can overlay the z-score series; the per-day tone-coloring matures.  The chart shell already accepts the data; only the per-tool wrapper changes.
- **The desk asks for the legacy pre-aggregated `yield_snapshot` widget to graduate onto this tool's typed-detail endpoint** → out of scope for this migration (it currently reads from the dashboard aggregate endpoint).  Would be a separate PR re-platforming `monitor/registry.ts:yield_snapshot` onto `fetchDetailYield`.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`; per the rendering-density standard the `custom_build_surface` tier REQUIRES both `buildExtended` + `buildCompact` files.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.  `monitor_surface` retained from the pre-migration claim (preserved widget — backward-compat lock).
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects, no register() calls.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; `surfaces.resultRenderer` deleted; corresponding files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern; the legacy `typedView: 'yield'` claim is removed.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract via `assertStandardModuleInvariants` plus explicit dual-view + standalone-bridge checks.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts) — unchanged from the pre-migration shape.
- Per the project's **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): reuses the existing typed-detail endpoint at `/api/v1/rates/detail/yield`; reuses the existing service helper `fetchDetailYield`; reuses the existing frontend type `YieldLevelOutput`; no parallel central infrastructure added.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both are wired in `module.ts.surfaces` + corresponding files at canonical paths.
- Per **MIGRATION_RULES §6** (backward-compat lock): the `yield_level` Monitor widget's `id` and `paramFields` (`curve_family`, `tenor`, `lookback_days`) are preserved verbatim.

---

## Migration audit

| Audit point | Status |
|---|---|
| Files DELETED | `surfaces/ResultRenderer.tsx` |
| Files NEW | `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/yieldLevelShared.ts` |
| Files OVERWRITTEN | `module.ts`, `THESIS.md`, `__tests__/module.spec.ts` |
| Files PRESERVED | `surfaces/monitor/YieldLevelWidget.tsx` (path + widget id + paramFields unchanged) |
| `module.ts.typedView` | `'yield'` → `null` |
| `module.ts.surfaces` | `{ resultRenderer }` → `{ build, buildExtended, buildCompact }` |
| `module.ts.richModel` | added `false` |
| Central files VERIFIED unchanged-in-name | `src/types/rates.ts:YieldLevelOutput`, `src/services/ratesApi.ts:fetchDetailYield`, `api/routes/rates/detail.py:/detail/yield`, `src/modules/index.ts:get_yield_levels_tool`, `src/lib/toolNames.ts:KNOWN_BACKEND_TOOLS` |

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-10 | Migration to dual-view + standalone-bridge per `rendering_density.md` §1 + `methodology_exposure.md` §5.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and shared helper `surfaces/yieldLevelShared.ts`.  Deleted `surfaces/ResultRenderer.tsx`.  Preserved `yield_level` Monitor widget verbatim (id + paramFields unchanged).  `typedView: 'yield'` removed; surfaces flipped to `{ build, buildExtended, buildCompact }`.  Central files unchanged (existing `/detail/yield` endpoint + `fetchDetailYield` + `YieldLevelOutput` reused). |
| v2 | 2026-05-08 | Stage 4d — Monitor catalog: added parameterised `yield_level` widget. |
| v1 | 2026-04-15 | Stage 4a — typed-view module under `typedView: 'yield'` + `surfaces.resultRenderer`. |
