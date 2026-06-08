# THESIS — `calculate_ois_forward_rate_tool`

> Dual-view + monitor module under the new dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) AND a Monitor bento tile — all REQUIRED per [`docs_revamped/03_standards/rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (factory dual-view implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_ois_forward_rate_tool` (generic_runnable; backend `_PRIMITIVE_SPECS` entry; standalone bridge via `/api/v1/rates/detail/ois-forward-rate`)
**Backend MCP function:** `calculate_ois_forward_rate_tool` (folder name === MCP function name — no alias bridge needed)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `forward_rate`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — capability tier with the **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link).  Identity row (e.g. *SOFR 5Y5Y FORWARD*), controls strip (OIS Curve / Forward / Lookback / Field), 9-cell KPI strip, top-right Z-Score / Percentile / Central-Bank-Context cards, main chart with z-score band overlays, stretch-context panel, methodology card, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations (e.g. *"compare USD_SOFR_OIS 1Y1Y vs 5Y5Y forwards"*).  3-KPI strip (FORWARD RATE / 1D CHANGE / Z-SCORE), mini-chart with ±2σ z-score bands + terminal dot, per-family central-bank caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/OisForwardRateWidget.tsx`](surfaces/monitor/OisForwardRateWidget.tsx)) showing one OIS implied forward (curve_family × forward_pair × lookback parameterised; defaults USD_SOFR_OIS / 5Y5Y / 252).  OIS forwards meet the eligibility rule per `surface_contract.md §3.4` — desk-glanceable, parked on morning-briefing boards as the secular implied-policy-path anchor.  Inherently compact per `rendering_density.md §8`; no extended Monitor variant.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE OIS forward observation.  They read, in order: (a) the title row identifying *which* curve_family + forward_pair + as-of date (the backend's `forward_label` like "SOFR 5Y5Y" is the canonical naming); (b) the top-right Z-Score (252D) / Percentile (252D) / Central-Bank-Context cards for an at-a-glance "is this stretched? whose policy path?" read; (c) the 9-cell KPI strip — FORWARD RATE / 1D CHANGE / START SPOT / END SPOT / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS — for the full numeric picture; (d) the main chart with z-score band overlays to see the historical context for the current observation; (e) the stretch-context panel for an interpretation paragraph framing the move as hawkish/dovish repricing of the implied policy path; (f) the methodology card with the forward formula (dual-compounding bootstrap) + sign convention (POSITIVE = forward sits above near pillar) + the per-family central-bank disclosure ("risk-neutral implied policy path anchored to {centralBank}'s {indexShort} overnight reference"); (g) the lineage footer to confirm freshness + provider chain.  The controls strip lets them swap curve_family / forward_pair (1Y1Y / 2Y1Y / 3Y2Y / 5Y5Y / 10Y10Y) / lookback / field — z-score conventions are YAML-locked on this primitive (no "Advanced" override panel — mirrors the OIS rate_level / curve_spread / butterfly siblings).

### Compact Build view

The at-a-glance grid card.  A PM sees this when they ask a multi-tool prompt that contains this primitive among others.  They read THREE numbers + a sparkline: forward rate (in %, 3-decimal precision matching the desk's OIS quote convention), 1-day change (in bps + percent subtext, tone-coloured POSITIVE = hawkish forward repricing / NEGATIVE = dovish), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The footer surfaces the per-family central-bank caveat ("Federal Reserve · OIS forward (risk-neutral implied policy path)") inline.  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

### Monitor tile

A desk-glanceable bento tile.  At a glance: index short label (SOFR / ESTR / SONIA / TONA / AONIA / CORRA) + forward_pair (5Y5Y / 1Y1Y / ...) + flag in the kicker; the implied forward rate in large mono type; a 1d Δ chip toned for hawkish (+) / dovish (−); a percentile chip; a 252d low/high range strip with a current-position marker.  The provenance footer shows the as-of date so the user can confirm freshness without opening the widget config.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (forward rate + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off an OIS forward snapshot.  Alternatives considered + rejected: 252d percentile (already visible via the z-score regime + band overlay), start/end spot rates (context, not headline data — the FORWARD is the headline; spots are the legs that bootstrap into it), 252d high/low (range context, not headline data).  The chosen three answer "where's the implied forward now / how much did it move today / is this stretched?" in one glance.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/ois-forward-rate`) instead of reusing `/api/v1/tools/{name}/run`: per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every new standalone module ships its own typed bridge.  The generic `/run` route is the LLM-facing surface; the frontend's typed consumption is a separate concern with its own typed contract.

**Why a per-tool family registry** (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) instead of reusing the shared `countryCaveatFor` helper: the shared registry covers the LINKER domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) only — OIS curve families are a disjoint universe.  Mirrors the sibling get_ois_rate_level_tool / calculate_ois_curve_spread_tool / calculate_ois_butterfly_tool per-tool registry pattern.

**Why a forward-pair pill selector** (1Y1Y / 2Y1Y / 3Y2Y / 5Y5Y / 10Y10Y) instead of two separate tenor dropdowns: the backend's tenor-mode input requires `start_tenor` + `end_tenor` together (the schema rejects partial / both modes), AND the desk speaks canonical forward shorthand (5Y5Y, 1Y1Y).  Surfacing the pair as a single selection mirrors the desk vocabulary AND removes the "did you pick the right end_tenor for your start_tenor?" foot-gun.  The selector pre-populates `start_tenor` + `end_tenor` simultaneously on change so the URL state captures the exact backend params.

