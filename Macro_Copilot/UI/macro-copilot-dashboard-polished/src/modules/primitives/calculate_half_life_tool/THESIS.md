# THESIS — `calculate_half_life_tool`

> Rich-model module MIGRATED to the dual-view standard (consolidation target #4).  Ships an extended Build view (full canvas composing the rich-model grammar at `@/components/shared/build/model`) AND a compact Build view (snapshot-shaped grid card) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — while RETAINING the persisted-artifact preview path untouched.

**Version:** v3 (dual-view rich-model migration)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_half_life_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/half-life`)
**Tier set:** `[generic_runnable, custom_build_surface, custom_preview_widget]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `model_fits`
**Mockups:** none — this migration composes the established rich-model grammar components (ModelResultLayout / ModelKpiStrip / MatrixTable / DecompositionBars / QualityBadge); the grammar IS the design reference.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries AND by the click-to-expand modal from the compact card.  Composes the rich-model grammar inside its own canvas: hero `ModelKpiStrip` (half-life with delta-method CI subtext · mean-reverting ✓/✗ · current deviation in native units · long-run mean) headed by a `QualityBadge` (ok = mean-reverting, degraded = not); primary **deviation read** via `DecompositionBars` (mode `signed` — the one signed quantity the snapshot owns: current − long-run mean, unit driven by the wire's `series_units`); secondary β block via one-row `MatrixTable` (β, CI lower, CI upper); diagnostics strip (R², observation count, confidence-level echo, lookback echo); shared `MethodologyCard` threaded from the response's echo fields + the backend's own AR(1)/OU wording; shared `ControlsStrip` (mode toggle single/pair, curve_family + tenor, curve_family_2 in pair mode, lookback, advanced field_name) and `LineageFooter`.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"how mean-reverting is the UST–Bund 10Y spread vs the BTP–Bund 10Y spread?"*).  Identity `{series_label} HALF-LIFE`, 3 KPIs (HALF-LIFE days / DEVIATION native / MEAN-REVERTING ✓✗), a half-life **CI band** where a sparkline would sit, the `AR(1) fit; CI at {confidence_level_used}` caveat footer, expand affordance.  **No sparkline — deliberately** (Q3).
- **`custom_preview_widget`** — **UNTOUCHED by this migration**: per-tool persisted-artifact card at [`surfaces/PreviewWidget.tsx`](surfaces/PreviewWidget.tsx), a thin caller of the shared `RichModelWidget`; per-tool copy from `MODULE.modelAdapter` (the honest "snapshot view not persistable today" callout with the detailUnavailable list).

Both Build views fetch the SAME typed-detail endpoint (`/api/v1/rates/detail/half-life` via `useHalfLifeData` in [`surfaces/halfLifeShared.ts`](surfaces/halfLifeShared.ts)); the compact view just renders less (rendering_density.md §2.2).

---

## 2. What does the user read off each surface?

### Extended Build view

The full OU-fit investigation canvas for ONE series.  A PM reads, in order: (a) the title row — the wire's `series_label`, the mean-reversion `QualityBadge`, mode subtitle (single series in percent / pair spread in bps), as-of + window; (b) the hero strip — half-life in trading days (emphasis, with the delta-method CI as subtext; honest '—' + the backend's three-cause undefined note when there is no half-life), mean-reverting ✓/✗, current deviation (sign-toned, native units), long-run mean; (c) the deviation read — a signed bar of current − long-run mean with a description that states what the OU fit expects (*"…to halve every N trading days"*) or honestly says there is no mean-reversion read; (d) the β block — β with its CI in the shared diverging-shaded matrix; (e) fit diagnostics — R², observations after dropna, the YAML-locked confidence-level echo, the lookback echo; (f) the methodology card — the discretized AR(1) form `Δx_t = α + β · x_{t−1} + ε_t`, the half-life formula + stability floor, the structural-test honesty (no ADF/KPSS), the OLS-SE caveat, the `series_units` unit contract, and the pasted-series-not-bridged note; (g) the lineage footer.

### Compact Build view

The at-a-glance DAG card.  THREE numbers + one band: half-life (days), current deviation (native units, sign-toned), mean-reverting ✓/✗; below them the half-life CI band (`[lo – hi]d` at the echoed confidence level with the point estimate marked) — or an honest one-liner when the half-life/CI is undefined.  Footer carries the `AR(1) fit; CI at {confidence_level_used}` caveat + as-of.  The expand arrow opens the extended view.

### Preview widget (persisted artifact)

Unchanged: pure-snapshot tool → renders the honest "snapshot view not persistable today" callout via `MODULE.modelAdapter`.

---

## 3. Why these surfaces and not others?

**Why `modelMetadata` is REMOVED (and `richModel: false`).**  The legacy rich-model chassis routed Build to the shared `BuilderCanvas` / `ModelWorkspacePage` via `contextDecoder` → `modelRegistry`, whose `MODELS` array derives from each module's `modelMetadata`.  Under the dual-view standard the module owns its Build surfaces directly; leaving `modelMetadata` in place would re-derive a registry entry and route single-tool queries BACK to the legacy chassis instead of `surfaces.buildExtended`.  Removal + `richModel: false` is therefore the load-bearing routing edit, pinned by [`__tests__/module.spec.ts`](__tests__/module.spec.ts).  The old `BuildSurface.tsx` (a lazy `BuilderCanvas` wrapper) is deleted; `surfaces.build` now aliases `BuildExtended` for the legacy `VirtualPrimitiveCanvas` dispatcher (transitional, per the module-spec field docs).  Half-life previously had NO custom renderer on the legacy chassis (AutoRenderer), so the grammar-composed extended view is a strict upgrade.

**Why the persisted path is RETAINED.**  `modelAdapter` + `surfaces.preview` + the `custom_preview_widget` tier serve the persisted-artifact `RichModelWidget` flow — orthogonal to Build routing.  The tool is a pure snapshot (no `time_series` on the wire), so the preview's honest "not persistable today" copy is still exactly right; the migration does not touch it.

**Why a no-sparkline compact (3 KPIs + CI band).**  The shared `BuildCompactShell` is LEVEL-shape oriented (3 KPI cells + sparkline + footer).  Half-life has NO time series on the wire — the backend pins this (`test_no_time_series_output`: the input series IS the time-series content; a rolling half-life would be a sibling tool).  Rendering an empty chart would read as missing data rather than snapshot-by-contract.  Following the scan_extremes_tool density-deviation precedent (which justified a table-shaped compact for SCANNER-shaped data), this module honours the SEMANTIC contract of rendering_density.md §2.2 — identity / headline data / methodology caveat / expand affordance / tone cues — with a snapshot-shaped layout: the CI band occupies the sparkline slot because the half-life's uncertainty interval IS this tool's most chart-like honest read.  Shared `FreshnessPill` + the shared tone vocabulary keep the visual treatment aligned with the compact catalogue.

**Why these three compact KPIs** (half-life + deviation + mean-reverting): they answer the PM's canonical questions in order — *"how fast does it revert / how far is it from fair now / is it even mean-reverting?"*.  Alternatives considered + rejected: β (an input to the half-life, not the desk read — extended secondary); R² (fit diagnostics, not headline); long-run mean (implied by deviation + current value; extended hero carries it).

**Why a deviation read as the extended primary** (DecompositionBars, mode `signed`).  No time series exists on the wire, so the primary zone is a snapshot visual: the single signed quantity the model owns (current − long-run mean) rendered through the shared signed-bars grammar — mint/coral by sign, with the not-mean-reverting case quality-flagged `degraded` on the entry.  A hand-rolled gauge/chart was rejected (grammar-only rule, FP13); a second KPI strip was rejected as redundant with the hero.

**Why the mode toggle exposes single + pair but NOT pasted_series.**  The GET bridge flattens the tool's one-of-three input union as: series mode (`curve_family` + `tenor`) and pair mode (add `curve_family_2` → spread (cf1 − cf2) at tenor, bps).  The third variant (`pasted_series` — caller-supplied rows for tool chaining) is intentionally NOT bridged over GET: rows don't fit query params.  This builder is honest about it — the methodology card states the mode exists and lives on MCP / the orchestrator's generic run endpoint.

**Why units are read from `series_units`** (never inferred): the backend schema is explicit that consumers of the `*_native` scalars MUST read the closed-enum `series_units` (percent for series mode, bps for pair mode).  `unitLabelForSeriesUnits` in `halfLifeShared.ts` is the single mapping; unknown enum values render raw rather than hidden (P6).

**Why a typed-detail endpoint** (`/api/v1/rates/detail/half-life`): per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every standalone module ships its own typed bridge; the generic `/run` route stays the LLM-facing surface.  All other methodology knobs (confidence level, CI method, rounding, stability floor) are YAML-locked and not exposed at the route (A13).

---

## 4. What would change the design?

- **A rolling-half-life sibling tool** lands (backend explicitly reserves this as a sibling, not a parameter) → that module ships its own dual-view with a real time-series chart; this one stays snapshot-shaped.
- **Backend persisting half-life Series snapshots** → `surfaces/PreviewWidget.tsx` becomes a real persisted-Series card; `modelAdapter.persistedRole` copy updates.
- **A stationarity-test sibling (ADF/KPSS / cointegration_test)** lands → the mean-reverting `QualityBadge` could link the structural-test caveat to the statistical sibling; today the methodology card carries the honesty note.
- **The pasted_series path gets a POST bridge** → add a paste affordance to the extended controls; until then the methodology note stands.
- **`VirtualPrimitiveCanvas` drops the legacy `build` key** → delete the transitional `build: BuildExtended` alias from `module.ts.surfaces`.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `custom_preview_widget`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; `custom_preview_widget` retention is the pre-existing persisted-artifact claim, unchanged.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` unchanged from the backend ToolCard-derived copy; `defaultParams` mirror the GET bridge params.
- **FM5d** (persisted-artifact ModelAdapter) — `MODULE.modelAdapter` kept untouched.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` + `surfaces.preview` populated; files at canonical paths; legacy `BuildSurface.tsx` deleted (dual-view modules alias `build` → BuildExtended, no separate file required).
- **FM9** (routing-claim disclosure) — `typedView: null`; `richModel: false`; `modelMetadata` removed (legacy BuilderCanvas routing off).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract + the rich-model demotion contract + persisted-path retention.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **FP9** (wire honesty) — every rendered number is the backend's, unchanged; display formatting only.
- **FP13** (finance-blind shared layer) — all half-life knowledge lives in `surfaces/halfLifeShared.ts`; the grammar components are composed, never edited.
- **P5** (honest disclosure) — methodology threaded from the response's echo fields (`confidence_level_used`, `series_units`) and mirrors the backend schema/config wording (AR(1) form, structural-test-only, delta-method CI caveat, pasted-series-not-bridged).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/half-life`; own service helper `fetchDetailHalfLife`; own frontend type `HalfLifeOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.

---

## One-line summary

Ornstein-Uhlenbeck / AR(1) fit on a supplied series.  Returns half-life of mean reversion (trading days), long-run mean, current deviation, OU β with confidence interval, and a delta-method CI on the half-life itself.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Dual-view rich-model migration.  Removed `modelMetadata` + set `richModel: false` (legacy BuilderCanvas routing off); shipped `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` + `surfaces/halfLifeShared.ts` composing the rich-model grammar; deleted the legacy `surfaces/BuildSurface.tsx` BuilderCanvas wrapper; KEPT `modelAdapter` + `surfaces/PreviewWidget.tsx` + `custom_preview_widget` untouched.  Standalone bridge at `/api/v1/rates/detail/half-life` (pair mode via `curve_family_2`; `pasted_series` intentionally not bridged). |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
