# THESIS — `calculate_inflation_swap_rate_level_tool`

> Dual-view + monitor module under the new dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) AND a Monitor bento tile — all REQUIRED per [`docs_revamped/03_standards/rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (factory dual-view implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_inflation_swap_rate_level_tool` (generic_runnable; backend `_PRIMITIVE_SPECS` entry; standalone bridge via `/api/v1/rates/detail/inflation-swap-rate-level`)
**Backend MCP function:** `calculate_inflation_swap_rate_level_tool` (folder name === MCP function name — no alias bridge required)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships this primitive in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — capability tier with the **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link).  Identity row (`<market> ZCIS <tenor> · <reference index>` — e.g. `EUR ZCIS 5Y · HICPxT`), controls strip (ZCIS Curve / Tenor / Lookback / Field), 9-cell KPI strip, top-right Z-Score / Percentile / Index-Family cards, main chart with z-score band overlays, stretch-context panel, methodology card (wire `methodology_label` plus the load-bearing `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index` rows), lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations (e.g. *"compare USD_ZCIS 5Y vs EUR_ZCIS 5Y vs GBP_ZCIS 5Y forward inflation compensation"*).  3-KPI strip (RATE / 1D CHANGE / Z-SCORE), mini-chart with ±2σ z-score bands + terminal dot, per-family reference-index caveat footer (`HICPxT · OTC ZCIS rate (risk-neutral implied inflation)`), click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/InflationSwapRateLevelWidget.tsx`](surfaces/monitor/InflationSwapRateLevelWidget.tsx)) showing one ZCIS rate level (curve_family × tenor × lookback parameterised; defaults EUR_ZCIS / 5Y / 252).  ZCIS rates meet the eligibility rule per `surface_contract.md §3.4` — desk-glanceable, parked on morning-briefing boards as the cleanest market read on forward inflation expectations.  Inherently compact per `rendering_density.md §8`; no extended Monitor variant.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE ZCIS rate observation.  They read, in order: (a) the title row identifying *which* curve_family + tenor + reference index + as-of date; (b) the top-right Z-Score (252D) / Percentile (252D) / Index-Family cards for an at-a-glance "is this stretched? against what underlying inflation reference?" read; (c) the 9-cell KPI strip for the full numeric picture (current rate + 1d/5d/1m changes in bps + z-score + percentile + 252d high/low + observations); (d) the main chart with z-score band overlays to see the historical context for the current observation; (e) the stretch-context panel for an interpretation paragraph framing the move as hawkish/dovish repricing of implied inflation compensation; (f) the methodology card with the wire `methodology_label` PLUS the load-bearing `inflation_index_family` / `index_lag` / `interpolation` / `underlying_index` rows so the cross-curve comparability caveat is visible inline; (g) the lineage footer to confirm freshness + provider chain.  The controls strip lets them swap curve_family / tenor / lookback / field — z-score conventions are YAML-locked on this primitive (no "Advanced" override panel — mirrors the OIS rate_level sibling).

### Compact Build view

The at-a-glance grid card.  A PM sees this when they ask a multi-tool prompt that contains this primitive among others.  They read THREE numbers + a sparkline: current ZCIS rate (in %, 3-decimal precision matching the desk-quoted half-bp tick), 1-day change (in bps + percent subtext, tone-coloured for the implied inflation compensation move — positive = hawkish repricing = coral), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The footer surfaces the per-family reference-index caveat inline (`HICPxT · OTC ZCIS rate (risk-neutral implied inflation)`).  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

### Monitor tile

A desk-glanceable bento tile.  At a glance: reference-index short label (CPI-U / HICPxT / RPI) + tenor + flag in the kicker; the ZCIS par rate in large mono type; a 1d Δ chip toned for hawkish (+) / dovish (−); a percentile chip; a 252d low/high range strip with a current-position marker.  The provenance footer shows the as-of date so the user can confirm freshness without opening the widget config.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current ZCIS rate + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a ZCIS rate-level snapshot.  Alternatives considered + rejected: 252d percentile (already visible via the z-score regime + band overlay), weekly / monthly change (lower-frequency reads; the 1d change is the actionable signal for inflation-compensation repricing), 252d high/low (range context, not headline data).  The chosen three answer "where's the ZCIS rate now / how much did it move today / is this stretched?" in one glance — mirrors the OIS rate_level sibling's choice for the same reason.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/inflation-swap-rate-level`) instead of reusing `/api/v1/tools/{name}/run`: per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every new standalone module ships its own typed bridge.  The generic `/run` route is the LLM-facing surface; the frontend's typed consumption is a separate concern with its own typed contract.

