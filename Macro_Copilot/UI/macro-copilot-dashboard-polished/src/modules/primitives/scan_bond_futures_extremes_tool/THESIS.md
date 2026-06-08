# THESIS — `scan_bond_futures_extremes_tool`

> Universe-wide bond-futures extremes scanner under the dual-view + standalone-bridge contract.  SCANNER shape (MULTI-METRIC) — the wire returns a ranked LIST of (curve_family, contract_code) extremes across FOUR metrics (price LEVEL, 1-day price CHANGE, volume LEVEL, open-interest LEVEL), each independently ranked by |z| of the 252d-rolling z-score on that metric.  Not a single time series — so the compact view is a top-N TABLE with a SCOPE chip per row (PRICE / Δ / VOL / OI), the extended view is a universe-scan canvas (distribution histogram + multi-metric ranked detail), and the Monitor tile surfaces the cross-metric top-3.

**Version:** v3 (initial SCANNER build — replaces the Stage 4f runtime-only scaffold)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `scan_bond_futures_extremes_tool` (generic_runnable; bond_futures domain per ADR 0013)
**Bridge endpoint:** `/api/v1/rates/detail/bond-futures-scanner`
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `scanners`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; falls back to `GenericPrimitiveBuilder` if the user reaches it via a non-canonical Library "Open in Build" path that bypasses the typed-detail endpoint.
- **`custom_build_surface` → extended** (`surfaces/BuildExtended.tsx`).  Universe-scan canvas: identity row + scan-summary top-right cards (flagged-count, distribution across all metrics, per-metric flagged breakdown + V1-monitors-only caveat) + controls strip (country scope / threshold / top-N per metric) + z-score distribution histogram across all four metrics + scan-summary callouts + ranked multi-metric detail table with per-row SCOPE chip and "Open in Build" deep-link to the per-stem `futures_price_level_tool` + methodology card sourced from the wire's `methodology_disclosure` + lineage footer.
- **`custom_build_surface` → compact** (`surfaces/BuildCompact.tsx`).  Grid card with the top-4 cross-metric ranked rows in a (# / contract / scope / value (native) / z-score) table — NOT a sparkline.  Each row carries a SCOPE chip (PRICE / Δ / VOL / OI), tone-cued z-scores (|z| ≥ 1.5 amber, |z| ≥ 2.0 coral/mint by direction), and a narrative qualifier ("Bond rally" / "Bond selloff" / "OI build" / "OI unwind" / etc.) drawn from the metric × signal pair.  "MOST EXTREME" highlight row below the table; footer caveat + "View all N" expand affordance.
- **`monitor_surface` → `BondFuturesScannerWidget`** (`surfaces/monitor/BondFuturesScannerWidget.tsx`).  Catalog tile parameterised on (scope, top_n, min_abs_z_score).  Surfaces the flagged-count chip + the top-3 cross-metric ranked rows + the load-bearing V1-monitors-only / CTD-out-of-scope caveat.

## 2. What does the user read off each surface?

**Extended (single-tool query).** A PM opens the canvas with one question — *"where is the bond-futures universe stretched today?"*.  The scan-summary card answers it in one number ("7 of 17 flagged at |z| ≥ 1.5σ across all four metrics").  The distribution histogram shows whether the flagged extremes lean rich (low z) or cheap (high z), and how many sit beyond the 2σ envelope.  The per-metric breakdown card on the top-right (Metric · count) tells the PM at a glance whether the extremity concentrates in PRICE (the headline question), Δ (today's move), VOLUME (activity surge), or OPEN INTEREST (positioning shift).  The ranked multi-metric table is the action layer — every flagged row is tagged with a SCOPE chip so the PM can scan by metric without sorting; clicking "Open in Build" on any row routes to the per-stem `futures_price_level_tool` with (curve_family, contract_code) bound.  The methodology card surfaces the wire's full disclosure verbatim — the 252d window, the rolling-generic-price caveat, the V1-monitors-only / CTD-out-of-scope ADR 0013 statement.

**Compact (multi-tool DAG node).** A PM running a multi-tool query like *"compare bond-futures extremes vs OIS extremes vs ZCIS extremes"* sees three scanner cards side-by-side.  The bond-futures card's three things the PM reads in 2 seconds: (1) WHICH stem tops the cross-metric ranking ("TY1"), (2) on WHAT metric and at WHAT |z| ("PRICE +2.7σ — Bond rally"), (3) how many other stems are flagged ("7 Flagged · P+Δ+V+O").  The expand affordance opens the full extended canvas in a modal with the multi-tool DAG behind it; the "View all N" footer link is a secondary entry to the same modal.

**Monitor.** Inherently compact (bento-grid constraint).  Surfaces the flagged-count headline + the top-3 CROSS-METRIC ranked rows (sorted by |z| across all four metrics); the threshold chip tells the PM at a glance whether anything is at 2σ extremity right now.  The catalog form lets the desk pin one tile per scope (e.g. one All-Universe tile, one UST-only tile) without per-tile JSX changes.

## 3. Why these surfaces and not others?

**Why a custom compact view (top-N table with SCOPE chip) rather than `AutoRenderer` or the standard `BuildCompactShell`?**
The standard `BuildCompactShell` is LEVEL-shape oriented — three KPI cells + sparkline + footer.  The scanner's headline data is a MULTI-METRIC ranked LIST of extremes, not a single number with a chart.  A literal 3-KPI mapping would surface only ONE row (rank 1 on ONE metric) and would drop the comparative context (the rest of the cross-metric top-N + the flagged count + the per-metric breakdown) that IS the scanner's point.  The catalog guardrail explicitly calls this out: this primitive's V1 wire is multi-metric ranked-list — the compact view honours the SEMANTIC contract of rendering_density.md §2.2 (identity / headline data / methodology / expand / tone cues) with a scanner-shaped layout, reusing the shared `FreshnessPill` + `toneTextClass` + format helpers so visual treatment matches the rest of the compact catalogue.  Mirrors the `scan_inflation_swaps_extremes_tool` / `scan_inflation_linkers_extremes_tool` sibling precedents.

**Why a custom extended view rather than `BuildExtendedShell`?**
The extended shell is also chart-centric (chartPoints + reference bands + KPI strip).  The scanner needs a distribution histogram + a ranked-detail table whose ROWS carry the per-metric SCOPE chip + narrative qualifier.  The shared shell's `ControlsStrip` + `MethodologyCard` + `LineageFooter` ARE finance-blind enough to drop in directly; this module composes them inside a scanner-shaped layout rather than fight the chart-centric main-canvas.

**Why claim `monitor_surface` (vs FM4 parsimony)?**
The desk's morning ritual is the universe-wide bond-futures sweep — this is exactly the use case FM4's exception #2 enumerates ("the desk reads this primitive at a glance every day").  Without the Monitor tile, a PM has to open the full Build canvas every morning; with it, the bento grid renders the flagged count + top-3 rows alongside the sibling ZCIS / linker / sovereign scanners.

**Why a MULTI-METRIC scan and not a single-metric one?**
The backend wire is multi-metric per ADR 0013 — four canonical metrics (price LEVEL, 1-day price CHANGE, volume LEVEL, open-interest LEVEL) — and per the catalog's V1 wording "the four metrics ARE the concept; exposing them as a per-query input would be input-schema overreach".  The frontend mirrors the wire — the SCOPE chip per row + the per-metric breakdown card are the compact rendering of "four metrics combined into one universe sweep".

**Why CONTRACT_CODE (TY1 / UXY1) as the canonical row identifier rather than (curve_family, tenor)?**
The backend's TD#11 note documents that (curve_family, tenor) alone is ambiguous on this universe — TY1 vs UXY1 (both UST_FUT 10Y), US1 vs WN1 (both UST_FUT 30Y).  The wire row carries `contract_code` as the disambiguator; this module's helpers + table layout key on it.

**Why not `ask_surface`?**
The chat dispatcher's generic `AssistantResearchCard` handles the "give me the bond-futures extremes" Ask response perfectly today — the response shape (top-N rows) reads naturally as a chat bubble.  A bespoke Ask card would be incremental at best; deferred per FM4.

**Why not `custom_preview_widget`?**
Persisted-artifact preview cards (the per-tool node renderer in the workspace DAG) for scanners are well-served by the artifact-type generic registry; the scanner doesn't carry a domain-specific preview need beyond what the workspace's node renderer already does.

## 4. What would change the design?

- **CTD analytics adoption (ADR 0013 Phase-4).** Once D-repo + D-deliverable data ingestion lands, the wire would extend to include CTD identification, gross / net basis, implied repo, and DV01-weighted RV.  At that point the compact view's SCOPE chip vocabulary expands (CTD / BASIS / DV01 alongside today's PRICE / Δ / VOL / OI) and the extended view's distribution panel may need a per-metric facet view.  Today these are documented-deferred per ADR 0013 V1 monitors-only; the methodology card surfaces this limit honestly.
- **Inter-commodity DV01-weighted spread scan.** The catalog's `planned_extensions` includes a Phase-4 inter-commodity DV01-weighted RV scan (TY1 vs RX1, US1 vs RX1, etc.).  When it lands, it ships as a SEPARATE tool with its own methodology card; this module remains the universe-wide single-stem sweep.
- **Wire-level pre-bucketed distribution.** Today the histogram is synthesised on the FRONTEND from the returned multi-metric rows (labelled "of the flagged extremes · all metrics").  If compute() ever ships a `universe_distribution_bins` field on the response, the histogram becomes the full-universe distribution and the surface label updates accordingly.
- **Per-row sparkline.** A future enhancement (deferred) is adding a 60d sparkline of each ranked stem's underlying-metric series to the extended view's table — would require a second wire field (or N second-tier fetches) and is non-blocking today.
- **Workflow-template surface.** When the universe-scan flow is wrapped in a workflow template (e.g. "morning sweep → deep-dive top-3 → assemble research note"), the workflow's results dashboard would mount this module's compact view in its DAG node body per rendering_density.md §4's provisional rule — no module-level change required.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `[generic_runnable, custom_build_surface, monitor_surface]`; both Build files exist; the Monitor catalog entry references the single-component shape.
- **FM4** (tier parsimony) — Monitor claim justified per Exception #2; dual-view mandate per the override in §1.2 of rendering_density.md.
- **FM5** (display-metadata sourcing) — `oneLineSummary` paraphrases the backend's tool description; `defaultParams` map onto the Pydantic Input.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value with no side effects.
- **FM8** (surface-file contract) — `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and `surfaces/monitor/BondFuturesScannerWidget.tsx` are present and referenced exactly once each.
- **FM9** (routing-claim disclosure) — `typedView: null` + `richModel: false` per the standalone-bridge contract.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.
- **rendering_density.md §1 / §2.2** (dual-view mandate) — extended + compact both populated; compact is a top-N TABLE per the SCANNER-shape guardrail; methodology is reachable via the footer caveat (full wire disclosure surfaces on hover via `title=`).
- **methodology_exposure.md §5** (standalone-bridge contract) — own `/api/v1/rates/detail/bond-futures-scanner` endpoint + own `fetchDetailBondFuturesScanner` helper + own `ScanBondFuturesExtremesOutput` TS type mirror; no shared `typedView` reuse.
- **PR10 / P5** (wire-honesty disclosure) — `methodology_disclosure` is consumed VERBATIM from `data.methodology_disclosure` on both the extended methodology card and the compact footer tooltip; the per-tool `BOND_FUTURES_SCANNER_COMPACT_CAVEAT` constant is the desk-canonical SHORT rendering of the ADR 0013 V1 monitors-only / CTD-out-of-scope limit (mirrors the ZCIS / linker scanner pattern).
- **ADR 0013** (bond_futures V1 monitors-only) — surfaced explicitly on the methodology card AND the per-metric breakdown card title ("VS V1 LIMITATIONS (ADR 0013)") so the desk reader cannot mistake the scan for a CTD-aware read.

---

## One-line summary

Universe-wide front-month bond-futures sweep — ranks every rolling-generic stem (TY1 / UXY1 / US1 / WN1 / RX1 / UB1 / JB1 / G1 / OAT1 / ...) by absolute 252-day z-score across price LEVEL, 1-day price CHANGE, volume LEVEL, and open-interest LEVEL; top-4 cross-metric table compact view, multi-metric distribution + ranked detail extended view, Monitor bento tile.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Initial SCANNER build — dual-view + standalone-bridge + monitor.  Multi-metric top-N table compact + universe-scan extended + Monitor tile.  Mirrors the `scan_inflation_swaps_extremes_tool` / `scan_inflation_linkers_extremes_tool` precedents adapted for the bond-futures four-metric wire. |
| v2 | 2026-05-26 | Stage 4f scaffold rewrite — runtime-only tier, no surfaces. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
