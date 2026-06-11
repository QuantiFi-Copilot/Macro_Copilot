# THESIS — `calculate_rolling_regression_tool`

> Rich-model module migrated to the dual-view rendering-density standard (consolidation target #4).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — composed from the shared rich-model grammar at `@/components/shared/build/model`, plus the unchanged persisted-artifact preview.

**Version:** v3 (dual-view migration off the legacy BuilderCanvas)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_rolling_regression_tool` (generic_runnable; standalone bridge via `GET /api/v1/rates/detail/rolling-regression`)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `rolling_analytics`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract, composed from the rich-model grammar (`ModelResultLayout` / `ModelKpiStrip` / `ModelSeriesPanel` / `QualityBadge`):
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas for single-tool queries.  Controls strip (target curve+tenor, up to 3 paired regressor selects, the central `regression_window_days` knob, Advanced lookback + field), then the grammar zones in `ModelResultLayout` order: hero KPI strip (target / per-regressor β / R² emphasised / residual), the rolling-β multi-line panel (zero line, legend carries current β per regressor), residual+α and R² secondary panels, the fit-diagnostics strip (window / min-periods / intercept / observations echoes + condition `QualityBadge`), and the methodology card threaded from the response's `*_used` echo fields.  Lineage footer at the bottom.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card for multi-tool DAG nodes (e.g. *"regress BTP 10Y on Bund AND regress UST 10Y on 5s"*).  Identity `{target_label} ~ N regressors`, three KPIs (first-regressor β, R², residual), the first beta series as sparkline, the rolling-window caveat footer, click-to-expand affordance.
- **`custom_preview_widget`** — per-tool persisted-artifact card at [`surfaces/PreviewWidget.tsx`](surfaces/PreviewWidget.tsx), a thin caller of the shared `RichModelWidget`.  Per-tool copy comes from `MODULE.modelAdapter`; the `widgets/index.ts` barrel walks `ALL_PRIMITIVE_MODULES` and registers it under `(Series, toolName)`.  **Unchanged by the dual-view migration** — this path never depended on `modelMetadata`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE rolling fit.  A PM reads, in order: (a) the title row stating the fitted equation (`UST_10Y ~ UST_5Y + …`) with the latest-fit condition badge inline; (b) the hero strip — target identity, current β per regressor (first β + count when >2), in-window R² (the emphasised cell: *is the linear relationship even holding?*), and the latest residual in yield-percent; (c) the rolling-β panel — one line per regressor on the shared model tone cycle, zero reference line, current levels in the legend; (d) the residual+α panel (both in yield-percent, one honest shared axis) and the rolling-R² panel; (e) the fit-diagnostics strip — every methodology echo the backend discloses (`regression_window_days_used`, `regression_min_periods_used`, `add_constant_used`, `observation_count`) plus the condition badge; (f) the methodology card with the construction, units, quality-gate semantics, and the two desk reads (β interpretation, R²-collapse tell).  Controls re-fetch the same typed-detail endpoint on every change.

### Compact Build view

The at-a-glance DAG node.  THREE numbers + a sparkline: first-regressor β (the hedge-ratio read), R² (fit health), residual (today's miss).  The sparkline is the FIRST beta series — beta drift is the compact-card signal; the full per-regressor set is one expand-click away.  The footer carries the honesty caveat: *"Trailing-window OLS — betas move with the window; near-singular fits are suppressed."*

### Persisted preview

One persisted rolling Series (whichever `output_field` the workspace selected) with the honest *"latest snapshot + peer series + condition flag are NOT in this artifact"* affordance from `modelAdapter.detailUnavailable`.

---

## 3. Why these surfaces and not others?

**Why `modelMetadata` was removed (the routing decision).**  `contextDecoder` routes `kind: 'builder'` — the legacy `BuilderCanvas` playground — whenever a tool has a model-registry entry, and the registry is derived from `MODULE.modelMetadata`.  As long as the field existed, the dual-view surfaces could never mount: builder routing preempts every other dispatch.  Removing `modelMetadata` and setting `richModel: false` lets `VirtualPrimitiveCanvas` fall through to `surfaces.buildExtended` (single-tool) and `DagNodeBody` to `surfaces.buildCompact` (multi-tool DAG nodes — where the legacy builder-class tools were previously FILTERED OUT of the list entirely, rendering nothing).  The PM-read copy the legacy interpretation cards carried is preserved on the spec's top-level `interpretationCards` and threaded into the extended view's panel descriptions + methodology rows.

**Why the persisted path is retained untouched.**  `modelAdapter` + `surfaces.preview` (RichModelWidget) render persisted Series artifacts in completed workspaces.  That path is keyed off `getModelAdapter(toolName)` and the widgets barrel — not off `modelMetadata` — so the migration does not touch it; the `widgets/index.ts` guard ensures the bespoke preview keeps winning over `StandardPreviewWidget`.

**Why grammar components rather than a bespoke canvas.**  The shared grammar (consolidation target #4) gives every rich model the SAME section order, tone cycle, legend treatment, and quality vocabulary — PCA's factor scores and rolling regression's betas read identically (P3).  The retired legacy renderer (`components/build/model/renderers/RollingRegressionRenderer.tsx`) had already converged on this structure (snapshot strip → beta chart → R² ribbon → residual/α minis); the migration maps the same content decisions onto `ModelResultLayout` zones instead of bespoke recharts blocks.

**Why these three compact KPIs** (β₁ / R² / residual): they answer *"what's the hedge ratio / is the fit holding / how far off the line are we today"* — the desk's first three questions of any rolling fit.  Alternatives rejected: α (a level offset, rarely the headline), observation_count (diagnostics, not signal), the full per-regressor β set (doesn't fit 3 cells; lives in the extended legend).

**Why α shares a panel with the residual:** both are yield-percent on the wire (`time_series_alpha.units === time_series_residual.units === 'percent'`), so a shared axis is honest; a separate α panel would burn a zone on the least-read series.  R² gets its own panel because its [0,1] ratio scale must not be mixed onto a percent axis (FP9).

**Why a typed-detail endpoint** (`GET /api/v1/rates/detail/rolling-regression`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge.  The Input's nested `List[SeriesSpec]` flattens to paired query lists (`regressor_curve_families[i]` + `regressor_tenors[i]`, single `field_name` for all legs); the frontend carries the same lists as comma-joined flat params — the convention is documented once, in [`surfaces/rollingRegressionShared.ts`](surfaces/rollingRegressionShared.ts).

**No mockups for this migration:** the visual contract is inherited from the shared grammar components (whose treatments are pinned by the grammar's own consolidation work) rather than from per-tool PNGs.

---

## 4. What would change the design?

- **Per-leg `field_name` overrides** land on the bridge (today: one field for all legs, per the route's A13 note) → add per-slot Advanced field selects; the wire convention gains a third paired list.
- **More than 3 regressors** becomes a real desk pattern → widen `MAX_REGRESSOR_SLOTS` in `rollingRegressionShared.ts` (the wire + endpoint already accept any N ≥ 1; only the controls strip is capped).
- **Masking suppressed regions** — the backend ships `time_series_condition_flag` per row; a shared `ModelSeriesPanel` enhancement that greys flagged spans would backport to every model at once.  Until then the latest-row badge + methodology row carry the disclosure.
- **Backend persisting multi-output** (all rolling series in one artifact) → upgrade the preview to render the full peer set; `modelAdapter.detailUnavailable` shrinks accordingly.
- **The legacy `?builder=` deep-link form dies** → drop the transitional `surfaces.build` alias once BuildShell's builder branch and the invariant helper read `buildExtended` natively.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `custom_preview_widget`.
- **FM5/FM5d** (display metadata + persisted ModelAdapter) — `modelAdapter` retained verbatim for the persisted-artifact path.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` + `surfaces.preview` populated; files at canonical paths; transitional `build` alias === `buildExtended`.
- **FM9** (routing-claim disclosure) — `typedView: null`, `richModel: false`; `modelMetadata` removed so dispatch reaches the dual-view surfaces (Q3).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the standard invariants + the dual-view contract + the modelMetadata removal.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **P5** (honest disclosure) — methodology card threads `regression_window_days_used` / `regression_min_periods_used` / `add_constant_used` from the response; condition-flag semantics surfaced as a `QualityBadge` + methodology row.
- **FP9** (render backend numbers unchanged) — all series + metrics pass through untouched; the only client-side reshaping is the date-union pivot inside the shared panel.
- **FP13** (finance-blind shared layer) — everything OLS-specific lives in this folder; the grammar components receive shapes only.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint, own service helper `fetchDetailRollingRegression`, own frontend types `RollingRegressionOutput` + `RollingRegressionMetrics`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces`.

---

## One-line summary

Rolling OLS regression of one sovereign yield on one or more regressor yields via numpy.linalg.lstsq, returning per-regressor betas, alpha, residual, in-window R², and a condition-number quality flag.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Dual-view migration (consolidation target #4): shipped `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` + `surfaces/rollingRegressionShared.ts` composed from the shared rich-model grammar; removed `modelMetadata` (+ `richModel: false`) so dispatch reaches the dual-view surfaces; retired `surfaces/BuildSurface.tsx` (BuilderCanvas wrapper); retained `modelAdapter` + `surfaces/PreviewWidget.tsx` (persisted path) untouched.  Standalone bridge at `GET /api/v1/rates/detail/rolling-regression`. |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
