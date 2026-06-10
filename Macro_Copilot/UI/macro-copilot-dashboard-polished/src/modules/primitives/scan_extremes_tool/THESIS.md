# THESIS — `scan_extremes_tool`

> Migration-mode rewrite: legacy `manifest_typed_view` typed-renderer
> (`typedView: 'scanner'` + `surfaces/ResultRenderer.tsx`) → dual-view +
> standalone-bridge.  Preserves the `manifest_typed_view` runtime tier and
> the existing `ScannerWidget` monitor surface verbatim; rebuilds the Build
> surface as the per-tool `BuildExtended` + `BuildCompact` pair fed by a
> NEW typed-detail endpoint at `/api/v1/rates/detail/scanner`.

**Version:** v3 (migration commit — dual-view + standalone-bridge)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `scan_extremes_tool` (`manifest_typed_view`)
**Bridge endpoint:** `/api/v1/rates/detail/scanner`
**Tier set:** `[manifest_typed_view, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `screening`

---

## 1. What surfaces does this module ship?

- **`manifest_typed_view`** — runtime-status tier (PRESERVED).  Backend
  `_PRIMITIVE_SPECS` does NOT contain this tool — it lives in the
  `_MANIFEST_ONLY_BUILD_TOOLS` set.  The migration removes the
  `module.ts.typedView = 'scanner'` string (no more shared typed-view
  dispatch) but keeps the runtime tier so the workflow gate continues to
  treat this tool the same way.
- **`custom_build_surface` → extended** (`surfaces/BuildExtended.tsx`).
  Universe-scan canvas: identity row + scan-summary cards (flagged-count /
  distribution / G10 curve coverage) + controls strip (curve scope /
  threshold / top-N) + z-score distribution histogram + scan-summary
  callouts + ranked detail table with per-row "Open in Build" deep-link to
  the per-pillar `get_yield_levels_tool` + methodology card + lineage
  footer.
- **`custom_build_surface` → compact** (`surfaces/BuildCompact.tsx`).  Grid
  card with the top-5 ranked rows in a (rank / country / curve / tenor /
  yield / z-score) table — NOT a sparkline.  Tone-cued z-scores (|z| ≥ 1.5
  amber, |z| ≥ 2.0 coral/mint by direction).  Footer caveat ("Cross-
  instrument z-scores are comparable; 252d window locked.") + "View all N"
  expand affordance.
- **`monitor_surface` → `ScannerWidget`** (`surfaces/monitor/ScannerWidget.tsx`).
  PRESERVED verbatim.  Bento bento tile reading from the pre-aggregated
  `RatesDataContext` feed; surfaces a ranked instrument table with a
  threshold chip and flagged-count badge.  Widget `id: 'scanner'` and
  non-parameterised shape unchanged so dashboards that mounted this widget
  keep hydrating.

## 2. What does the user read off each surface?

**Extended (single-tool query).** A PM opens the canvas with one question —
*"where is the G10 sovereign yield curve stretched today?"*.  The scan-
summary card answers it in one number ("6 of 36 stems flagged at |z| ≥
1.5σ").  The distribution histogram shows whether the flagged extremes lean
rich (low) or cheap (high), and how many sit beyond the 2σ envelope.  The
curve-coverage card answers "is this just one country or several?" at a
glance.  The ranked table is the action layer — every flagged pillar is a
candidate for a deeper read; clicking "Open in Build" on a row routes to
the per-pillar `get_yield_levels_tool` with the (curve, tenor) bound.

**Compact (multi-tool DAG node).** A PM running a multi-tool query like
*"compare sovereign extremes vs OIS extremes vs ZCIS extremes"* sees three
scanner cards side-by-side.  The sovereign card's three things the PM
reads in 2 seconds: (1) WHICH instruments top the ranking ("Italy 10Y BTP /
Japan 30Y JGB / Spain 10Y Bono"), (2) at WHAT |z| ("+2.7σ / −2.4σ / +2.1σ"
— coral/mint/coral), (3) how many stems are flagged total ("6 Flagged at
|z| ≥ 2.0σ").  The expand affordance opens the full extended canvas in a
modal with the multi-tool DAG behind it; the "View all 6" footer link is
a secondary entry to the same modal.

**Monitor.** Inherently compact (bento-grid constraint).  Surfaces a
ranked table (rank / instrument / yield / Δ1d / z) with a top-rule amber
chrome (the canonical "anomaly" widget rule) plus a `≥ 1.5σ · top 8` chip
that names the hardcoded threshold + cap honestly.  The PM at a glance
sees the flagged-count badge plus the top instruments without parameter
editing.

## 3. Why these surfaces and not others?

**Why a custom compact view (top-N table) rather than `AutoRenderer` or
the standard `BuildCompactShell`?** The standard `BuildCompactShell` is
LEVEL-shape oriented — three KPI cells + sparkline + footer.  The
scanner's headline data is a ranked LIST of extremes, not a single number
with a chart.  A literal 3-KPI mapping would surface only ONE row (rank
1) and would drop the comparative context (the rest of the top-N + the
flagged count) that IS the scanner's point.  Per the catalog guardrail
(Option (c) shell-density precedent — Batch 1 fdac7d2): keep shell-
standard density on Compact (3 KPIs equivalent → 5 ranked rows, single
identity → SOVEREIGN YIELD EXTREMES) and document the density deviation
here.  This module honours the SEMANTIC contract of rendering_density.md
§2.2 (identity / headline data / methodology / expand / tone cues) with a
scanner-shaped layout, reusing the shared `FreshnessPill` and
`toneTextClass` helpers so visual treatment matches the rest of the
compact catalogue.

**Why a custom extended view rather than `BuildExtendedShell`?** The
extended shell is chart-centric (chartPoints + reference bands + KPI
strip).  The scanner needs a distribution histogram + a ranked-detail
table.  The shared `ControlsStrip` + `MethodologyCard` + `LineageFooter`
ARE finance-blind enough to drop in directly; this module composes them
inside a scanner-shaped layout rather than fight the chart-centric main-
canvas.

**Why PRESERVE `manifest_typed_view`?** The backend `_PRIMITIVE_SPECS`
membership is unchanged by this migration — `scan_extremes_tool` lives in
`_MANIFEST_ONLY_BUILD_TOOLS` (it's a deep universe-scan that the LLM-
facing workflow bridge cannot dispatch).  Per `MIGRATION_RULES.md §4 step
6` + §8 anti-pattern, the tier set must mirror backend reality —
preserve `manifest_typed_view`; do NOT swap to `generic_runnable`.

**Why PRESERVE `ScannerWidget` (non-parameterised, `RatesDataContext`-fed)
verbatim?** The widget `id: 'scanner'` is the backward-compat lock —
every dashboard that mounted this widget persists the id, so changing it
breaks hydration silently.  The widget's data source (the pre-aggregated
RatesPage feed) is preserved so the RatesPage continues to render exactly
as before.  A future enhancement (V2) makes the threshold + cap
configurable; per MIGRATION_RULES.md the migration commit does NOT change
runtime behaviour, only the Build surface shape.

**Why not `ask_surface`?** The chat dispatcher's generic
`AssistantResearchCard` handles the "give me the sovereign extremes" Ask
response perfectly today — the response shape (top-N rows) reads
naturally as a chat bubble.  A bespoke Ask card would be incremental at
best; deferred per FM4.

**Why not `custom_preview_widget`?** Persisted-artifact preview cards for
scanners are well-served by the artifact-type generic registry; the
scanner doesn't carry a domain-specific preview need beyond what the
workspace's node renderer already does.

## 4. What would change the design?

- **Backend `_PRIMITIVE_SPECS` adoption.** If the backend adds
  `scan_extremes_tool` to `_PRIMITIVE_SPECS`, the runtime tier promotes
  from `manifest_typed_view` to `generic_runnable` (workflow-runnable);
  the Build surfaces are unchanged.
- **Per-sub-agent scanner siblings.** OIS / inflation_swaps / linker /
  bond-futures / policy-futures sibling scanners already ship under the
  dual-view + standalone-bridge contract; if more sub-agents appear, this
  module's pattern is the template.
- **Wire-level `methodology_disclosure` field.** Today the
  Pydantic `ScannerOutput` does NOT carry a `methodology_disclosure`
  string (unlike the sibling ZCIS / linker scanners).  The Extended view's
  methodology card synthesises rows from the YAML's published conventions
  via the per-tool TS registry.  When the backend ships
  `methodology_disclosure` on the wire (mirroring the sibling scanners),
  the Extended view switches to the wire field — mirrors the swap_spread
  / OIS curve_spread precedent noted in `api/routes/rates/detail.py`.
- **Parameterised Monitor widget.** If V2 exposes the threshold + cap as
  widget params (today they are hardcoded constants), the `ScannerWidget`
  switches to per-widget `fetchDetailScanner` while keeping the `id:
  'scanner'` lock — the catalog form gains `paramFields` entries
  alongside.  Today, parameterised editing is deferred to preserve byte-
  identical dashboard hydration.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`
  exactly (`scan_extremes_tool`).
