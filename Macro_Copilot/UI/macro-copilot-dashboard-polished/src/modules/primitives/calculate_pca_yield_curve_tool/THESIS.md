# THESIS — `calculate_pca_yield_curve_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_pca_yield_curve_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `model_fits`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — rich-model module: `surfaces/BuildSurface.tsx` is a lazy wrapper around the shared `BuilderCanvas` (full model playground — controls rail + output canvas + interpretation cards).  Sets `MODULE.richModel: true` and carries the full `ModelMetadata` block via `MODULE.modelMetadata`.
- **`custom_preview_widget`** — per-tool persisted-artifact card at `surfaces/PreviewWidget.tsx`, a thin caller of the shared `RichModelWidget`.  Per-tool copy comes from `MODULE.modelAdapter`; the `widgets/index.ts` barrel walks `ALL_PRIMITIVE_MODULES` and registers each populated preview into the per-tool `nodeRendererRegistry`.

## 2. What does the user read off each surface?

* Build (rich-model canvas): controls rail (curve_family, tenors, lookback_days, n_components, change_frequency) on the left; PCA loadings table + variance-explained bar + factor-score time series on the centre; PC1=Level / PC2=Slope / PC3=Curvature interpretation cards on the right.
* Preview (persisted artifact): single factor-score Series with an honest 'rich detail not in this snapshot' callout (per-tenor loadings, variance, current factor levels, diagnostics flags live only on the live *Output dict).

## 3. Why these surfaces and not others?

PCA is the canonical analytical-model primitive; the rich BuilderCanvas surface and the persisted-artifact card both earn their bespoke treatment.  No Monitor tile today because the model is parameter-heavy (each user picks their own curve / window / components).

## 4. What would change the design?

Concrete triggers:
- Backend persisting the full PCA output (loadings + variance + current factor levels) → the preview widget upgrades from 'pure_snapshot_unavailable' callout to a full snapshot view.
- Cross-curve PCA panel demand → sibling primitive, not extension.

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

PCA on the yield-CHANGES panel of one sovereign curve.  Returns per-component loadings, variance shares, factor-score time series, and per-component quality metadata (degenerate + sign-anchor flags).

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
