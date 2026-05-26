# THESIS — `calculate_beta_adjusted_spread_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_beta_adjusted_spread_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `rolling_analytics`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — rich-model module: `surfaces/BuildSurface.tsx` is a lazy wrapper around the shared `BuilderCanvas` (full model playground — controls rail + output canvas + interpretation cards).  Sets `MODULE.richModel: true` and carries the full `ModelMetadata` block via `MODULE.modelMetadata`.
- **`custom_preview_widget`** — per-tool persisted-artifact card at `surfaces/PreviewWidget.tsx`, a thin caller of the shared `RichModelWidget`.  Per-tool copy comes from `MODULE.modelAdapter`; the `widgets/index.ts` barrel walks `ALL_PRIMITIVE_MODULES` and registers each populated preview into the per-tool `nodeRendererRegistry`.

## 2. What does the user read off each surface?

* Build (rich-model canvas): target_spec + hedge_spec + window-length + lookback controls; output canvas plots rolling beta / residual / residual z-score time series with an interpretation card explaining the rich/cheap thresholds.
* Preview (persisted artifact): single rolling output Series (beta / residual / z-score — whichever the workspace selected) with the per-tool detailUnavailable callout.

## 3. Why these surfaces and not others?

Bivariate hedge-ratio RV is a recurring desk pattern (BTP-Bund hedged, EUR/USD hedged) and the dynamic-beta time series is itself the signal — earns the rich BuilderCanvas treatment, with the persisted preview surfacing one selected series at a time.

## 4. What would change the design?

Concrete triggers:
- Backend persisting multi-output → upgrade preview.
- Multi-regressor mode → sibling primitive (this one is intentionally bivariate).

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

Bivariate beta-adjusted RV — rolling OLS regresses one sovereign yield (target) on another (regressor); returns hedge ratio (beta), alpha (yield-percent), residual in bps, and a rolling z-score on the residual.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