- **FM3** (surface-tier capability declaration) — claims
  `[manifest_typed_view, custom_build_surface, monitor_surface]`; both
  Build files exist; the Monitor catalog entry references the preserved
  single-component widget shape.
- **FM4** (tier parsimony) — Monitor claim justified per Exception #2
  (the desk's morning ritual is the universe sweep — daily glanceable
  read); dual-view mandate per the override in `rendering_density.md
  §1.2`.
- **FM5** (display-metadata sourcing) — `oneLineSummary` paraphrases the
  backend's tool description; the legacy default params are kept implicit
  (the backend's `ScannerInput` has positive defaults: `top_n=10`,
  `min_abs_z_score=1.5`, `field_name='YLD_YTM_MID'`).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value with
  no side effects.
- **FM8** (surface-file contract) — `surfaces/BuildExtended.tsx`,
  `surfaces/BuildCompact.tsx`, and `surfaces/monitor/ScannerWidget.tsx`
  are all present and referenced exactly once each.
  `surfaces/ResultRenderer.tsx` is DELETED.
- **FM9** (routing-claim disclosure) — `typedView: null` + `richModel:
  false` per the standalone-bridge contract.  The legacy `workspaceLabel`
  field is removed.  `unsupportedReason` is KEPT (the framework's FM6
  invariant requires it when `manifest_typed_view` is in the tier set —
  see `__test-utils.ts` invariant #8) and its `whatWorksNow` copy is
  updated to describe the new dual-view affordance; the catalog entry's
  "remove unsupportedReason" instruction conflicts with FM6 and is
  overridden by the framework contract.
- **FM10** (THESIS discipline) — this file (v3 — replaces the v2 Stage 4e
  stub).
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants` + the rendering_density.md §11 dual-
  view contract checks (both `surfaces.buildExtended` +
  `surfaces.buildCompact` populated; `typedView === null`; mockups
  present).
- **FM12** (loader presence) — module imported in `src/modules/index.ts`
  (entry already present; alphabetical position unchanged).
- **rendering_density.md §1 / §2.2** (dual-view mandate) — extended +
  compact both populated; compact is a top-N TABLE per the SCANNER-shape
  guardrail; methodology is reachable via the footer caveat + tooltip.
- **methodology_exposure.md §5** (standalone-bridge contract) — own
  `/api/v1/rates/detail/scanner` endpoint + own `fetchDetailScanner`
  helper + own `ScanExtremesOutput` TS type mirror; no shared `typedView`
  reuse.
- **MIGRATION_RULES.md §6** (monitor widget identity rule) — widget
  `id: 'scanner'` preserved verbatim; non-parameterised shape unchanged;
  component file kept at `surfaces/monitor/ScannerWidget.tsx`.

---

## One-line summary

Sovereign universe yield scanner — ranks every (curve_family, tenor)
benchmark by absolute 252d rolling z-score; top-5 table compact view,
distribution + ranked-detail extended view, preserved RatesDataContext-
fed Monitor tile.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-10 | Migration-mode rewrite — dual-view + standalone-bridge.  Legacy `typedView: 'scanner'` + `surfaces/ResultRenderer.tsx` removed; new `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` + `surfaces/scanExtremesShared.ts` added; `ScannerWidget` preserved verbatim; new `/api/v1/rates/detail/scanner` typed-detail endpoint + `fetchDetailScanner` helper + `ScanExtremesOutput` TS mirror added to central files (distinct from legacy `ScannerResponse` aggregated-dashboard shape). |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
