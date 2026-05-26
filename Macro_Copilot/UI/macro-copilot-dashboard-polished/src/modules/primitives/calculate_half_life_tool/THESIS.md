# THESIS — `calculate_half_life_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_half_life_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `model_fits`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — rich-model module: `surfaces/BuildSurface.tsx` is a lazy wrapper around the shared `BuilderCanvas` (full model playground — controls rail + output canvas + interpretation cards).  Sets `MODULE.richModel: true` and carries the full `ModelMetadata` block via `MODULE.modelMetadata`.
- **`custom_preview_widget`** — per-tool persisted-artifact card at `surfaces/PreviewWidget.tsx`, a thin caller of the shared `RichModelWidget`.  Per-tool copy comes from `MODULE.modelAdapter`; the `widgets/index.ts` barrel walks `ALL_PRIMITIVE_MODULES` and registers each populated preview into the per-tool `nodeRendererRegistry`.

## 2. What does the user read off each surface?

* Build (rich-model canvas): series_spec control + AR(1) / OU fit output panel showing half-life (with delta-method CI), long-run mean, current deviation, and the underlying OU β.
* Preview (persisted artifact): pure-snapshot tool — renders the honest 'snapshot view not persistable today' callout via `MODULE.modelAdapter` with the per-tool detailUnavailable list.

## 3. Why these surfaces and not others?

Half-life is a single-knob model where the user picks the series and reads the resulting scalar snapshot — perfect rich-model fit.  No Monitor tile today because the scalar isn't a continuously-streaming signal.

## 4. What would change the design?

Concrete triggers:
- Backend persisting half-life Series snapshots → preview becomes a real persisted-Series card.
- Pair-spec mode upgrades (z-score on the residual pair-series) → extend the rich-model controls.

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

Ornstein-Uhlenbeck / AR(1) fit on a supplied series.  Returns half-life of mean reversion (trading days), long-run mean, current deviation, OU β with confidence interval, and a delta-method CI on the half-life itself.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
