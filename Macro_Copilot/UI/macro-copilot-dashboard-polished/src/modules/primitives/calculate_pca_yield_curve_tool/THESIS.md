# THESIS — `calculate_pca_yield_curve_tool`

> Rich-model module migrated to the **dual-view rendering-density contract**: the legacy BuilderCanvas route (`modelMetadata` + `richModel: true` + `PcaLoadingsRenderer`) is retired in favour of a `buildExtended` + `buildCompact` pair composed from the shared rich-model grammar at `@/components/shared/build/model` — both views REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (dual-view migration onto the rich-model grammar)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_pca_yield_curve_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/pca-yield-curve`)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `model_fits`
**Mockups:** captured by the integrator post-merge (deliverable scope for this migration is code-only).

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (curve_family / tenors subset / lookback / n_components / change_frequency + advanced field_name override) above a `ModelResultLayout` composition: hero `ModelKpiStrip`, loadings `MatrixTable` with per-component quality chips, `DecompositionBars` (variance shares + Σ cumulative), `ModelSeriesPanel` (factor scores keyed pc1..pcN with current levels in the legend), a diagnostics strip + per-component `QualityBadge` row, a `MethodologyCard` threaded from the response's `*_used` echo fields, and the retained PC1/PC2/PC3 interpretation cards.  `LineageFooter` closes the canvas.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs.  Headline 3-KPI strip (VAR EXPLAINED / PC1 LEVEL / COMPONENTS), pc1 factor-score sparkline, the statistical-factors honesty caveat in the footer, click-to-expand affordance.  Composed via the shared `BuildCompactShell`.
- **`custom_preview_widget`** — per-tool persisted-artifact card at [`surfaces/PreviewWidget.tsx`](surfaces/PreviewWidget.tsx), a thin caller of the shared `RichModelWidget`.  **Untouched by this migration** — per-tool copy still comes from `MODULE.modelAdapter`; the `widgets/index.ts` barrel registers it from `surfaces.preview`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas for ONE PCA fit.  A PM reads, in order: (a) the identity header (`{family} PCA`, components × change frequency, as-of date); (b) the hero strip — components returned, **total variance explained** (the emphasized number), current PC1 level, observation count; (c) the loadings matrix (tenors × pcN) with the shared diverging shading and per-component quality chips in the column headers; (d) variance explained per component with the running Σ cumulative; (e) the factor-score time series with current levels in the legend; (f) the diagnostics strip — fit window, change frequency, sign anchor, lookback echo — plus the per-component quality-flag row; (g) the methodology card assembled from the response's echo fields; (h) the canonical-interpretation cards (PC1=level / PC2=slope / PC3=curvature / variance-explained read), explicitly labelled as interpretation rather than response-threaded methodology; (i) the lineage footer.

### Compact Build view

The at-a-glance grid card for multi-tool prompts (e.g. *"PCA the UST curve and the Bund curve"*).  A PM reads THREE numbers + a sparkline: total variance explained (emphasis), the current PC1 factor level, and the component count, over the pc1 score path.  The footer carries the statistical-factors caveat; the expand arrow opens the extended view in a modal.

---

## 3. Why these surfaces and not others? (Routing-claim changes in this migration)

**Why `modelMetadata` was REMOVED + `richModel: false`.**  The central `contextDecoder` routes `kind: 'builder'` — the legacy BuilderCanvas redirect — whenever `hasModelMetadata(toolName)` is true, and that branch has the HIGHEST priority, so it preempts the module-first dual-view dispatch entirely.  Removing the block (and setting `richModel: false`) is what lets the decode fall through so `VirtualPrimitiveCanvas` mounts `surfaces.buildExtended`.  Consequences the integrator should know (documented in the migration report): the tool drops out of `modelRegistry.MODELS`, so `ToolDetailDrawer`'s Library CTA stops saying "Open in builder" and rides the `?context=` route (which now lands on the NEW extended view — an upgrade), `paramHintFor` falls back to `inferFieldControl` for the generic-builder path, and `OutputCanvas`/`InterpretationCards` no longer find a registry entry (both are on the retired BuilderCanvas route).

**What was RETAINED.**  `modelAdapter` + `surfaces.preview` are kept EXACTLY as they were: the persisted-artifact `RichModelWidget` path reads `MODULE.modelAdapter` and the `widgets/index.ts` barrel registers `surfaces.preview` — reconciling that path onto the grammar is a separate workstream.  `surfaces.build` is kept as a transitional alias of `BuildExtended` for the legacy dispatchers (BuildShell's `?builder=` branch + VirtualPrimitiveCanvas's fallback chain).  The legacy `surfaces/BuildSurface.tsx` wrapper and the central `PcaLoadingsRenderer` stay on disk un-imported; deletion happens in a later cleanup.

**Why the interpretation cards moved to the spec's top level.**  The PC1=level / PC2=slope / PC3=curvature reads are PM-facing copy worth keeping; `modelMetadata.interpretationCards` was their only home and it's gone.  They now live on the spec's top-level `interpretationCards` field (sourced from `PCA_INTERPRETATION_CARDS` in [`surfaces/pcaYieldCurveShared.ts`](surfaces/pcaYieldCurveShared.ts)) and render in the Extended methodology zone, labelled "canonical interpretation (not response-threaded)" so they cannot be mistaken for P5 methodology disclosure.

**Why these three compact KPIs** (variance explained / PC1 level / components): they answer the desk's first three questions about a PCA fit — *"how much of the curve's movement does this basis capture / where is the dominant factor now / how many factors am I looking at?"*.  Alternatives considered + rejected: per-component variance rows (the compact card has no room for N rows; the Σ headline conveys fit quality), the loadings matrix (the extended view's primary visual — unreadable at card scale), fit-window dates (diagnostics detail, not headline).

**Why the sparkline is pc1**: the first component is the fit's dominant story (highest variance share by construction), and a single-series sparkline is the shell's level-shape contract.  The full pcN overlay belongs to the extended `ModelSeriesPanel`.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/pca-yield-curve`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  Only the structural inputs are exposed (curve_family / tenors / lookback_days / n_components / change_frequency / field_name); every other methodology knob is YAML-locked per A13 and therefore deliberately NOT a control.

