# THESIS — `build_sovereign_yield_panel_tool`

> PANEL-BUILDER dual-view module.  The backend assembles a wide multi-leg Panel of sovereign yields ('<curve_family>_<tenor>' columns) for workflow / backtest consumption — but the WIRE carries the panel's METADATA CONTRACT only (dims, column keys, date range, units, methodology disclosures); the assembled cell matrix is a workflow-side `Panel` artifact the MCP layer (and the detail route, which replicates the drop) strips before serialisation.  Both Build surfaces are therefore PANEL-CONTRACT cards: they tell the PM exactly what panel a workflow would receive — and never pretend to show cells they don't have.

**Version:** v3 (dual-view panel-contract implementation)
**Last reviewed:** 2026-06-12
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `build_sovereign_yield_panel_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/sovereign-yield-panel`)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Category:** `panel_assembly`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full-canvas PANEL-CONTRACT card: controls strip (paired leg-family/leg-tenor CSVs, YYYY-MM-DD date range with render-time defaults, advanced `field_name` + `missing_data_policy` overrides), hero KPI strip (COLUMNS / ROWS / DATE RANGE / FAMILIES), the full column roster ('<curve_family>_<tenor>' keys with per-column units), the methodology card threading the backend's `methodology_disclosures` VERBATIM, the panel-contract honesty note, and the lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — table-shaped grid card for multi-tool DAGs: identity ("SOVEREIGN YIELD PANEL · CONTRACT"), three contract KPIs (COLUMNS / ROWS / DATE RANGE), a column-chip strip (first 4 keys + "+K more"), the sharpest wire disclosure in the footer, and the click-to-expand affordance.

## 2. What does the user read off each surface?

### Extended Build view

The "what would my backtest get?" canvas.  A PM reads, in order: (a) the title row confirming this is the panel CONTRACT, with the honesty note card explaining that cells stay workflow-side; (b) the hero KPI strip — how many columns the leg list resolved to, how many trade-date rows the window produced, the resolved inclusive date range, and how many sovereign families are mixed into the panel; (c) the column roster — the exact '<curve_family>_<tenor>' keys (in leg order) a downstream operator would address rows by, each with its closed-enum unit tag; (d) the methodology card — the requested-vs-YAML field, the unit set, and every backend `methodology_disclosures` entry VERBATIM (V1 calendar limitation, ffill policy, sovereign-families-only refusal).  The decisions: is my leg list right, did the window resolve to enough rows for the strategy, and do the disclosures invalidate my intended use (e.g. holiday-sensitive carry math against the V1 weekend-only calendar)?

### Compact Build view

The at-a-glance contract chip inside a multi-tool DAG (e.g. "build me the UST/Bund panel AND the linker panel AND run the spread workflow").  A PM reads THREE numbers — COLUMNS, ROWS, DATE RANGE — which together answer "did the panel assemble, at what shape, over what window?".  The column-chip strip shows the first few resolved keys so a wrong leg list is visible without expanding; the footer carries the sharpest V1 disclosure verbatim.  The expand arrow opens the extended view.

## 3. Why these surfaces and not others?

**Why a PANEL-CONTRACT card instead of a chart**: the wire has NO series — `compute()` returns the typed `Panel` and the MCP layer drops it (`{k: v for k, v in result.items() if k != "panel"}`); the detail route replicates that drop.  Rendering a chart would require either fetching cell data that intentionally never crosses this wire or fabricating one client-side (an FP9 violation).  The honest extended canvas is the contract: dims, roster, disclosures.

**Why the compact view is TABLE-SHAPED (no sparkline)**: `rendering_density.md §2.2` names the sparkline as the typical headline-data vehicle, but its SEMANTIC contract is "identity / headline data / methodology / expand / tone cues" — and the scanner-compact precedent (`scan_extremes_tool/surfaces/BuildCompact.tsx`) already established that a non-series tool honours the contract with a table-shaped layout instead.  A panel contract's canonical headline data is its dims; a sparkline would have nothing truthful to draw.

