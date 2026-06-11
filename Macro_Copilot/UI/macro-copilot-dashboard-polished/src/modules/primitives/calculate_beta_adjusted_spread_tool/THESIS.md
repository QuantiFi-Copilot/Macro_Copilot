# THESIS — `calculate_beta_adjusted_spread_tool`

> Rich-model module migrated to the dual-view rendering-density standard (consolidation target #4).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — composed from the shared rich-model grammar at `@/components/shared/build/model`, plus the unchanged persisted-artifact preview.

**Version:** v3 (dual-view migration off the legacy BuilderCanvas)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_beta_adjusted_spread_tool` (generic_runnable; standalone bridge via `GET /api/v1/rates/detail/beta-adjusted-spread`)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `rolling_analytics`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — BOTH dual-view Build surfaces:
  - **`surfaces/BuildExtended.tsx`** — full canvas: controls strip
    (target leg, hedge leg, fit window — the central knob — lookback,
    field override) + `ModelResultLayout` (hero KPI strip → residual
    z-score panel → residual-bps + rolling-β panels → diagnostics with
    the condition `QualityBadge` → methodology card threaded from the
    response's `*_used` echo fields + `spread_label`).
  - **`surfaces/BuildCompact.tsx`** — grid card: 3 desk KPIs
    (RESIDUAL Z · RESIDUAL bps · β) + residual-z sparkline + the
    cheap/rich sign-convention caveat + `onExpand`.
- **`custom_preview_widget`** — `surfaces/PreviewWidget.tsx`
  (UNCHANGED): the persisted-artifact card via `RichModelWidget` +
  `MODULE.modelAdapter` — a saved Series (β / residual / residual-z,
  whichever `output_field` selected) renders read-only by hash.
- Both Build views fetch the SAME typed-detail endpoint through
  `surfaces/betaAdjustedSpreadShared.ts` (single data hook, KPI
  builders, caveat strings — P10).

## 2. What does the user read off each surface?

**Extended.** "Is BTP 10Y rich or cheap vs Bunds after the right hedge
ratio?"  The hero strip leads with the residual z-score (the stretch
read), then the bps residual with its cheap/rich tone, the rolling β,
and R².  The primary panel charts the residual z through time; the
secondary panels chart the raw bps residual and the drifting hedge
ratio.  Diagnostics echo every fit knob (window, min periods, z-window,
intercept, observations) plus the near-singular condition flag as a
`QualityBadge`.

**Compact.** The three numbers a PM triages: how stretched (z), how big
(bps), and what hedge ratio produced the read — with the residual-z
sparkline for shape and the sign convention as the caveat line.

**Preview (persisted).** The saved series with the adapter's honest
"what's NOT in this artifact" list, deferring full snapshot KPIs to the
live Build surface.

## 3. Why these surfaces and not others?

The legacy route (`richModel: true` + `modelMetadata` →
`BuilderCanvas` → `RollingRegressionRenderer`) hand-rolled its results
body with no shared grammar — the consolidation's Fragment B.  The
migrated Extended composes ONLY grammar components, so this tool reads
exactly like PCA / rolling regression / half-life (P3).  The compact
KPI triple is the desk triage set; α and R² live behind the expand —
α is a fit artifact, not a trading read.  The sparkline is the
residual-Z (not the raw spread): the z IS the signal the desk acts on,
and the raw level without the hedge adjustment would misread.

## 4. What would change the design?

- Backend exposing the residual z-score window as an Input knob →
  promote it from the methodology card to a control.
- A multi-regressor variant of this tool → the per-regressor β table
  (MatrixTable) joins the secondary zone, mirroring rolling regression.
- The slug-unification workstream persisting `current_metrics` →
  PreviewWidget gains deterministic full KPIs; revisit
  `detailUnavailable`.

## 5. Which backend doctrine does this module operationalise?

- **rendering_density.md §1–2** — both dual-view surfaces; compact has
  no controls strip and calls `onExpand`.
- **methodology_exposure.md §5** — standalone bridge at
  `/api/v1/rates/detail/beta-adjusted-spread`; both views consume it
  via the shared hook.
- **FP13 / P3** — all result rendering via the finance-blind grammar
  (`ModelResultLayout`, `ModelSeriesPanel`, `ModelKpiStrip`,
  `QualityBadge`); the finance-aware mapping lives in this module's
  Shared file.
- **P5 / FP8** — methodology rows threaded from the response's
  `*_used` echo fields + `spread_label`; the cheap/rich sign
  convention (part of the schema contract) is stated verbatim on both
  views.
- **FP9** — backend numbers render unchanged; the only client-side
  work is display mapping.
- **FM1 / FM3 / FM7 / FM8 / FM9** — folder = tool_name; tier claims
  match shipped files; pure-spec module; `typedView: null`,
  `richModel: false` (the `modelMetadata` block is retired — its
  paramHints moved to `MODULE.paramHints`, its interpretation copy to
  `MODULE.interpretationCards`).
- **FM10 / FM11 / FM12** — this file; `__tests__/module.spec.ts`
  (pilot-pattern dual-view spec); alphabetical loader entry.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Consolidation G-3.2 — dual-view migration onto the shared rich-model grammar; modelMetadata retired (paramHints + interpretationCards moved onto the spec); persisted preview unchanged. |
| v2 | 2026-05-26 | Stage 4b — rich-model module (BuilderCanvas + modelMetadata + RichModelWidget preview). |
| v1 | 2026-05-25 | Stage 3 scaffold. |
