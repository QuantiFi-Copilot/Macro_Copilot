# THESIS — `calculate_zscore_custom_tool`

> Dual-view module under the rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1 — plus a Monitor tile parameterised on the tool's central knob.  The UX framing: the WINDOW is the product.  Every other z-emitting tool in the catalogue pins the window at 252d; this module exists so a PM can ask "how stretched is UST 10Y on a 60-day view?" and read the answer with the rolling-stat parameters echoed honestly alongside.

**Version:** v3 (dual-view + Monitor implementation)
**Last reviewed:** 2026-06-11
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_zscore_custom_tool` (generic_runnable; standalone bridge via `/api/v1/rates/detail/zscore-custom` — endpoint pre-existing on the backend)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `rolling_analytics`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — the dual-view contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted for single-tool queries.  Controls strip (curve family + tenor + **Z-Score Window** as a primary control + display lookback + field), top-right Z-Score / Rolling-Window / Instrument cards, KPI strip (current yield, current z, window used, min periods, ddof, observation count), z-score chart with fixed ±1.5σ/±2σ regime bands, stretch-context panel, methodology card threaded from the wire's echoed parameters, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAGs (e.g. *"60d vs 252d z-score on UST 10Y"* or *"z-stretch on UST vs Bund vs Gilt 10Y"*).  Identity chip (country · curve · tenor · window), 3-KPI strip (YIELD / Z-SCORE (Wd) / OBS), z-score sparkline with the same regime bands, wire-threaded caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/ZScoreCustomWidget.tsx`](surfaces/monitor/ZScoreCustomWidget.tsx)) showing one (curve_family × tenor × **z_score_window_days**) stretch read: big signed z with regime tone, yield + observation count, and the shared `ZScoreRegimeSlider`.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### `custom_build_surface` — extended view

The full investigation canvas for ONE custom-window z-score.  A PM reads, in order: (a) the title row identifying curve · tenor + as-of date; (b) the top-right Z-Score card (signed value + Normal/Elevated/Extreme regime), the Rolling-Window card (the window ACTUALLY used, with the YAML-locked min_periods + ddof echoed beside it — these come off the wire, so a YAML change shows up without a frontend release), and the Instrument card; (c) the KPI strip — current yield, current z, window used, min periods, ddof, observation count — so the decomposition "what number, against what window, under what conventions" is auditable on one screen; (d) the z-score history chart with the ±1.5σ/±2σ regime envelope (the chart IS the z series, so the bands are fixed in y-units); (e) the stretch-context panel interpreting the regime against the CHOSEN window (a 60d stretch and a 504d stretch are different desk statements); (f) the methodology card (statistic description from the wire's TimeSeries, window/min_periods/ddof echoes, display-window observation count, field); (g) the lineage footer.  The central decision the canvas supports: *is this point stretched at the horizon I trade?* — and the window control is primary, never buried in Advanced, because it is the tool's A13 central knob.

### `custom_build_surface` — compact view

The at-a-glance grid card.  A PM sees this in a multi-tool prompt — most naturally a WINDOW comparison (same instrument, 60d vs 252d cards side by side) or a cross-market stretch comparison.  They read THREE numbers + a sparkline: latest yield (%), the z-score with the window length IN THE LABEL (so two cards with different windows cannot be misread as the same statistic), and the observation count.  The footer threads the caveat from the wire's echoed parameters (window, min_periods).  The expand arrow opens the extended view in a modal.

### `monitor_surface` tile

One (curve, tenor, window) stretch read, desk-glanceable: signed z (coral/amber/mint regime tone), regime word, the window chip in the header (the differentiator vs the fixed-252d `yield_level` tile and `yield_snapshot` grid), latest yield, and the shared regime slider.  The tile is the trigger for a deeper look in Build when |z| crosses 1.5σ at the pinned horizon.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality.  This tool's defining query shape is intrinsically multi-call — *"compare the 60d and 252d z on the same point"* IS two tool calls — so the compact card earns its keep immediately.  The standard OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (yield + z(window) + obs): "what is the level / how stretched is it at this window / how much data backs the statistic".  Alternatives considered + rejected: daily/weekly changes (NOT on this tool's wire — `get_yield_levels` owns the change decomposition; fabricating them client-side would violate FP9 no-client-compute); percentile (not on the wire); min_periods/ddof as compact cells (parameters echo, not headline — they live in the extended KPI strip + the compact caveat).

**Why the window length appears in the z KPI label** (`Z-SCORE (60D)` not `Z-SCORE`): the whole point of this tool is that the window varies per call.  In a DAG with two cards on the same instrument, an unlabeled z would be actively misleading.  The label threads `z_score_window_days_used` from the wire, not the request param, so what is shown is what was computed.