---

## 4. What would change the design?

- **The persisted-artifact reconciliation workstream lands** → `surfaces/PreviewWidget.tsx` + `modelAdapter` migrate onto the grammar; until then both stay frozen.
- **The legacy dispatchers retire** (`?builder=` branch + `surfaces.build` fallback) → drop the transitional `build` alias from `module.ts`; the spec test pins the dual-view fields either way.
- **Backend ships a prose methodology field on the Output** → the methodology card switches from the assembled-echo-fields form to threading the prose verbatim (P5), and the "Source" row goes away.
- **A cross-curve PCA panel primitive lands** → sibling module, not an extension here.
- **Mockups land** → restore the pilot's mockups spec check in [`__tests__/module.spec.ts`](__tests__/module.spec.ts).

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `custom_preview_widget`.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value (the interpretation cards are a shared constant, not a computed value).
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; `surfaces.preview` retained.
- **FM9** (routing-claim disclosure) — `typedView: null`, `richModel: false`; this module owns its own full surfaces (no typed-view reuse, no legacy builder claim).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract + the modelMetadata-removal/persisted-retention invariants.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **FP13** (finance-blind shared layer) — every result visual is a shared grammar component (`ModelResultLayout`, `ModelKpiStrip`, `MatrixTable`, `DecompositionBars`, `ModelSeriesPanel`, `QualityBadge`); all PCA-aware mapping lives in [`surfaces/pcaYieldCurveShared.ts`](surfaces/pcaYieldCurveShared.ts).
- **P5** (honest disclosure) — methodology threads from the response's `*_used` echo fields and says so; the pc-label honesty caveat (statistical factors, not guaranteed level/slope/curvature) is inline in both views.
- Per the **rendering-density dual-view contract** ([`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1–§2): both views ship + both wired in `module.ts.surfaces` + the compact view is a curated headline read, not a shrunken extended view.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Dual-view migration onto the rich-model grammar.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/pcaYieldCurveShared.ts`.  REMOVED `modelMetadata` / set `richModel: false` (retires the legacy BuilderCanvas route); RETAINED `modelAdapter` + `surfaces.preview` (persisted-artifact path).  Interpretation cards moved to the spec's top-level field.  Standalone bridge at `/api/v1/rates/detail/pca-yield-curve`. |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
