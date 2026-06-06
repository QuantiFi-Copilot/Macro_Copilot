# THESIS — `get_ois_rate_level_tool`

> Dual-view + monitor module under the new dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) AND a Monitor bento tile — all REQUIRED per [`docs_revamped/03_standards/rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (factory dual-view implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_ois_rate_level_tool` (generic_runnable; backend `_PRIMITIVE_SPECS` entry; standalone bridge via `/api/v1/rates/detail/ois-rate-level`)
**Backend MCP function:** `calculate_ois_rate_level_tool` (verb-mismatched manifest name bridged via `KNOWN_TOOL_ALIASES` in [`src/lib/toolNames.ts`](../../../lib/toolNames.ts))
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `snapshots`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — capability tier with the **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link).  Identity row, controls strip (OIS Curve / Tenor / Lookback / Field), 9-cell KPI strip, top-right Z-Score / Percentile / Central-Bank-Context cards, main chart with z-score band overlays, stretch-context panel, methodology card, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations (e.g. *"compare USD_SOFR_OIS 2Y vs EUR_ESTR_OIS 2Y vs GBP_SONIA_OIS 2Y"*).  3-KPI strip (RATE / 1D CHANGE / Z-SCORE), mini-chart with ±2σ z-score bands + terminal dot, per-family central-bank caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/OisRateLevelWidget.tsx`](surfaces/monitor/OisRateLevelWidget.tsx)) showing one OIS rate level (curve_family × tenor × lookback parameterised; defaults USD_SOFR_OIS / 2Y / 252).  OIS rate levels meet the eligibility rule per `surface_contract.md §3.4` — desk-glanceable, parked on morning-briefing boards as the front-end policy-path read.  Inherently compact per `rendering_density.md §8`; no extended Monitor variant.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE OIS rate observation.  They read, in order: (a) the title row identifying *which* curve_family + tenor + as-of date; (b) the top-right Z-Score (252D) / Percentile (252D) / Central-Bank-Context cards for an at-a-glance "is this stretched? whose policy path?" read; (c) the 9-cell KPI strip for the full numeric picture (current rate + 1d/5d/1m changes in bps + z-score + percentile + 252d high/low + observations); (d) the main chart with z-score band overlays to see the historical context for the current observation; (e) the stretch-context panel for an interpretation paragraph framing the move as hawkish/dovish repricing of the implied policy path; (f) the methodology card with the per-family central-bank disclosure ("risk-neutral implied policy path anchored to {centralBank}'s {indexShort} overnight reference"); (g) the lineage footer to confirm freshness + provider chain.  The controls strip lets them swap curve_family / tenor / lookback / field — z-score conventions are YAML-locked on this primitive (no "Advanced" override panel — mirrors the OIS curve_spread / butterfly siblings).

### Compact Build view

The at-a-glance grid card.  A PM sees this when they ask a multi-tool prompt that contains this primitive among others.  They read THREE numbers + a sparkline: current OIS rate (in %, 4-decimal precision matching the 0.25bp tick the desk quotes), 1-day change (in bps + percent subtext, tone-coloured for tightening/easing on the implied policy path), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The footer surfaces the per-family central-bank caveat ("Federal Reserve · OIS (risk-neutral implied policy path)") inline.  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

### Monitor tile

A desk-glanceable bento tile.  At a glance: index short label (SOFR / ESTR / SONIA / TONA / AONIA / CORRA) + tenor + flag in the kicker; the par-swap rate in large mono type; a 1d Δ chip toned for tightening (+) / easing (−); a percentile chip; a 252d low/high range strip with a current-position marker.  The provenance footer shows the as-of date so the user can confirm freshness without opening the widget config.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (current OIS rate + 1d change + z-score): they're the desk-canonical "first three numbers" a PM reads off an OIS rate-level snapshot.  Alternatives considered + rejected: 252d percentile (already visible via the z-score regime + band overlay), weekly / monthly change (lower-frequency reads; the 1d change is the actionable signal for policy-path repricing), 252d high/low (range context, not headline data).  The chosen three answer "where's the OIS rate now / how much did it move today / is this stretched?" in one glance.

**Why a typed-detail endpoint** (`/api/v1/rates/detail/ois-rate-level`) instead of reusing `/api/v1/tools/{name}/run`: per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every new standalone module ships its own typed bridge.  The generic `/run` route is the LLM-facing surface; the frontend's typed consumption is a separate concern with its own typed contract.

**Why a per-tool family registry** (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS / CAD_OIS) instead of reusing the shared `countryCaveatFor` helper: the shared registry covers the LINKER domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) only — OIS curve families are a disjoint universe.  Mirrors the sibling `calculate_ois_curve_spread_tool` per-tool registry pattern.

**Why NO z-score override controls** (in contrast to the linker `get_real_yield_level_tool` reference): the OIS rate_level primitive's `OISRateLevelInput` intentionally does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` — these are YAML-locked at compute() time only.  Mirrors the OIS curve_spread / butterfly siblings.  Adding an Advanced panel here would imply the controls do something they don't.