**Why NOT a date-pair input mode** (yet): the backend supports `(start_date, end_date)` ad-hoc forwards but the desk's primary read pattern is the tenor-mode canonical forwards.  Date-mode is reachable via the typed-detail endpoint (and via the generic Build builder, since the MCP wrapper accepts both); surfacing it as a controls-strip mode would double the controls width with low desk value.  Reachable in V2 if a workflow lands that requires it.

**Why NO z-score override controls** (in contrast to the linker `get_real_yield_level_tool` reference): the OIS forward_rate primitive's `OISForwardRateInput` intentionally does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` — these are YAML-locked at compute() time only.  Mirrors the OIS rate_level / curve_spread / butterfly siblings.  Adding an Advanced panel here would imply the controls do something they don't.

**Why NOT a bespoke Ask card** (yet): the generic `AssistantResearchCard` (KPIs + sparkline + provenance) covers the chat-result framing for *"what's SOFR 5Y5Y?"* adequately.  A bespoke ask card would be claimed only if a domain-specific framing (e.g. forward + policy-path decomposition language inline in chat) becomes desk-canonical.

---

## 4. What would change the design?

- **A backend `methodology_label` field** lands on `OISForwardRateCurrentMetrics` → swap the methodology card's "Disclosure" row from the per-family hand-rolled string to the wire field.  Today the OIS family of primitives uniformly lacks `methodology_label` (mirrors the OIS rate_level / curve_spread / butterfly siblings); when one ships the change propagates here in a one-line edit.
- **Backend `weekly_change_bps` + `monthly_change_bps` fields** land on `OISForwardRateCurrentMetrics` (documented as a deferred planned_extension in [`forward_rate/config.yaml`](../../../../../rates_agent/ois/tools/forward_rate/config.yaml)) → swap the START SPOT / END SPOT KPI cells in `extendedKPIs` for 5D CHANGE / 1M CHANGE cells, matching the OIS rate_level extended-strip ordering and the mockup design.  Today's KPI substitution is documented in `oisForwardRateShared.ts` directly above the `extendedKPIs` builder.
- **A new OIS curve_family** (e.g. NZD_OIS, CHF_OIS) lands in the backend universe → add the curve_family code to `FAMILY_REGISTRY` in [`surfaces/oisForwardRateShared.ts`](surfaces/oisForwardRateShared.ts).  No shell changes.
- **A new canonical forward pair** (e.g. 2Y3Y, 7Y3Y) becomes desk-canonical → append it to `OIS_FORWARD_PAIRS` in [`surfaces/oisForwardRateShared.ts`](surfaces/oisForwardRateShared.ts) and it auto-shows up in the Build controls strip AND the Monitor widget config form.
- **A workflow template composing OIS forward with the bond-implied breakeven primitive** (the "real / nominal / implied-policy" four-quadrant view) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.
- **Date-pair forward mode** (start_date, end_date — ad-hoc windows aligned to central-bank meetings) becomes desk-canonical → add a tabbed selector to the controls strip toggling between tenor-mode pill set and a date-range picker; the backend accepts both today.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name (`calculate_ois_forward_rate_tool`) equals backend `tool_name` exactly.  No alias bridge needed; the MCP function + workflow registry + folder all align (distinct from `get_ois_rate_level_tool` which uses the get-verb form).
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`; per the rendering-density standard the second REQUIRES both `buildExtended` + `buildCompact` files.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.  Other capability tiers (preview / ask) NOT claimed for V1 per FM4 parsimony.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry + ToolCard.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; corresponding files at canonical paths; Monitor widget at `surfaces/monitor/OisForwardRateWidget.tsx`.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern; this module owns its own full Build surfaces (no shared shared typed view).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the project's **methodology-exposure standalone-bridge contract** ([`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md)): own typed-detail endpoint at `/api/v1/rates/detail/ois-forward-rate`; own service helper `fetchDetailOisForwardRate`; own frontend type `OisForwardRateOutput`; no reuse of sovereign-yield or OIS rate-level shells.
- Per the **rendering-density dual-view contract** ([`rendering_density.md §1`](../../../../../docs_revamped/03_standards/rendering_density.md)): both views ship + both are wired in `module.ts.surfaces` + corresponding files at canonical paths.

---

## Mockup-first design workflow

This module was designed mockup-first per the documented workflow: the design intent for both views was captured as PNG mockups committed to [`./mockups/`](./mockups/) BEFORE implementation began.  Future maintainers reading this folder cold can compare the rendered surfaces against the mockup images for visual-fidelity regression checks.

**Known mockup/backend deviation** (documented in `extendedKPIs` in [`surfaces/oisForwardRateShared.ts`](surfaces/oisForwardRateShared.ts)): the Extended mockup's 9-cell KPI strip shows "5D CHANGE" and "1M CHANGE" cells.  The backend's `OISForwardRateCurrentMetrics` Pydantic schema does NOT expose `weekly_change_bps` / `monthly_change_bps` in V1 — config.yaml's `methodology.planned_extensions` documents this as a deferred follow-up PR (period_offsets convention block).  We substitute START SPOT and END SPOT cells (`start_spot_rate_pct` / `end_spot_rate_pct` interpolated par rates at the forward-window endpoints) — these are the natural contextual reads for an OIS forward (the forward IS the bootstrap-implied rate that ties the two spots together) and are both exposed on the backend wire today.  When the backend lands the period-changes extension, this builder swaps in the 5D / 1M cells in their canonical ordering.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-07 | Frontend factory dual-view + monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/oisForwardRateShared.ts`, and `surfaces/monitor/OisForwardRateWidget.tsx`.  Tier set extended to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/ois-forward-rate`.  Per-family OIS metadata registry + canonical forward-pair grid (FAMILY_REGISTRY + OIS_FORWARD_PAIRS) live in `oisForwardRateShared.ts`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
