# THESIS — `get_scan_policy_futures_extremes_tool`

> Universe-wide policy-futures (STIR) extremes scanner under the dual-view + standalone-bridge contract.  SCANNER shape (MULTI-METRIC) — the wire returns a ranked LIST of (curve_family, strip_position, contract_code) extremes across FOUR metrics (implied-rate LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL, open-interest LEVEL), each independently ranked by |z| of the 252d-rolling z-score on that metric.  Not a single time series — so the compact view is a top-N TABLE with PACK (WHITES / REDS) + SCOPE (IR / Δ / VOL / OI) chips per row, the extended view is a universe-scan canvas (distribution histogram + multi-metric ranked detail), and the Monitor tile surfaces the cross-metric top-3.

**Version:** v3 (initial SCANNER build — replaces the Stage 4f runtime-only scaffold)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_scan_policy_futures_extremes_tool` (generic_runnable; policy_futures domain per ADR 0013)
**Bridge endpoint:** `/api/v1/rates/detail/policy-futures-scanner`
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `scanners`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; falls back to `GenericPrimitiveBuilder` if the user reaches it via a non-canonical Library "Open in Build" path that bypasses the typed-detail endpoint.
- **`custom_build_surface` → extended** (`surfaces/BuildExtended.tsx`).  Universe-scan canvas: identity row + scan-summary top-right cards (flagged-count, distribution across all metrics, central-bank + RFR/IBOR regime breakdown) + controls strip (STIR scope / threshold / top-N per metric) + z-score distribution histogram across all four metrics + scan-summary callouts + per-metric flagged-count breakdown card + ranked multi-metric detail table with per-row PACK chip (WHITES / REDS), SCOPE chip (IR / Δ / VOL / OI), REGIME chip (RFR / IBOR), native value + 1D Δ + tone-cued z-score + "Open in Build" deep-link to the per-strip `policy_futures_get_futures_price_level_tool` + methodology card sourced from the wire's `methodology_disclosure` + lineage footer.
- **`custom_build_surface` → compact** (`surfaces/BuildCompact.tsx`).  Grid card with the top-5 cross-metric ranked rows in a (# / contract / pack / scope / value (native, 1D Δ, narrative) / z-score) table — NOT a sparkline.  Each row carries a PACK chip (WHITES for strip positions 1-4, REDS for 5-8) and a SCOPE chip (IR / Δ / VOL / OI), tone-cued z-scores (|z| ≥ 1.5 amber, |z| ≥ 2.0 coral/mint by direction), and a narrative qualifier ("OI build" / "Volume surge" / "Rate stretched high" / etc.) drawn from the metric × signal pair.  "MOST EXTREME" highlight row below the table; footer caveat + "View all N" expand affordance.
- **`monitor_surface` → `PolicyFuturesScannerWidget`** (`surfaces/monitor/PolicyFuturesScannerWidget.tsx`).  Catalog tile parameterised on (scope, top_n, min_abs_z_score).  Surfaces the flagged-count chip + the top-3 cross-metric ranked rows + the load-bearing Fed / ECB / BoE meeting + RFR/IBOR regime caveat.

## 2. What does the user read off each surface?

**Extended (single-tool query).** A PM opens the canvas with one question — *"where is the STIR universe stretched today, ahead of the next meeting cluster?"*.  The scan-summary card answers it in one number ("5 of 9 flagged at |z| ≥ 2.0σ across all four metrics").  The distribution histogram shows whether the flagged extremes lean rich (low z = rates stretched low / volume quiet / OI unwind) or cheap (high z = rates stretched high / volume surge / OI build), and how many sit beyond the 2σ envelope.  The central-bank card on the top-right (USD Fed / EUR ECB / GBP BoE, each tagged RFR or IBOR per ADR 0013) tells the PM at a glance which central-bank-anchored stems show up in the top-N.  The per-metric breakdown card (IR · Δ · VOL · OI counts) tells the PM whether the extremity concentrates in IR (the headline question), Δ (today's move), VOLUME (activity surge), or OPEN INTEREST (positioning shift).  The ranked multi-metric table is the action layer — every flagged row is tagged with a PACK chip (WHITES front-year / REDS second-year), a SCOPE chip and a REGIME chip; clicking "Open in Build" on any row routes to the per-strip `policy_futures_get_futures_price_level_tool` with (curve_family, contract_code) bound.  The methodology card surfaces the wire's full disclosure verbatim — the 252d window, the rolling-generic-strip caveat, the inverse-pricing rule, the RFR-vs-IBOR per-row regime caveat, and the V1-monitors-only / CTD-out-of-scope ADR 0013 statement.

**Compact (multi-tool DAG node).** A PM running a multi-tool query like *"compare STIR extremes vs OIS extremes vs ZCIS extremes"* sees three scanner cards side-by-side.  The STIR card's three things the PM reads in 2 seconds: (1) WHICH stem tops the cross-metric ranking ("SFR3 WHITES IR" = front-year SOFR strip on the implied-rate axis), (2) at WHAT |z| and on WHAT pack ("+2.7σ — Rate stretched high"), (3) how many other stems are flagged ("5 Flagged · IR+Δ+V+O").  The expand affordance opens the full extended canvas in a modal with the multi-tool DAG behind it; the "View all N" footer link is a secondary entry to the same modal.

**Monitor.** Inherently compact (bento-grid constraint).  Surfaces the flagged-count headline + the top-3 CROSS-METRIC ranked rows (sorted by |z| across all four metrics); the threshold chip tells the PM at a glance whether anything is at 2σ extremity right now.  The catalog form lets the desk pin one tile per scope (e.g. one All-Universe tile, one SOFR-only tile) without per-tile JSX changes.

## 3. Why these surfaces and not others?

**Why a custom compact view (top-N table with PACK + SCOPE chips) rather than `AutoRenderer` or the standard `BuildCompactShell`?**
The standard `BuildCompactShell` is LEVEL-shape oriented — three KPI cells + sparkline + footer.  The scanner's headline data is a MULTI-METRIC ranked LIST of extremes, not a single number with a chart.  A literal 3-KPI mapping would surface only ONE row (rank 1 on ONE metric) and would drop the comparative context (the rest of the cross-metric top-N + the flagged count + the per-metric breakdown + the WHITES-vs-REDS pack distinction that STIR desks read for free) that IS the scanner's point.  The catalog guardrail explicitly calls this out: this primitive's V1 wire is multi-metric ranked-list — the compact view honours the SEMANTIC contract of rendering_density.md §2.2 (identity / headline data / methodology / expand / tone cues) with a scanner-shaped layout, reusing the shared `FreshnessPill` + `toneTextClass` + format helpers so visual treatment matches the rest of the compact catalogue.  Mirrors the `scan_bond_futures_extremes_tool` / `scan_inflation_swaps_extremes_tool` sibling precedents but adds the PACK chip the STIR mockup explicitly requires.

**Why a custom extended view rather than `BuildExtendedShell`?**
The extended shell is also chart-centric (chartPoints + reference bands + KPI strip).  The scanner needs a distribution histogram + a ranked-detail table whose ROWS carry the per-metric SCOPE + PACK + REGIME chips + narrative qualifier.  The shared shell's `ControlsStrip` + `MethodologyCard` + `LineageFooter` ARE finance-blind enough to drop in directly; this module composes them inside a scanner-shaped layout rather than fight the chart-centric main-canvas.

**Why claim `monitor_surface` (vs FM4 parsimony)?**
The desk's morning ritual is the universe-wide STIR sweep ahead of Fed / ECB / BoE meeting clusters — this is exactly the use case FM4's exception #2 enumerates ("the desk reads this primitive at a glance every day").  Without the Monitor tile, a PM has to open the full Build canvas every morning; with it, the bento grid renders the flagged count + top-3 rows alongside the sibling bond_futures / ZCIS / linker / sovereign scanners.

**Why a MULTI-METRIC scan and not a single-metric one?**
The backend wire is multi-metric per the catalog's V1 wording ("the four metrics — implied rate, Δ, volume, OI — ARE the concept") + ADR 0013, and exposing them as a per-query input would be input-schema overreach.  The frontend mirrors the wire — the SCOPE chip per row + the per-metric breakdown card are the compact rendering of "four metrics combined into one universe sweep".

**Why CONTRACT_CODE (SFR3 / ER1 / SFI2) + PACK chip as the canonical row identifier rather than (curve_family, strip_position) alone?**
The wire carries `strip_position` (1..8) as the canonical disambiguator within a curve family, and `contract_code` (SFR1 / ER1 / SFI3) is the master rolling-generic stem.  The STIR desk reads strip_position as a PACK label (positions 1-4 = WHITES front-year; 5-8 = REDS second-year) — this is the desk-canonical compression of strip_position into a glanceable chip.  Per the mockup, the PACK chip appears next to the contract code on every ranked row.

**Why the per-row REGIME chip (RFR vs IBOR) on the extended view?**
ADR 0013 requires the per-row RFR-vs-IBOR disclosure on every output row — SOFR_FUT / SONIA_FUT are RFR-anchored (compounded daily risk-free rate); EUR_SHORT_RATE_FUT is IBOR-anchored (unsecured 3M term Euribor).  The scan ranks across this heterogeneity; a desk consumer reading "ER1 +2.4σ" must see immediately that this is an IBOR-anchored rate, not an RFR-anchored rate.

**Why not `ask_surface`?**
The chat dispatcher's generic `AssistantResearchCard` handles the "give me the STIR extremes" Ask response perfectly today — the response shape (top-N rows) reads naturally as a chat bubble.  A bespoke Ask card would be incremental at best; deferred per FM4.

**Why not `custom_preview_widget`?**
Persisted-artifact preview cards (the per-tool node renderer in the workspace DAG) for scanners are well-served by the artifact-type generic registry; the scanner doesn't carry a domain-specific preview need beyond what the workspace's node renderer already does.

## 4. What would change the design?

- **Meeting-by-meeting policy-path decomposition (ADR 0013 Phase-4).** Once the WIRP-style meeting-by-meeting decomposition primitive lands, the extended view would gain a "meeting calendar" sidebar showing how the universe extremity aligns with upcoming Fed / ECB / BoE meeting dates.  Today's Phase-1 V1-monitors-only scope surfaces this limit honestly on the methodology card.
- **CTD-of-futures-of-OIS scan (ADR 0013 Phase-4).** Maps each strip slot's implied rate onto a calibrated OIS forward curve; gated on the OIS-curve interpolation stack landing as primitives.  When it lands, the SCOPE chip vocabulary expands (OIS-BASIS alongside today's IR / Δ / VOL / OI) and the extended view's distribution panel may need a per-metric facet view.
- **Cross-CB ranking variant.** The catalog's `planned_extensions` includes a cross-CB ranking that ranks SOFR_FUT vs SONIA_FUT vs EUR_SHORT_RATE_FUT stems against EACH OTHER on a common-tenor basis (today the scan ranks each stem against its OWN distribution).  Needs a benchmark-family-mismatch disclosure caveat per row and an explicit common-tenor projection — its own primitive.
- **Wire-level pre-bucketed distribution.** Today the histogram is synthesised on the FRONTEND from the returned multi-metric rows (labelled "of the flagged extremes · all metrics").  If compute() ever ships a `universe_distribution_bins` field on the response, the histogram becomes the full-universe distribution and the surface label updates accordingly.
- **Per-row sparkline.** A future enhancement (deferred) is adding a 60d sparkline of each ranked stem's underlying-metric series to the extended view's table — would require a second wire field (or N second-tier fetches) and is non-blocking today.
- **Direct-priced family support (e.g. TIIE_FUT).** The PolicyFuturesScanCurveFamily Literal in schemas.py is the gate; a new family requires an ADR + schema migration.  The per-row `inverse_priced` flag in the TS type mirror is already wired so the frontend will render a direct-priced row honestly when the backend adds one.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly (`get_scan_policy_futures_extremes_tool`).  The workflow registry uses the sub-agent-prefixed `policy_futures_get_scan_policy_futures_extremes_tool` form but that name does not appear in TS code; the FM1 invariant binds frontend folder ↔ MCP function name, which match here.
- **FM3** (surface-tier capability declaration) — claims `[generic_runnable, custom_build_surface, monitor_surface]`; both Build files exist; the Monitor catalog entry references the single-component shape.
- **FM4** (tier parsimony) — Monitor claim justified per Exception #2; dual-view mandate per the override in §1.2 of rendering_density.md.
- **FM5** (display-metadata sourcing) — `oneLineSummary` paraphrases the backend's tool description; `defaultParams` map onto the Pydantic Input (curve_families / top_n / min_abs_z_score).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value with no side effects.
- **FM8** (surface-file contract) — `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and `surfaces/monitor/PolicyFuturesScannerWidget.tsx` are present and referenced exactly once each.
- **FM9** (routing-claim disclosure) — `typedView: null` + `richModel: false` per the standalone-bridge contract.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.
- **rendering_density.md §1 / §2.2** (dual-view mandate) — extended + compact both populated; compact is a top-N TABLE per the SCANNER-shape guardrail; methodology is reachable via the footer caveat (full wire disclosure surfaces on hover via `title=`).
- **methodology_exposure.md §5** (standalone-bridge contract) — own `/api/v1/rates/detail/policy-futures-scanner` endpoint + own `fetchDetailPolicyFuturesScanner` helper + own `ScanPolicyFuturesExtremesOutput` TS type mirror; no shared `typedView` reuse.
- **PR10 / P5** (wire-honesty disclosure) — `methodology_disclosure` is consumed VERBATIM from `data.methodology_disclosure` on both the extended methodology card and the compact footer tooltip; the per-tool `POLICY_FUTURES_SCANNER_COMPACT_CAVEAT` constant is the desk-canonical SHORT rendering of the ADR 0013 RFR/IBOR + Fed / ECB / BoE meeting-cluster framing (mirrors the bond_futures / ZCIS / linker scanner pattern).
- **ADR 0013** (policy_futures V1 monitors-only) — surfaced explicitly on the methodology card AND the per-row REGIME chip + the central-bank summary card title ("CENTRAL-BANK REGIME (ADR 0013)") so the desk reader cannot mistake an IBOR-anchored z-score for an RFR-anchored z-score and cannot mistake the scan for a meeting-by-meeting policy-path decomposition.

---

## One-line summary

Universe-wide policy-futures (STIR) strip sweep — ranks every (curve_family, strip_position) STIR stem (SFR1..8 / ER1..8 / SFI1..8) by absolute 252-day z-score across implied-rate LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL, and open-interest LEVEL; top-5 cross-metric table compact view with PACK (WHITES / REDS) + SCOPE (IR / Δ / VOL / OI) chips, multi-metric distribution + ranked detail extended view, Monitor bento tile.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Initial SCANNER build — dual-view + standalone-bridge + monitor.  Multi-metric top-N table compact + universe-scan extended + Monitor tile.  Mirrors the `scan_bond_futures_extremes_tool` / `scan_inflation_swaps_extremes_tool` / `scan_inflation_linkers_extremes_tool` precedents adapted for the policy-futures four-metric wire (implied-rate LEVEL, IR CHANGE in bps, volume LEVEL, OI LEVEL) + STIR-specific PACK (WHITES / REDS) + RFR-vs-IBOR REGIME chip per ADR 0013. |
| v2 | 2026-05-26 | Stage 4f scaffold rewrite — runtime-only tier, no surfaces. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