**Why COLUMNS / ROWS / DATE RANGE as the three compact KPIs**: they are the desk-canonical "did it assemble?" read — shape first, window second.  Alternatives considered + rejected: FAMILIES count (derivable from the chips; surfaced in the extended hero strip), units (uniform 'percent' across sovereign yield legs — zero glance value), disclosure count (a number with no decision content; the sharpest disclosure itself is in the footer).

**Why NO `monitor_surface`**: a panel contract is assembly metadata, not glanceable morning state — there is no level, change, or stretch to read at breakfast, and `surface_contract.md §3.4` eligibility (canonical morning-briefing read) plainly fails.  Claiming it would be tier inflation (FM4).

**Why NO `ask_surface` / `custom_preview_widget`**: the generic assistant card renders the contract dims adequately in chat; the persisted-artifact preview path belongs to the workflow surfaces that actually hold the `Panel` artifact, not to this metadata module.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/sovereign-yield-panel`): per `methodology_exposure.md §5` every standalone module ships its own typed bridge; the nested leg-spec list flattens to PAIRED repeated query lists (`leg_curve_families[i]` ↔ `leg_tenors[i]`) per the rolling-regression flattening precedent, and the module's URL params carry the same pairs comma-joined.

**Why paired CSV text controls** (not dropdowns): the Input is an ordered list of up to 20 unique (family, tenor) legs — a free-form paired-CSV pair is the only control shape that exposes the actual Pydantic field without inventing a bespoke list-builder widget; the closed sovereign-family set is enforced backend-side (422 on a typo, surfaced verbatim in the error state).

## 4. What would change the design?

- **A "panel preview rows" backend extension** (a small tail-sample of cells added to the wire, analogous to the typed-detail `time_series` blocks on level tools) would change the compact card materially — a real mini heat-strip / sparkline per column becomes honest, and the extended view would gain a preview table between the roster and the methodology card.  Until the backend ships it, drawing anything is an FP9 violation.
- **A roster metadata enrichment** (per-column vendor_ticker / benchmark reference on the methodology card, as the linker/ZCIS panels carry via `curve_family_reference`) would add reference columns to the roster table.
- **New sovereign families** (e.g. `SE_GOVT`) require zero frontend change — the roster and family derivation read the wire; only the THESIS example text would refresh.
- **A leg-builder UX decision** (desk feedback that paired CSVs are error-prone) would replace the two text controls with a structured leg-list editor — a per-tool component, no shell change.
- **Workflow-template surfaces landing** (the deferred workflow contract in `rendering_density.md §4`) would mount this module's compact card as a workflow-DAG node body unchanged — both mounts consume the same component by design.

## 5. Which backend doctrine does this module operationalise?

- **P5** (honest disclosure) — the backend's `methodology_disclosures` thread onto the methodology card VERBATIM; the panel-contract note states explicitly that cells never cross this wire.
- **P8** (closed-family discipline) — sovereign families are a closed backend set; the module exposes them as free text and lets the backend 422 authoritatively rather than duplicating the enum client-side.
- **FP9** (no client-side compute) — dims, keys, units and disclosures render as-received; no series is fabricated; the only client logic is presentation-side splitting of '<family>_<tenor>' keys.
- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3 / FM4** — claims `generic_runnable` + `custom_build_surface`; FM4 parsimony respected for every other capability tier (see Q3).
- **FM5** (display metadata) — displayName / category / oneLineSummary preserved from the Stage-3 scaffold (PM-facing).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; the default date window is computed at RENDER time via `surfaces/sovereignYieldPanelShared.ts::defaultWindow()`.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; legacy `build` alias === `buildExtended` for the current dispatcher.
- **FM9** (routing-claim disclosure) — `typedView: null`; `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/sovereign-yield-panel`; own service helper `fetchDetailSovereignYieldPanel`; own frontend type `SovereignYieldPanelOutput` (wire mirror MINUS `panel`).
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship; the compact card's table shape is justified against §2.2 via the scanner-compact precedent.
- Backend **PR 20** (typed `panel: Panel` field + MCP drop) — the module's honesty note mirrors this mechanism instead of hiding it.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view panel-contract implementation.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and `surfaces/sovereignYieldPanelShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface]` (no monitor — Q3).  Standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/sovereign-yield-panel`; paired leg-CSV param convention per the wiring handoff. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