**Why a Monitor tile** (claimed): zscore stretch is desk-glanceable, and the catalog audit found NO duplication — the only hand-authored widget is the pre-aggregated `yield_snapshot` (fixed 252d z across a grid) and the module-derived `yield_level` tile also pins 252d.  A tile whose param form exposes the window gives the desk a pinned tactical-horizon read neither existing widget can express.  Generic alternative inadequate: there is no parameterised-window path through the existing widgets at all.

**Why min_periods / ddof have NO controls**: the backend Input surface is intentionally narrow (config.yaml A13) — `min_periods`, `ddof`, `buffer_multiplier`, `ffill_limit_days` are YAML-locked.  Exposing dead controls would misrepresent the contract; instead the EFFECTIVE values are echoed on the wire (`z_score_min_periods_used`, `z_score_ddof_used`) and surfaced read-only in the KPI strip + methodology card (P5 honest disclosure).  This deliberately diverges from the breakeven pilot, whose backend DOES expose those overrides.

**Why fixed reference bands** (±1.5σ/±2σ constants, not re-estimated from the displayed window): the chart's y-axis is already in z units, so the regime thresholds from the shared tone family ARE the envelope.  Re-deriving a mean/std of the z series (as level-unit tools must) would draw bands that drift from the regime colouring — an inconsistency a reviewer would rightly flag.

**Why no bespoke Ask card / preview widget**: the generic `AssistantResearchCard` renders a one-series z snapshot adequately; the persisted-artifact preview is well-served by the Series default.  No claim until the desk's read pattern demands per-release framing.

**Why no methodology reference chips**: the backend `config.yaml:methodology.citations` is the empty list — rendering invented citations would violate P5.  The methodology card ships rows only.

---

## 4. What would change the design?

- **Per-window-aware min_periods scaling lands** (config.yaml `planned_extensions`: min_periods = window // 4 instead of fixed 60, or a quality-flag warning for small windows) → the compact caveat + extended Window card gain a warm-up-quality indicator; the wire echo fields already carry what is needed, so it is copy-only.
- **A cross-sectional / robust (median+MAD) / expanding-window z-score ships** → per the YAML those are SIBLING TOOLS, not parameters; each gets its own module.  This module's extended view could then offer "open as robust z" cross-links, mirroring the scanner's Open-in-Build pattern.
- **Percentile or change fields land on the wire** → the compact KPI triple is re-debated (z + percentile is the breakeven precedent); THESIS Q3 rewritten.
- **The desk stops using non-252d windows** (Monitor telemetry shows the tile pinned only at 252d) → drop `monitor_surface` per FM4 and point users at the existing `yield_level` tile; the Build dual-view stays.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden by `rendering_density.md §1.2` for the dual-view mandate; the Monitor claim is justified by the no-duplication audit in Q3.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` kept from the Stage-3 scaffold (already PM-facing).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; files at canonical paths; `monitorWidgets[]` carries the tile.
- **FM9** (routing-claim disclosure) — `typedView: null`; this module owns its own full surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- **P5** (honest disclosure) — every methodology row threads the wire's echoed parameters (`z_score_window_days_used` / `z_score_min_periods_used` / `z_score_ddof_used`, the canonical TimeSeries `description` + `units`); nothing is hardcoded that the YAML could silently change.
- **FP9** (no client-side compute) — the z-score, its parameters and the observation count are the backend's; the frontend renders, never recomputes.
- **A13 central-knob doctrine** (`docs/architecture/tool_architecture.md`, mirrored in the tool's `config.yaml`) — the window control is primary; YAML-locked ancillaries get no controls.
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): typed-detail endpoint `/api/v1/rates/detail/zscore-custom` (pre-existing); own service helper `fetchDetailZscoreCustom`; own frontend type `ZscoreCustomOutput`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both wired in `module.ts.surfaces` + files at canonical paths.
- Backend wire-contract note: the output carries BOTH `time_series` (legacy V1 name) and `time_series_zscore` (canonical alias, identical payload — the naming fix that unblocked the workflow_router bridge lift).  The surfaces prefer the canonical field and fall back to the legacy one.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Dual-view + Monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/monitor/ZScoreCustomWidget.tsx`, and the shared helper `surfaces/zscoreCustomShared.ts`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`.  Consumes the pre-existing standalone-bridge endpoint `/api/v1/rates/detail/zscore-custom`.  Brought to parity with `calculate_breakeven_inflation_simple_tool`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
