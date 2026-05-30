# THESIS — `scan_inflation_swaps_extremes_tool`

> First SCANNER-shape primitive shipped under the dual-view + standalone-bridge contract.  The wire returns a ranked LIST of (curve_family, tenor) extremes, NOT a single time series — so this module's compact view is a top-N TABLE and its extended view is a universe-scan canvas (distribution histogram + ranked detail table), distinct from the level/spread/butterfly module shapes already in the catalogue.

**Version:** v3 (initial SCANNER build — replaces the Stage 4f runtime-only scaffold)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `scan_inflation_swaps_extremes_tool` (generic_runnable)
**Bridge endpoint:** `/api/v1/rates/detail/zcis-scanner`
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `scanners`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; falls back to `GenericPrimitiveBuilder` if the user reaches it via a non-canonical Library "Open in Build" path that bypasses the typed-detail endpoint.
- **`custom_build_surface` → extended** (`surfaces/BuildExtended.tsx`).  Universe-scan canvas: identity row + scan-summary top-right cards (flagged-count, distribution, index-family caveat) + controls strip (scope / threshold / top-N) + z-score distribution histogram + scan-summary callouts + ranked detail table with per-row "Open in Build" deep-link to the per-pillar `calculate_inflation_swap_rate_level_tool` + methodology card sourced from the wire's `methodology_disclosure` + lineage footer.
- **`custom_build_surface` → compact** (`surfaces/BuildCompact.tsx`).  Grid card with the top-4 ranked rows in a (rank / instrument / current rate / z-score) table — NOT a sparkline.  Tone-cued z-scores (|z| ≥ 1.5 amber, |z| ≥ 2.0 coral/mint by direction).  "MOST EXTREME" highlight row below the table.  Footer caveat + "View all N" expand affordance.
- **`monitor_surface` → `ZcisScannerWidget`** (`surfaces/monitor/ZcisScannerWidget.tsx`).  Catalog tile parameterised on (scope, top_n, min_abs_z_score).  Surfaces the flagged-count chip + the top-3 ranked rows + the load-bearing CPI-family caveat.

## 2. What does the user read off each surface?

**Extended (single-tool query).** A PM opens the canvas with one question — *"where is the ZCIS curve stretched today, across the whole universe?"*.  The scan-summary card answers it in one number ("7 of 21 stems flagged at |z| ≥ 1.5σ").  The distribution histogram shows whether the flagged extremes lean rich (low) or cheap (high), and how many sit beyond the 2σ envelope.  The ranked table is the action layer — every flagged pillar is a candidate for a deeper read; clicking "Open in Build" on a row routes to the per-pillar `calculate_inflation_swap_rate_level_tool` with the (curve, tenor) bound.  The methodology card surfaces the wire's full disclosure verbatim — the 252d window, the index-family heterogeneity caveat (CPI-U / HICPxT / RPI), the index-lag + interpolation conventions, and the morning-screen scope statement.

**Compact (multi-tool DAG node).** A PM running a multi-tool query like *"compare ZCIS extremes vs sovereign extremes vs linker extremes"* sees three scanner cards side-by-side.  The ZCIS card's three things the PM reads in 2 seconds: (1) WHICH instrument tops the ranking ("USD 5Y CPI-U"), (2) at WHAT |z| ("+2.7σ" — coral, so cheap), (3) how many other stems are flagged ("7 Flagged at |z| ≥ 2.0σ").  The expand affordance opens the full extended canvas in a modal with the multi-tool DAG behind it; the "View all N" footer link is a secondary entry to the same modal.

**Monitor.** Inherently compact (bento-grid constraint).  Surfaces the flagged-count headline + the top-3 ranked rows; the threshold chip tells the PM at a glance whether anything is at 2σ extremity right now.  The catalog form lets the desk pin one tile per scope (e.g. one All-ZCIS tile, one USD-only tile) without per-tile JSX changes.

## 3. Why these surfaces and not others?

**Why a custom compact view (top-N table) rather than `AutoRenderer` or the standard `BuildCompactShell`?**
The standard `BuildCompactShell` is LEVEL-shape oriented — three KPI cells + sparkline + footer.  The scanner's headline data is a ranked LIST of extremes, not a single number with a chart.  A literal 3-KPI mapping would surface only ONE row (rank 1) and would drop the comparative context (the rest of the top-N + the flagged count) that IS the scanner's point.  The catalog guardrail explicitly calls this out: "SCANNER SHAPE — compact view is a top-N ranked table, NOT a sparkline."  This module honours the SEMANTIC contract of rendering_density.md §2.2 (identity / headline data / methodology / expand / tone cues) with a scanner-shaped layout, reusing the shared `FreshnessPill` + `toneTextClass` + format helpers so visual treatment matches the rest of the compact catalogue.

**Why a custom extended view rather than `BuildExtendedShell`?**
The extended shell is also chart-centric (chartPoints + reference bands + KPI strip).  The scanner needs a distribution histogram + a ranked-detail table.  The shared shell's `ControlsStrip` + `MethodologyCard` + `LineageFooter` ARE finance-blind enough to drop in directly; this module composes them inside a scanner-shaped layout rather than fight the chart-centric main-canvas.

