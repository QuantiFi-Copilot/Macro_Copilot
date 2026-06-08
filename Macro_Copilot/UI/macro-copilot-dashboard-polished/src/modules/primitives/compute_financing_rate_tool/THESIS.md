# THESIS — `compute_financing_rate_tool`

> Frontend factory module under the dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`docs_revamped/03_standards/rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.  FIRST-OF-ITS-KIND architectural deviation in the factory: route-side synthesis of the snapshot shape from a Panel-only backend Output (see §"Backend shape note").

**Version:** v3 (factory dual-view implementation w/ route-side synthesis)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `compute_financing_rate_tool` (generic_runnable; Panel-shaped Output via `_PRIMITIVE_SPECS`; route-side synthesized snapshot view via `/api/v1/rates/detail/financing-rate`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png)

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — capability tier with the **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link).  Full controls strip (method / proxy_curve / lookback), 9-cell KPI strip (rate + 1d/5d/1m changes + z-score + percentile + 252d high/low + observations), main chart with ±2σ z-score bands, stretch-context panel, methodology card, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations.  3-KPI headline (financing rate · 1d change · z-score), mini-chart with reference bands, one-line caveat footer, click-to-expand affordance opening the extended view in a modal/drawer.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/FinancingRateWidget.tsx`](surfaces/monitor/FinancingRateWidget.tsx)) showing one OIS-implied financing rate (proxy_curve × lookback parameterised; defaults USD_SOFR_OIS / 252).  Financing meets the eligibility rule per `surface_contract.md §3.4` — desk-canonical daily PM read for repo / specials dislocations.  Inherently compact per `rendering_density.md §8`.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE financing-rate observation.  They read, in order: (a) the title row identifying *which* proxy curve (e.g. USD_SOFR_OIS proxies UST financing) + as-of date; (b) the top-right Z-Score (252D) / Percentile (252D) / Method cards for an at-a-glance "is this stretched? which OIS proxy?" read; (c) the 9-cell KPI strip (financing rate %, 1d/5d/1m changes in bps + percent subtext, z-score + regime caption, percentile + bucket, 252d high/low %, observations count); (d) the main chart with ±2σ z-score band overlays to see historical context; (e) the stretch-context panel for an interpretation paragraph; (f) the methodology card carrying the backend-joined disclosure (sources: method, proxy curve, z-score model, caveat — "OIS proxy approximates true overnight repo; does NOT reflect CUSIP-level specials, GC scarcity, or term-repo basis"); (g) the lineage footer for freshness + provider chain.  The controls strip lets them swap method / proxy_curve / lookback.

### Compact Build view

The at-a-glance grid card.  A PM sees this when they ask a multi-tool prompt that contains this primitive among others.  They read THREE numbers + a sparkline: current financing rate (in %), 1-day change (in bps + percent subtext, tone-coloured for tightening/easing), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The footer surfaces the first sentence of the backend-joined methodology disclosure inline.  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.

**Why these three compact KPIs** (financing rate + 1d change + z-score): they're the desk-canonical "first three numbers" for a financing-rate read.  Alternatives considered + rejected: 252d percentile (already visible via the z-score regime + band overlay), weekly / monthly change (lower-frequency reads; the 1d change is the actionable signal), 252d high/low (range context, not headline data).  The chosen three answer "where is financing now / how much did it move today / is this stretched?" in one glance.

**Why a Monitor tile** (claimed at V1): financing rate is one of the canonical daily desk reads for repo / specials drift; the Monitor bento is the right home for it (mirrors the linker `get_real_yield_level_tool` decision).

**Why a typed-detail endpoint** (`/api/v1/rates/detail/financing-rate`) instead of reusing `/api/v1/tools/{name}/run`: per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every new standalone module ships its own typed bridge.  The generic `/run` route is the LLM-facing surface; the frontend's typed consumption is a separate concern with its own typed contract.  **For this tool the typed bridge is load-bearing for a second reason — see §"Backend shape note".**

---

## 4. What would change the design?

- **TD #29 (term GC repo) lands** → unblocks `method=term_repo_curve`; the controls strip gains a new method option + a tenor-pair param-form section.
- **TD #30 (CUSIP-level repo specials) lands** → unblocks `method=gc_special_blend`; the methodology card gains a specials-premium subsection so the desk can audit financing-rate sensitivity to specials.
- **A workflow template using this primitive** (e.g. a carry-attribution backtest) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required.
- **A new OIS market** lands in the backend universe → add the proxy_curve code to `PROXY_CURVE_REGISTRY` in [`surfaces/financingRateShared.ts`](surfaces/financingRateShared.ts) (one-line entry: bond-family label + flag + short-label).  No shell changes.
- **The backend `FinancingRateOutput` is refactored to carry `current_metrics` + `time_series` natively** → the route-side synthesis collapses to a passthrough; the frontend module is structurally unchanged because the deviation lives behind the typed-detail boundary.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; corresponding files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint at `/api/v1/rates/detail/financing-rate`; own service helper `fetchDetailFinancingRate`; own frontend type `FinancingRateDetailResponse`.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both are wired in `module.ts.surfaces`.

