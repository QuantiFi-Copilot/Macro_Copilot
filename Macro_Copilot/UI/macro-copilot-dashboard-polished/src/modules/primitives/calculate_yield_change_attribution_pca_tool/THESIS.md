# THESIS — `calculate_yield_change_attribution_pca_tool`

> Rich-model module migrated to the dual-view rendering-density standard (consolidation target #4).  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — composed from the shared rich-model grammar at `@/components/shared/build/model`, plus the unchanged persisted-artifact preview.

**Version:** v3 (dual-view migration off the legacy BuilderCanvas)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_yield_change_attribution_pca_tool` (generic_runnable; standalone bridge via `GET /api/v1/rates/detail/yield-change-attribution`)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `pca_analytics`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — BOTH dual-view Build surfaces:
  - **`surfaces/BuildExtended.tsx`** — full canvas: controls strip
    (curve family, target tenor, window start/end, inline-fit knobs) +
    `ModelResultLayout` (hero KPI strip → signed `DecompositionBars`
    of per-component bps contributions with the residual row →
    loading-at-target `MatrixTable` → loadings-provenance diagnostics →
    methodology card threaded from the response's `loadings_*` echo
    fields, stating the pasted-loadings non-bridging honestly).
  - **`surfaces/BuildCompact.tsx`** — grid card: 3 desk KPIs
    (TOTAL Δ · TOP FACTOR · RESIDUAL) + compact signed contribution
    rows (the per-component bps split) + caveat + `onExpand`.
- **`custom_preview_widget`** — `surfaces/PreviewWidget.tsx`
  (UNCHANGED): the persisted-artifact card via `RichModelWidget` +
  `MODULE.modelAdapter` (`previewArtifactTypes` keeps the extra Panel
  registration).
- Both Build views fetch the SAME typed-detail endpoint through
  `surfaces/yieldChangeAttributionShared.ts` (single data hook, KPI
  builders, caveat strings — P10).

## 2. What does the user read off each surface?

**Extended.** "WHY did the 10Y move 25bps over this window?"  The hero
strip leads with the total change in bps, the dominant factor (by
|contribution|, a display-only sort), the residual, and the resolved
window.  The primary visual is the signed decomposition — each
component's bps contribution around a center axis with the
de-emphasised residual row; Σ contributions + residual = total Δ by
construction.  The secondary matrix shows each component's loading at
the target tenor (the lever arm).  Diagnostics carry the loadings
provenance (source, fit window, overlap %, observations) so the PM can
judge whether the basis was fit on representative history.

**Compact.** The triage read: how big the move was, which factor owned
it, and how much the basis failed to explain — as compact signed rows
(a categorical factor axis; a sparkline would misrepresent it).

**Preview (persisted).** The saved artifact via the generic persisted
renderer with the adapter's honest detail-unavailable list.

## 3. Why these surfaces and not others?

The legacy route hand-rolled `AttributionRenderer` (374 lines of
bespoke bars/tables) — Fragment B of the consolidation audit.  The
migrated Extended composes ONLY grammar components, so attribution
reads exactly like PCA / regression / half-life (P3).  The compact uses
the sanctioned table/bars-shaped guardrail (like the scanner compacts):
the output is a PURE SNAPSHOT decomposition — no time series exists on
the wire, so no sparkline is honest.  `pasted_loadings` is not offered
on these surfaces: a loadings matrix doesn't fit GET query params; that
orchestrator-paste mode stays on the generic run endpoint / MCP, and
the methodology zone says so (P5).

## 4. What would change the design?

- The typed-detail bridge gaining a POST variant for pasted loadings →
  a paste-mode control panel becomes honest; add it then.
- Backend emitting a contribution time series (per-day attribution) →
  a `ModelSeriesPanel` joins the secondary zone.
- The slug-unification workstream persisting `current_metrics` →
  PreviewWidget gains deterministic snapshot KPIs.

## 5. Which backend doctrine does this module operationalise?

- **rendering_density.md §1–2** — both dual-view surfaces; compact has
  no controls strip and calls `onExpand`; the snapshot-shaped compact
  follows the same semantic-contract guardrail the scanner compacts
  documented.
- **methodology_exposure.md §5** — standalone bridge at
  `/api/v1/rates/detail/yield-change-attribution`; both views consume
  it via the shared hook.
- **FP13 / P3** — all result rendering via the finance-blind grammar
  (`ModelResultLayout`, `DecompositionBars`, `MatrixTable`,
  `ModelKpiStrip`, `QualityBadge`); the finance-aware mapping lives in
  this module's Shared file.
- **P5 / FP8** — methodology rows threaded from the response's
  `loadings_*` echo fields; window resolution (requested vs resolved)
  and the pasted-loadings non-bridging stated verbatim.
- **FP9** — backend numbers render unchanged; "top factor" is a
  display-only |bps| sort, never a recomputation.
- **FM1 / FM3 / FM7 / FM8 / FM9** — folder = tool_name; tier claims
  match shipped files; pure-spec module; `typedView: null`,
  `richModel: false` (the `modelMetadata` block is retired — its
  paramHints moved to `MODULE.paramHints`, its interpretation copy to
  `MODULE.interpretationCards` via the Shared file's
  `ATTRIBUTION_INTERPRETATION_CARDS`).
- **FM10 / FM11 / FM12** — this file; `__tests__/module.spec.ts`
  (pilot-pattern dual-view spec); alphabetical loader entry.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Consolidation G-3.2 — dual-view migration onto the shared rich-model grammar; modelMetadata retired (paramHints + interpretationCards moved onto the spec); persisted preview unchanged. |
| v2 | 2026-05-26 | Stage 4b — rich-model module (BuilderCanvas + modelMetadata + RichModelWidget preview). |
| v1 | 2026-05-25 | Stage 3 scaffold. |
