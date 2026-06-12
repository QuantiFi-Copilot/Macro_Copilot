# THESIS — `build_zcis_panel_tool`

> PANEL-BUILDER dual-view module.  The backend assembles a wide multi-instrument Panel of zero-coupon inflation swap rates (one `vendor_ticker` column per swap across the USD_ZCIS / EUR_ZCIS / GBP_ZCIS universe) for cross-curve regression / PCA / RV-scan consumption — but the WIRE carries the panel's METADATA CONTRACT only (dims, column keys, date range, units, methodology_card); the assembled cell matrix is a workflow-side `Panel` artifact the MCP layer (and the detail route, which replicates the drop) strips before serialisation.  Both Build surfaces are therefore PANEL-CONTRACT cards: they tell the PM exactly what panel a workflow would receive — and never pretend to show cells they don't have.

**Version:** v3 (dual-view panel-contract implementation)
**Last reviewed:** 2026-06-12
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `build_zcis_panel_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/zcis-panel`)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Category:** `panels`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full-canvas PANEL-CONTRACT card: controls strip (ZCIS-family CSV, tenor CSV, YYYY-MM-DD date range with render-time defaults, advanced `field_name` + `calendar_policy` + `missing_data_policy` overrides), hero KPI strip (COLUMNS / ROWS / DATE RANGE / FAMILIES × TENORS), the full column roster (`vendor_ticker` keys with per-column units), the per-family index-reference table (inflation index family / index_lag / interpolation / underlying index, VERBATIM off the methodology_card's `curve_family_reference`), the methodology card threading the backend's `security_name_caveat` + `index_family_caveat` VERBATIM, the panel-contract honesty note, and the lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — table-shaped grid card for multi-tool DAGs: identity ("ZCIS PANEL · CONTRACT"), three contract KPIs (COLUMNS / ROWS / DATE RANGE), a column-chip strip (first 4 vendor_ticker keys + "+K more"), the sharpest wire caveat in the footer, and the click-to-expand affordance.

## 2. What does the user read off each surface?

### Extended Build view

The "what would my backtest get?" canvas.  A PM reads, in order: (a) the title row confirming this is the panel CONTRACT, with the honesty note card explaining that cells stay workflow-side; (b) the hero KPI strip — how many columns the family/tenor scope resolved to, how many trade-date rows the window produced, the resolved inclusive date range, and the FAMILIES × TENORS scope (whose product can exceed COLUMNS — tenors absent on a family are silently dropped backend-side, so the grid is sparse and the caption says so); (c) the column roster — the exact `vendor_ticker` keys (in the wire's deterministic (curve_family, tenor, vendor_ticker) order) a downstream operator would address columns by, each with its closed-enum unit tag; (d) the per-family index-reference table — which inflation index each curve family actually references (CPI-U vs HICP-xT vs RPI), with its index_lag and interpolation convention, VERBATIM off the wire; (e) the methodology card — resolved field / calendar / missing-data policies, the unit set, and the backend's `security_name_caveat` + `index_family_caveat` VERBATIM.  The decisions: is my family/tenor scope right, did the window resolve to enough rows for the strategy, and does mixing three inflation regimes (different indices, different lags) invalidate my intended cross-curve use?

### Compact Build view

The at-a-glance contract chip inside a multi-tool DAG (e.g. "build me the ZCIS panel AND the linker panel AND run the breakeven-vs-swap workflow").  A PM reads THREE numbers — COLUMNS, ROWS, DATE RANGE — which together answer "did the panel assemble, at what shape, over what window?".  The column-chip strip shows the first few resolved vendor_ticker keys so a wrong family/tenor scope is visible without expanding; the footer carries the sharpest wire caveat (the `index_family_caveat` — CPI-U / HICP-xT / RPI are NOT a harmonised expected-inflation surface) verbatim.  The expand arrow opens the extended view.

## 3. Why these surfaces and not others?

**Why a PANEL-CONTRACT card instead of a chart**: the wire has NO series — `compute()` returns the typed `Panel` and the MCP layer drops it (`{k: v for k, v in result.items() if k != "panel"}`); the detail route replicates that drop.  Rendering a chart would require either fetching cell data that intentionally never crosses this wire or fabricating one client-side (an FP9 violation).  The honest extended canvas is the contract: dims, roster, index reference, caveats.

**Why the compact view is TABLE-SHAPED (no sparkline)**: `rendering_density.md §2.2` names the sparkline as the typical headline-data vehicle, but its SEMANTIC contract is "identity / headline data / methodology / expand / tone cues" — and the scanner-compact precedent (`scan_extremes_tool/surfaces/BuildCompact.tsx`) already established that a non-series tool honours the contract with a table-shaped layout instead.  A panel contract's canonical headline data is its dims; a sparkline would have nothing truthful to draw.

**Why COLUMNS / ROWS / DATE RANGE as the three compact KPIs**: they are the desk-canonical "did it assemble?" read — shape first, window second.  Alternatives considered + rejected: FAMILIES × TENORS scope (surfaced in the extended hero strip; on the compact card the chips already expose a wrong scope), units (uniform 'percent' across ZCIS columns — zero glance value), caveat count (a number with no decision content; the sharpest caveat itself is in the footer).

**Why a per-family index-reference table (extended only)**: this panel's defining risk is REGIME MIXING — USD/EUR/GBP ZCIS reference three different inflation indices with different indexation lags, and the backend declares that per-family via the methodology_card's `curve_family_reference` block.  Threading it as a scannable table (rather than burying it in prose) is the P5-honest rendering of the wire's own disclosure.  The wire does NOT carry a per-COLUMN family/tenor mapping, so the roster stays vendor_ticker + unit — parsing ticker mnemonics client-side to fake those columns would be FP9 inference.

**Why NO `monitor_surface`**: a panel contract is assembly metadata, not glanceable morning state — there is no level, change, or stretch to read at breakfast, and `surface_contract.md §3.4` eligibility (canonical morning-briefing read) plainly fails.  Claiming it would be tier inflation (FM4).

**Why NO `ask_surface` / `custom_preview_widget`**: the generic assistant card renders the contract dims adequately in chat; the persisted-artifact preview path belongs to the workflow surfaces that actually hold the `Panel` artifact, not to this metadata module.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/zcis-panel`): per `methodology_exposure.md §5` every standalone module ships its own typed bridge; `curve_families` and `tenors` render as repeated query params, and the module's URL params carry the same lists comma-joined.

**Why independent CSV text controls** (not a paired leg-builder): unlike the sovereign panel's index-wise leg pairs, the Input's `curve_families` and `tenors` are INDEPENDENT optional scopes — a cross product, not a leg list — so two free CSVs with explicit full-universe defaults expose the actual Pydantic fields without inventing a pairing invariant that doesn't exist.  The closed ZCIS family set is enforced backend-side (422 on a typo, surfaced verbatim in the error state).

## 4. What would change the design?

- **A "panel preview rows" backend extension** (a small tail-sample of cells added to the wire, analogous to the typed-detail `time_series` blocks on level tools) would change the compact card materially — a real mini heat-strip / sparkline per column becomes honest, and the extended view would gain a preview table between the roster and the methodology card.  Until the backend ships it, drawing anything is an FP9 violation.
- **A per-column mapping on the wire** (vendor_ticker → curve_family / tenor) would add family and tenor columns to the roster table — today that mapping intentionally stays backend-side and the roster does not fake it.
- **New ZCIS families** (e.g. `JPY_ZCIS`) require zero frontend change beyond the default-CSV constant — the roster, index-reference table, and KPI scope all read the wire; only the THESIS example text would refresh.
- **A tenor-picker UX decision** (desk feedback that free CSVs are error-prone) would replace the text controls with structured family/tenor multi-selects — a per-tool component, no shell change.
- **Workflow-template surfaces landing** (the deferred workflow contract in `rendering_density.md §4`) would mount this module's compact card as a workflow-DAG node body unchanged — both mounts consume the same component by design.

## 5. Which backend doctrine does this module operationalise?

- **P5** (honest disclosure) — the backend's `security_name_caveat` + `index_family_caveat` thread onto the methodology card VERBATIM; the `curve_family_reference` block renders as the per-family index table verbatim; the panel-contract note states explicitly that cells never cross this wire.
- **P8** (closed-family discipline) — ZCIS families are a closed backend Literal; the module exposes them as free text and lets the backend 422 authoritatively rather than duplicating the enum client-side.
- **FP9** (no client-side compute) — dims, keys, units, reference rows and caveats render as-received; no series is fabricated; no family/tenor is inferred from ticker mnemonics.
- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3 / FM4** — claims `generic_runnable` + `custom_build_surface`; FM4 parsimony respected for every other capability tier (see Q3).
- **FM5** (display metadata) — displayName / category preserved from the Stage-3 scaffold; oneLineSummary expanded to the full panel-contract sentence (PM-facing).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; the default date window is computed at RENDER time via `surfaces/zcisPanelShared.ts::defaultWindow()`.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; legacy `build` alias === `buildExtended` for the current dispatcher.
- **FM9** (routing-claim disclosure) — `typedView: null`; `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/zcis-panel`; own service helper `fetchDetailZcisPanel`; own frontend type `BuildZcisPanelOutput` (wire mirror MINUS `panel`).
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship; the compact card's table shape is justified against §2.2 via the scanner-compact precedent.
- The backend's **typed `panel: Panel` field + MCP drop** mechanism — the module's honesty note mirrors this mechanism instead of hiding it.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view panel-contract implementation.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and `surfaces/zcisPanelShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface]` (no monitor — Q3).  Standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/zcis-panel`; independent family/tenor CSV param convention per the wiring handoff; per-family index-reference table threading `curve_family_reference` verbatim. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