**Why a per-tool ZCIS family registry** (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) instead of reusing the shared `countryCaveatFor` helper: the shared registry covers the LINKER domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) only — ZCIS curve families are a disjoint universe with their own `inflation_index_family` / `index_lag` / `interpolation` triple that varies per family (CPI-U / 3M / Daily vs HICPxT / 3M / Monthly vs RPI / 2M / Monthly).  Mirrors the OIS rate_level + sibling per-tool registry pattern.

**Why NO z-score override controls** (in contrast to the linker `get_real_yield_level_tool` reference): the ZCIS rate_level primitive's `InflationSwapRateLevelInput` intentionally does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` — these are YAML-locked at compute() time only.  Mirrors the OIS rate_level + sibling level tools.  Adding an Advanced panel here would imply the controls do something they don't.

**Why surface the reference-metadata triple on the extended view** (`inflation_index_family` / `index_lag` / `interpolation` / `underlying_index`): per the schema docstring these are LOAD-BEARING for honest interpretation — USD_ZCIS vs EUR_ZCIS at the same tenor is a CPI-U vs HICP differential, NOT a pure cross-country expected-inflation differential.  Surfacing them on the methodology card (and as the top-right index-family card) gives the desk visibility into the comparability caveat without a second tool call.

**Why NOT a bespoke Ask card** (yet): the generic `AssistantResearchCard` (KPIs + sparkline + provenance) covers the chat-result framing for *"where's USD 5Y ZCIS?"* adequately.  A bespoke ask card would be claimed only if a domain-specific framing (e.g. ZCIS rate + index-family decomposition language inline in chat) becomes desk-canonical.

---

## 4. What would change the design?

- **A new ZCIS curve_family** (e.g. JPY_ZCIS, CAD_ZCIS) lands in the backend universe → add the curve_family code to `FAMILY_REGISTRY` in [`surfaces/inflationSwapRateLevelShared.ts`](surfaces/inflationSwapRateLevelShared.ts) and the tenor set to `ZCIS_TENOR_OPTIONS_BY_CURVE`.  No shell changes.
- **A workflow template composing ZCIS rate-level with the bond-implied breakeven primitive** (the "swap vs linker implied inflation" two-quadrant view) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.
- **The desk decides PX_BID / PX_ASK exposure is unnecessary** → drop those entries from the `FIELD_OPTIONS` list in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx).
- **Backend Output schema gains a per-day z-score series** (instead of a single rolling z) → the main chart can overlay the z-score series; the per-day tone-coloring matures.  The chart shell already accepts the data; only the per-tool wrapper changes.
- **`trailing_range_window_days` becomes configurable** (per `methodology.planned_extensions` in config.yaml) → expose it in the controls strip; rename the wire field names in lockstep.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name (`calculate_inflation_swap_rate_level_tool`) equals backend `tool_name` exactly.  No alias bridge required.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`; per the rendering-density standard the second REQUIRES both `buildExtended` + `buildCompact` files.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.  Other capability tiers (preview / ask) NOT claimed for V1 per FM4 parsimony.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry + ToolCard.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; corresponding files at canonical paths; Monitor widget at `surfaces/monitor/InflationSwapRateLevelWidget.tsx`.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern; this module owns its own full Build surfaces (no shared typed view).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the project's **methodology-exposure standalone-bridge contract** ([`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md)): own typed-detail endpoint at `/api/v1/rates/detail/inflation-swap-rate-level`; own service helper `fetchDetailInflationSwapRateLevel`; own frontend type `InflationSwapRateLevelOutput`; no reuse of sovereign / linker / OIS shells.
- Per the **rendering-density dual-view contract** ([`rendering_density.md §1`](../../../../../docs_revamped/03_standards/rendering_density.md)): both views ship + both are wired in `module.ts.surfaces` + corresponding files at canonical paths.
- **P5 / PR10 methodology-label threading**: the methodology card's `Disclosure` row sources from `current_metrics.methodology_label` (the wire field threaded from `config.yaml:methodology.what_it_does`).  NEVER a hardcoded TS literal — a YAML edit flows through to runtime.

---

## Mockup-first design workflow

This module was designed mockup-first per the documented workflow: the design intent for both views was captured as PNG mockups committed to [`./mockups/`](./mockups/) BEFORE implementation began.  Future maintainers reading this folder cold can compare the rendered surfaces against the mockup images for visual-fidelity regression checks.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-06 | Frontend factory dual-view + monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/inflationSwapRateLevelShared.ts`, and `surfaces/monitor/InflationSwapRateLevelWidget.tsx`.  Tier set extended to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/inflation-swap-rate-level`.  Per-family ZCIS metadata registry (FAMILY_REGISTRY) lives in `inflationSwapRateLevelShared.ts`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
