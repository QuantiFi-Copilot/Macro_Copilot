# THESIS — `calculate_yield_change_attribution_pca_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_yield_change_attribution_pca_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `model_fits`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — rich-model module: `surfaces/BuildSurface.tsx` is a lazy wrapper around the shared `BuilderCanvas` (full model playground — controls rail + output canvas + interpretation cards).  Sets `MODULE.richModel: true` and carries the full `ModelMetadata` block via `MODULE.modelMetadata`.
- **`custom_preview_widget`** — per-tool persisted-artifact card at `surfaces/PreviewWidget.tsx`, a thin caller of the shared `RichModelWidget`.  Per-tool copy comes from `MODULE.modelAdapter`; the `widgets/index.ts` barrel walks `ALL_PRIMITIVE_MODULES` and registers each populated preview into the per-tool `nodeRendererRegistry`.

## 2. What does the user read off each surface?

* Build (rich-model canvas): curve_family + target_tenor + start_date + end_date + n_components controls; output canvas shows the per-component contribution waterfall, residual, and a Sign-convention interpretation card.
* Preview (persisted artifact): registers under BOTH Series AND Panel artifact types (via `MODULE.previewArtifactTypes`).  Pure-snapshot tool — the preview renders an honest 'snapshot view not persistable today' callout because the backend output_class has no time_series field to lift.

## 3. Why these surfaces and not others?

Attribution is rich-model because the user needs the explicit PCA-loadings + dates window + components controls; no generic form captures the waterfall output.  The persisted-artifact Panel registration is unique to this primitive — the bridge can lift the snapshot table as a Panel even though it can't lift a Series.

## 4. What would change the design?

Concrete triggers:
- Backend extending output_class with a per-component time_series field → the preview can render a real persisted Series; flip `hasTimeSeriesOutput: true` in the modelAdapter.
- Cross-curve attribution → sibling primitive.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims the runtime
  tier `generic_runnable` and capability tiers
  `[custom_build_surface, custom_preview_widget]`.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — every claimed capability tier has
  a matching populated surface file under `surfaces/`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Decompose a sovereign yield change at a given tenor over a window into per-PCA-component contributions in bps.  Loadings come from an inline PCA fit or a caller-supplied pasted payload.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