---

## Backend shape note

This module is the **first** in the factory to use **route-side synthesis** of the snapshot shape.  Documented honestly because it sets a precedent for future Panel-shaped tools.

**Why FinancingRateOutput diverges.**  [`rates_agent/ois/tools/financing_rate/schemas.py`](../../../../../rates_agent/ois/tools/financing_rate/schemas.py) lines 148-187 declare `FinancingRateOutput` with:

```
method, as_of_start, as_of_end, n_observations, mean_rate_pct (scalar),
methodology_disclosures (List[str], PLURAL), panel (Optional[Panel])
```

It does NOT carry `current_metrics` + `time_series` like every other snapshot tool.  The Panel-based shape is load-bearing for the `evaluate_trades` workflow consumer: financing rate feeds carry-of-financing P&L into a backtest, and the Panel artifact carries per-day rates + units + lineage in the exact shape `tool_output_to_artifact_panel` consumes.  The MCP layer drops `panel` before LLM serialisation (token budget), but the route handler in `api/routes/rates/detail.py` accesses it directly.

**Where the synthesis happens.**  In the route, NOT on the backend.  The handler at `/api/v1/rates/detail/financing-rate`:

1. Calls `compute_financing_rate(engine, params, config)` directly.
2. Reads `result['panel'].payload` as a `pd.DataFrame` (single-column daily rates in PERCENT).
3. Reduces the column to a `pd.Series`; computes `latest`, `daily_change_bps` (delta × 100), `weekly_change_bps` (5-trading-day delta × 100), `monthly_change_bps` (22-trading-day delta × 100), trims to the display window, computes mean/std → `z_score`, `max/min → high_252d_pct/low_252d_pct`, percentile rank.
4. Joins `methodology_disclosures: List[str]` (PLURAL) into a single `methodology_disclosure` string.
5. Returns a `FinancingRateDetailResponse` — the shape the frontend consumes.

This preserves backend compatibility with `evaluate_trades` (Output stays as-is) while letting the frontend module follow the standard snapshot-tool pattern.

**Why the frontend is structurally identical to other snapshots.**  The TS types (`FinancingRateCurrentMetrics`, `FinancingRateTimeSeriesRow`, `FinancingRateDetailResponse` in [`src/types/rates.ts`](../../../../types/rates.ts)) mirror the SYNTHESIZED shape, NOT the backend Output.  Above the typed-detail boundary — in `BuildExtended.tsx`, `BuildCompact.tsx`, `surfaces/financingRateShared.ts`, the Monitor tile, and the round-trip test — the architectural deviation is **invisible**.  This file is the only place in the frontend module that describes the deviation; everything else looks like a normal snapshot tool.

**Resolution lineage.**  This pattern was authorised by the human on 2026-06-08 (Option (a)) after the prior wake's `human_required` block flagged the backend-Output / frontend-snapshot-shape mismatch.  Two alternatives were considered + rejected: Option (b) modify `FinancingRateOutput` to add native `current_metrics` + `time_series` (rejected — breaks `evaluate_trades` shape contract), Option (c) ship the frontend module against the Panel shape only (rejected — would diverge from every other snapshot tool's frontend pattern).

## Mockup conformance

Compact.png shows an auxiliary KPI strip beneath the 3 headline KPIs (5D · 1M · 252D PCTL · 252D HIGH · 252D LOW · OBSERVATIONS).  Per the BuildCompactShell §2.2 contract (enforces exactly 3 headline KPIs), the auxiliary strip is collapsed into the Extended view's 9-cell strip — same data, different surface.  Precedent: Batch 1 fdac7d2 Option (c).  Rationale: a multi-tool DAG card with 9 KPIs visually competes with sibling tool cards; the headline 3 + expand-affordance to extended is the consistent multi-tool pattern across the factory.  No data is hidden — every Compact.png datum is reachable via expand.

Extended.png shows the full canvas this module renders: identity row with proxy-curve + bond-family + as-of, top-right Z-score / Percentile / Method cards, controls strip, 9-cell KPI strip, main chart with z-score bands, stretch context, methodology card, lineage footer.  Mockup-faithful.

---

## Mockup-first design workflow

This module was designed mockup-first per the user's documented workflow: the design intent for both views was captured as PNG mockups committed to [`./mockups/`](./mockups/) BEFORE implementation began.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Factory dual-view implementation w/ route-side synthesis (Option (a), human-authorised).  Shipped both `surfaces/BuildExtended.tsx` and `surfaces/BuildCompact.tsx` with shared helper module `surfaces/financingRateShared.ts`, Monitor tile `surfaces/monitor/FinancingRateWidget.tsx`, route-side synthesis at `/api/v1/rates/detail/financing-rate`, frontend type mirror `FinancingRateDetailResponse`.  Tier set updated to `[generic_runnable, custom_build_surface, monitor_surface]`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