**Why NOT a bespoke Ask card** (yet): the generic `AssistantResearchCard` (KPIs + sparkline + provenance) covers the chat-result framing for *"where's SOFR 2Y OIS?"* adequately.  A bespoke ask card would be claimed only if a domain-specific framing (e.g. OIS rate + policy-path decomposition language inline in chat) becomes desk-canonical.

---

## 4. What would change the design?

- **A backend `methodology_label` field** lands on `OISRateLevelMetrics` → swap the methodology card's "Disclosure" row from the per-family hand-rolled string to the wire field.  Today the OIS family of primitives uniformly lacks `methodology_label` (mirrors the OIS curve_spread / butterfly siblings); when one ships the change propagates here in a one-line edit.
- **A new OIS curve_family** (e.g. NZD_OIS, CHF_OIS) lands in the backend universe → add the curve_family code to `FAMILY_REGISTRY` in [`surfaces/oisRateLevelShared.ts`](surfaces/oisRateLevelShared.ts) and the tenor set to `OIS_TENOR_OPTIONS_BY_CURVE`.  No shell changes.
- **A workflow template composing OIS rate-level with the bond-implied breakeven primitive** (the "real / nominal / implied-policy" four-quadrant view) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.
- **The desk decides PX_BID / PX_ASK exposure is unnecessary** → flip `default_swap_rate_field.exposure.expose` in [`config.yaml`](../../../../../rates_agent/ois/tools/rate_level/config.yaml) to `false` for those fields + drop them from the `FIELD_OPTIONS` list.
- **Backend Output schema gains a per-day z-score series** (instead of a single rolling z) → the main chart can overlay the z-score series; the per-day tone-coloring matures.  The chart shell already accepts the data; only the per-tool wrapper changes.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name (`get_ois_rate_level_tool`) equals backend `tool_name` exactly.  The verb-mismatched manifest form `calculate_ois_rate_level_tool` is bridged in [`KNOWN_TOOL_ALIASES`](../../../lib/toolNames.ts) so every Build entry path normalises before lookup.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`; per the rendering-density standard the second REQUIRES both `buildExtended` + `buildCompact` files.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.  Other capability tiers (preview / ask) NOT claimed for V1 per FM4 parsimony.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry + ToolCard.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; corresponding files at canonical paths; Monitor widget at `surfaces/monitor/OisRateLevelWidget.tsx`.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern; this module owns its own full Build surfaces (no shared shared typed view).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts).
- Per the project's **methodology-exposure standalone-bridge contract** ([`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md)): own typed-detail endpoint at `/api/v1/rates/detail/ois-rate-level`; own service helper `fetchDetailOisRateLevel`; own frontend type `OisRateLevelOutput`; no reuse of sovereign-yield shells.
- Per the **rendering-density dual-view contract** ([`rendering_density.md §1`](../../../../../docs_revamped/03_standards/rendering_density.md)): both views ship + both are wired in `module.ts.surfaces` + corresponding files at canonical paths.

---

## Mockup-first design workflow

This module was designed mockup-first per the documented workflow: the design intent for both views was captured as PNG mockups committed to [`./mockups/`](./mockups/) BEFORE implementation began.  Future maintainers reading this folder cold can compare the rendered surfaces against the mockup images for visual-fidelity regression checks.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-06 | Frontend factory dual-view + monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/oisRateLevelShared.ts`, and `surfaces/monitor/OisRateLevelWidget.tsx`.  Tier set extended to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/ois-rate-level`.  Per-family OIS metadata registry (FAMILY_REGISTRY) lives in `oisRateLevelShared.ts` — mirrors the sibling OIS curve_spread tool. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