**Why claim `monitor_surface` (vs FM4 parsimony)?**
The desk's morning ritual is the universe-wide ZCIS sweep — this is exactly the use case FM4's exception #2 enumerates ("the desk reads this primitive at a glance every day").  Without the Monitor tile, a PM has to open the full Build canvas every morning; with it, the bento grid renders the flagged count + top-3 rows alongside the sibling rates / linker / OIS scanners.

**Why a single-metric scan and not per-tenor sub-scans?**
The wire is single-metric (|z| of the rate LEVEL) per the catalog's literal wording.  Multi-metric ranking is reserved for the `scan_bond_futures_extremes` shape (whose catalog enumerated four metrics).  The frontend mirrors the wire — no synthetic multi-metric overlay.

**Why not `ask_surface`?**
The chat dispatcher's generic `AssistantResearchCard` handles the "give me the ZCIS extremes" Ask response perfectly today — the response shape (top-N rows) reads naturally as a chat bubble.  A bespoke Ask card would be incremental at best; deferred per FM4.

**Why not `custom_preview_widget`?**
Persisted-artifact preview cards (the per-tool node renderer in the workspace DAG) for scanners are well-served by the artifact-type generic registry; the scanner doesn't carry a domain-specific preview need beyond what the workspace's node renderer already does.

## 4. What would change the design?

- **Multi-metric scan adoption.** If the catalog ever expands `required_metrics` from `[yield_mid]` to multiple metrics (matching the `scan_bond_futures_extremes` shape), the wire would gain per-metric rankings and the extended view would need a metric-switcher chip.  Today the schema is locked to single-metric.
- **Wire-level pre-bucketed distribution.** Today the histogram is synthesised on the FRONTEND from the returned top-N (labelled "of the flagged extremes" so the desk reader doesn't mistake it for the full-universe distribution).  If compute() ever ships a `universe_distribution_bins` field on the response, the histogram would become the full-universe distribution and the surface label updates accordingly.
- **Per-row sparkline.** A future enhancement (deferred) is adding a 60d sparkline of each ranked stem's ZCIS rate to the extended view's table — would require a second wire field (or N second-tier fetches) and is non-blocking today.
- **Workflow-template surface.** When the universe-scan flow is wrapped in a workflow template (e.g. "morning sweep → deep-dive top-3 → assemble research note"), the workflow's results dashboard would mount this module's compact view in its DAG node body per rendering_density.md §4's provisional rule — no module-level change required.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly (note the documented MCP-wrapper alias `get_scan_inflation_swaps_extremes_tool`; the frontend uses the canonical `scan_inflation_swaps_extremes_tool` everywhere).
- **FM3** (surface-tier capability declaration) — claims `[generic_runnable, custom_build_surface, monitor_surface]`; both Build files exist; the Monitor catalog entry references the single-component shape.
- **FM4** (tier parsimony) — Monitor claim justified per Exception #2; dual-view mandate per the override in §1.2 of rendering_density.md.
- **FM5** (display-metadata sourcing) — `oneLineSummary` paraphrases the backend's tool description; `defaultParams` map onto the Pydantic Input.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value with no side effects.
- **FM8** (surface-file contract) — `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and `surfaces/monitor/ZcisScannerWidget.tsx` are present and referenced exactly once each.
- **FM9** (routing-claim disclosure) — `typedView: null` + `richModel: false` per the standalone-bridge contract.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.
- **rendering_density.md §1 / §2.2** (dual-view mandate) — extended + compact both populated; compact is a top-N TABLE per the SCANNER-shape guardrail; methodology is reachable via the footer caveat (full wire disclosure surfaces on hover via `title=`).
- **methodology_exposure.md §5** (standalone-bridge contract) — own `/api/v1/rates/detail/zcis-scanner` endpoint + own `fetchDetailZcisScanner` helper + own `ScanInflationSwapsExtremesOutput` TS type mirror; no shared `typedView` reuse.
- **PR10 / P5** (wire-honesty disclosure) — `methodology_disclosure` is consumed VERBATIM from `data.methodology_disclosure` on both the extended methodology card and the compact footer tooltip; the per-tool `ZCIS_SCANNER_COMPACT_CAVEAT` constant is the desk-canonical SHORT rendering of that wire disclosure (mirrors the inflation_swap_butterfly compact-caveat pattern).

---

## One-line summary

Universe-wide ZCIS rate-level sweep — ranks every (USD_ZCIS / EUR_ZCIS / GBP_ZCIS, tenor) pillar by absolute 252-day rolling z-score; top-N table compact view, distribution + ranked detail extended view.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-29 | Initial SCANNER build — dual-view + standalone-bridge + monitor. Top-N table compact + universe-scan extended + Monitor tile. First scanner-shape module under the dual-view contract. |
| v2 | 2026-05-26 | Stage 4f scaffold rewrite — runtime-only tier, no surfaces. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
