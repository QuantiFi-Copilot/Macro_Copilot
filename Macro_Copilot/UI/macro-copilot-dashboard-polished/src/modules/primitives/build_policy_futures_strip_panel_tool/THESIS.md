# THESIS — `build_policy_futures_strip_panel_tool`

> PANEL-BUILDER dual-view module.  The backend assembles a wide multi-instrument Panel of policy-futures IMPLIED RATES ('<CURVE_FAMILY>|<STRIP_POSITION>' columns, percent) across the SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT strips for workflow / backtest consumption — but the WIRE carries the panel's METADATA CONTRACT only (dims, column keys, date range, units, methodology card); the assembled cell matrix is a workflow-side `Panel` artifact the MCP layer (and the detail route, which replicates the drop) strips before serialisation.  Both Build surfaces are therefore PANEL-CONTRACT cards: they tell the PM exactly what panel a workflow would receive — and never pretend to show cells they don't have.

**Version:** v3 (dual-view panel-contract implementation)
**Last reviewed:** 2026-06-12
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `build_policy_futures_strip_panel_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/policy-futures-strip-panel`)
**Tier set:** `[generic_runnable, custom_build_surface]`
**Category:** `panels`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full-canvas PANEL-CONTRACT card: controls strip (family / strip-position CSVs that default BLANK → the full universe, YYYY-MM-DD date range with render-time defaults, advanced `field_name` + `calendar_policy` + `missing_data_policy` overrides), hero KPI strip (COLUMNS / ROWS / DATE RANGE / FAMILIES), the full column roster ('<CURVE_FAMILY>|<STRIP_POSITION>' keys with the desk stem read 'SFR3'/'ER8'/'SFI1', whites/reds segment tags, per-column units), the methodology card threading the backend `methodology_card`'s caveats VERBATIM (inverse pricing, rolling generics, cross-region calendar, per-family RFR/IBOR + Buba-mix disclosures), the panel-contract honesty note, and the lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — table-shaped grid card for multi-tool DAGs: identity ("POLICY-FUTURES STRIP PANEL · CONTRACT"), three contract KPIs (COLUMNS / ROWS / DATE RANGE), a column-chip strip (first 4 stem labels + "+K more", raw flat keys on the tooltip), the wire's rolling-generic caveat in the footer, and the click-to-expand affordance.

## 2. What does the user read off each surface?

### Extended Build view

The "what would my backtest get?" canvas.  A PM reads, in order: (a) the title row confirming this is the panel CONTRACT, with the honesty note card explaining that cells stay workflow-side; (b) the hero KPI strip — how many (family × strip slot) columns the scope resolved to, how many trade-date rows the window produced, the resolved inclusive date range, and how many policy-futures families are mixed into the panel; (c) the column roster — the exact '<CURVE_FAMILY>|<STRIP_POSITION>' keys (in deterministic column-axis order) a downstream operator would address rows by, each decomposed into family / stem slot ('SFR3') / whites-reds segment / closed-enum unit tag; (d) the methodology card — the requested-vs-YAML field, the `implied_rate_pct` value field, the calendar + ffill + missing-data policy line, and every wire caveat VERBATIM (inverse-pricing handling, rolling-generic strip caveat, cross-region business-days caveat, per-family RFR/IBOR regime + EUR Buba-mix disclosures).  The decisions: is my family/position scope right, did the window resolve to enough rows for the strategy, and do the disclosures invalidate my intended use (e.g. single-contract roll analysis against rolling-generic strip slots, or holiday-sensitive math against the unioned Mon-Fri calendar)?

### Compact Build view

The at-a-glance contract chip inside a multi-tool DAG (e.g. "build me the STIR strip panel AND the OIS panel AND run the cross-CB workflow").  A PM reads THREE numbers — COLUMNS, ROWS, DATE RANGE — which together answer "did the panel assemble, at what shape, over what window?".  The column-chip strip shows the first few resolved slots in the desk stem read ('SFR1', 'SFR2', …) so a wrong scope is visible without expanding; the footer carries the backend's rolling-generic strip caveat verbatim.  The expand arrow opens the extended view.

## 3. Why these surfaces and not others?

**Why a PANEL-CONTRACT card instead of a chart**: the wire has NO series — `compute()` returns the typed `Panel` and the MCP layer drops it (`{k: v for k, v in result.items() if k != "panel"}`); the detail route replicates that drop.  Rendering a chart would require either fetching cell data that intentionally never crosses this wire or fabricating one client-side (an FP9 violation).  The honest extended canvas is the contract: dims, roster, caveats.

**Why the compact view is TABLE-SHAPED (no sparkline)**: `rendering_density.md §2.2` names the sparkline as the typical headline-data vehicle, but its SEMANTIC contract is "identity / headline data / methodology / expand / tone cues" — and the scanner-compact precedent (`scan_extremes_tool/surfaces/BuildCompact.tsx`) already established that a non-series tool honours the contract with a table-shaped layout instead.  A panel contract's canonical headline data is its dims; a sparkline would have nothing truthful to draw.

**Why COLUMNS / ROWS / DATE RANGE as the three compact KPIs**: they are the desk-canonical "did it assemble?" read — shape first, window second.  Alternatives considered + rejected: FAMILIES count (derivable from the chips; surfaced in the extended hero strip), units (uniform 'percent' across implied-rate cells — zero glance value), positions count (visible in the chip strip; a number with no extra decision content over COLUMNS).

