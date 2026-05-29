# THESIS — `get_real_yield_level_tool`

> Phase-1 pilot module under the new dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) — both REQUIRED per [`docs_revamped/03_standards/rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (Phase-1 dual-view implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_real_yield_level_tool` (generic_runnable; backend `_PRIMITIVE_SPECS` entry; standalone bridge via `/api/v1/rates/detail/real_yield`)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) (Build dual-view) — committed alongside the module per the user's mockup-first workflow.  Monitor tile mirrors the sovereign `get_yield_levels_tool` analog visually; no separate mockup needed (Monitor surface is inherently compact per `rendering_density.md §8`).

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — capability tier with the Phase-1 **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link).  Full controls strip, KPI strip with 9 metrics, main chart with z-score bands + time-range chips, stretch-context panel, methodology card, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations (e.g. *"compare TIPS 10Y real yield vs GBP_LINKER 10Y vs EUR_FR_LINKER 10Y"*).  Headline 3-KPI strip, mini-chart with ±2σ z-score bands + terminal dot, country-caveat footer, click-to-expand affordance opening the extended view in a modal/drawer overlay.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/RealYieldLevelWidget.tsx`](surfaces/monitor/RealYieldLevelWidget.tsx)) showing one linker real-yield level (curve_family × tenor × lookback parameterised; defaults USD_TIPS / 10Y / 252).  Real yields meet the eligibility rule per `surface_contract.md §3.4` — desk-glanceable, parked on morning-briefing boards.  Inherently compact per `rendering_density.md §8`; no extended Monitor variant.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE real-yield observation.  They read, in order: (a) the title row identifying *which* curve + tenor + as-of date; (b) the top-right Z-Score (252D) / Percentile (252D) / Country caveat cards for an at-a-glance "is this stretched?" read; (c) the 9-cell KPI strip for the full numeric picture (current real yield + 1d/5d/1m changes in bps + z-score + percentile + 252d high/low + observations); (d) the main chart with z-score band overlays to see the historical context for the current observation; (e) the stretch-context panel for an interpretation paragraph; (f) the methodology card for the per-call convention values + theoretical-reference chips; (g) the lineage footer to confirm freshness + provider chain.  The controls strip lets them swap curve_family / tenor / lookback / field, OR override the rolling-z-score model under "Advanced" (Phase-1 exposed conventions: z_score_window_days / z_score_min_periods / z_score_ddof).

### Compact Build view

The at-a-glance grid card.  A PM sees this when they ask a multi-tool prompt that contains this primitive among others.  They read THREE numbers + a sparkline: current real yield (in %), 1-day change (in bps + percent subtext, tone-coloured for tightening/easing), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The country-caveat footer surfaces the curve_family-specific disclosure inline.  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current real yield + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off a real-yield level snapshot.  Alternatives considered + rejected: 252d percentile (already visible via the z-score regime + band overlay), weekly / monthly change (lower-frequency reads; the 1d change is the actionable signal), 252d high/low (range context, not headline data).  The chosen three answer "where are real yields now / how much did they move today / is this stretched?" in one glance.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/real_yield`) instead of reusing `/api/v1/tools/{name}/run`: per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every new standalone module ships its own typed bridge.  The generic `/run` route is the LLM-facing surface; the frontend's typed consumption is a separate concern with its own typed contract.

**Why NOT a Monitor tile** (yet): planned for a follow-up PR.  Monitor surface needs the RatesDataProvider shared-context wiring + a curve_family / tenor / lookback param-form spec; ships alongside the breakeven_inflation_simple_tool Monitor work for symmetry.  The compact Build view already covers the at-a-glance read for multi-tool DAG contexts.

**Why NOT a bespoke Ask card** (yet): the generic `AssistantResearchCard` (KPIs + sparkline + provenance) covers the chat-result framing for *"where's TIPS 10Y real yield?"* adequately.  A bespoke ask card would be claimed only if a domain-specific framing (e.g. real-yield + breakeven decomposition language inline in chat) becomes desk-canonical.

---

## 4. What would change the design?

- **A typed-detail endpoint for the related linker breakeven primitive** lands → could compose a side-by-side decomposition card in the extended view ("real yield + breakeven = nominal yield"), surfacing the four-quadrant macro framing.  Today the extended view shows real-yield only.
- **A backend Monitor aggregate endpoint** for the multi-country linker snapshot → adds a `monitor_surface` tier claim + a `RealYieldLevelWidget` tile.  Currently planned for a follow-up PR.
- **A workflow template using this primitive** (e.g. inflation-decomposition event_study) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.
- **A new linker market** (e.g. AUS, JPY, SEK) lands in the backend universe → add the curve_family code to `CURVE_OPTIONS` in [`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx) and the tenor set to `TENOR_OPTIONS_BY_CURVE`; add a country-caveat entry to [`@/components/shared/build/lib/countryCaveats.ts`](../../../../components/shared/build/lib/countryCaveats.ts).  No shell changes.
- **Backend output schema gains a per-day z-score series** (instead of a single rolling z) → the main chart can overlay the z-score series; the per-day tone-coloring matures.  The chart shell already accepts the data; only the per-tool wrapper changes.
- **The desk decides ddof exposure is unnecessary** → flip [`config.yaml:z_score_ddof.exposure.expose`](../../../../../rates_agent/inflation_indexed_bonds/tools/real_yield_level/config.yaml) to `false` + remove the field from `RealYieldLevelInput` + drop the control from the Advanced section.  Per the exposure-decision protocol this is a breaking change requiring schema migration + parity-fixture regeneration.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface`; per the rendering-density standard the latter REQUIRES both `buildExtended` + `buildCompact` files.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.  Other capability tiers (preview / monitor / ask) NOT claimed for V1 per FM4 parsimony.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry + ToolCard.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; corresponding files at canonical paths.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern; this module owns its own full Build surfaces (no shared shared typed view).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the project's **methodology-exposure standalone-bridge contract** (`methodology_exposure.md §5`): own typed-detail endpoint at `/api/v1/rates/detail/real_yield`; own service helper `fetchDetailRealYield`; own frontend type `RealYieldLevelOutput`; no reuse of sovereign-yield shells.
- Per the **rendering-density dual-view contract** (`rendering_density.md §1`): both views ship + both are wired in `module.ts.surfaces` + corresponding files at canonical paths.

---

## Mockup-first design workflow

This module was designed mockup-first per the user's documented workflow: the design intent for both views was captured as PNG mockups committed to [`./mockups/`](./mockups/) BEFORE implementation began.  Future maintainers reading this folder cold can compare the rendered surfaces against the mockup images for visual-fidelity regression checks, and future tool authors can use the same workflow (place mockups → AI agent implements per the rendering-density contract).

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-28 | Phase-1 dual-view implementation per `rendering_density.md`.  Shipped both `surfaces/BuildExtended.tsx` (full canvas) and `surfaces/BuildCompact.tsx` (grid card) with shared helper module `surfaces/realYieldShared.ts` (single data hook + KPI/methodology builders used by both views).  Tier set updated to `[generic_runnable, custom_build_surface]`.  Per-tool helpers + the per-curve_family country caveat registry land in `@/components/shared/build/` (finance-blind shared infrastructure used by future tools).  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/real_yield`. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