**Why the roster decomposes the flat key client-side**: columns are (family, strip position) pairs flattened to '<CURVE_FAMILY>|<STRIP_POSITION>' strings because the Panel artifact's `units_by_column` is `Dict[str, TimeSeriesUnits]` (tuple keys would break the typed boundary).  Splitting the key at the separator for the family / stem / segment columns is presentation-side string parsing of a wire value, not computed finance — the same FP9 line the sovereign sibling draws when splitting '<curve_family>_<tenor>'.

**Why NO `monitor_surface`**: a panel contract is assembly metadata, not glanceable morning state — there is no level, change, or stretch to read at breakfast, and `surface_contract.md §3.4` eligibility (canonical morning-briefing read) plainly fails.  Claiming it would be tier inflation (FM4).

**Why NO `ask_surface` / `custom_preview_widget`**: the generic assistant card renders the contract dims adequately in chat; the persisted-artifact preview path belongs to the workflow surfaces that actually hold the `Panel` artifact, not to this metadata module.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/policy-futures-strip-panel`): per `methodology_exposure.md §5` every standalone module ships its own typed bridge; `curve_families` / `strip_positions` flatten to repeated query params, and the module's URL params carry the same lists comma-joined per the wiring handoff.

**Why BLANK-default scope CSVs** (unlike the sovereign sibling's seeded leg lists): the sibling's leg list is a REQUIRED Input field, so it needs a starter default to avoid a 422; here `curve_families` and `strip_positions` are optional — omitting them is the backend-sanctioned "full universe × full strip" read, so a blank Library invocation renders the honest 24-column cross-CB contract with zero client-side invention.  The closed family / position sets are enforced backend-side (422 on a typo, surfaced verbatim in the error state); the only client-side pre-validation is the integer parse on the positions CSV.

## 4. What would change the design?

- **A "panel preview rows" backend extension** (a small tail-sample of cells added to the wire, analogous to the typed-detail `time_series` blocks on level tools) would change the compact card materially — a real mini strip-curve read per family becomes honest, and the extended view would gain a preview table between the roster and the methodology card.  Until the backend ships it, drawing anything is an FP9 violation.
- **The `delivery_month_type` annotation landing** (the playbook-extension queue item that unblocks the EUR Buba serial/quarterly split) would add a cadence column to the roster for EUR_SHORT_RATE_FUT cells and refresh the verbatim Buba-mix caveat the backend emits.
- **New policy-futures families** (e.g. a CORRA strip) require a one-line registry entry in `policyFuturesStripPanelShared.ts` (flag / labels / regime / stem) — the roster, KPIs and methodology threading all read the wire; unknown families already degrade to the raw flat key.
- **A MultiIndex column axis** (literal `(curve_family, strip_position)` tuple keys, gated on a Panel typed-artifact extension) would replace the client-side key split with direct wire fields — strictly less parsing, same layout.
- **Workflow-template surfaces landing** (the deferred workflow contract in `rendering_density.md §4`) would mount this module's compact card as a workflow-DAG node body unchanged — both mounts consume the same component by design.

## 5. Which backend doctrine does this module operationalise?

- **P5** (honest disclosure) — the backend `methodology_card`'s caveat fields (`inverse_pricing_handling`, `rolling_generic_strip_caveat`, `cross_region_business_days_caveat`, per-family `regime_caveat` / `buba_mix_caveat`) thread onto the methodology card VERBATIM; the panel-contract note states explicitly that cells never cross this wire.
- **P8** (closed-family discipline) — policy-futures families and strip positions are closed backend Literals; the module exposes them as free text and lets the backend 422 authoritatively rather than duplicating the enum client-side.
- **FP9** (no client-side compute) — dims, keys, units and caveats render as-received; no series is fabricated; the only client logic is presentation-side splitting of the '<CURVE_FAMILY>|<STRIP_POSITION>' flat keys.
- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3 / FM4** — claims `generic_runnable` + `custom_build_surface`; FM4 parsimony respected for every other capability tier (see Q3).
- **FM5** (display metadata) — displayName / category preserved from the Stage-3 scaffold; oneLineSummary expanded to the PM-facing substrate framing.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; the default date window for the required `start_date` is computed at RENDER time via `surfaces/policyFuturesStripPanelShared.ts::defaultWindow()`.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; legacy `build` alias === `buildExtended` for the current dispatcher.
- **FM9** (routing-claim disclosure) — `typedView: null`; `richModel: false`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint `/api/v1/rates/detail/policy-futures-strip-panel`; own service helper `fetchDetailPolicyFuturesStripPanel`; own frontend type `BuildPolicyFuturesStripPanelOutput` (wire mirror MINUS `panel`).
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship; the compact card's table shape is justified against §2.2 via the scanner-compact precedent.
- Per **ADR 0013** (policy_futures domain) — the per-family RFR/IBOR regime labels and the EUR Buba-mix preservation/disclosure discipline surface exactly as the backend declares them; the module owns its per-tool curve registry copy (flags / stems / regimes) rather than cross-importing the snapshot sibling's.
- Backend **MCP-drop mechanism** (typed `panel: Panel` field stripped at `mcp_server.py`, replicated by the detail route) — the module's honesty note mirrors this mechanism instead of hiding it.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-12 | Dual-view panel-contract implementation.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, and `surfaces/policyFuturesStripPanelShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface]` (no monitor — Q3).  Standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/policy-futures-strip-panel`; blank-default family/position CSV param convention per the wiring handoff. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
