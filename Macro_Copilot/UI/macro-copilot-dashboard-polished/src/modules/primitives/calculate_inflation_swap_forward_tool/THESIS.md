# THESIS — `calculate_inflation_swap_forward_tool`

> Dual-view + monitor module under the dual-view rendering-density contract.  Ships an extended Build view (full canvas for single-tool queries) AND a compact Build view (grid card for multi-tool query DAGs) AND a Monitor bento tile — all REQUIRED per [`docs_revamped/03_standards/rendering_density.md`](../../../../../docs_revamped/03_standards/rendering_density.md) §1.

**Version:** v3 (factory dual-view implementation)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_inflation_swap_forward_tool` (generic_runnable; backend `_PRIMITIVE_SPECS` entry; standalone bridge via `/api/v1/rates/detail/inflation-swap-forward`)
**Backend MCP function:** `calculate_inflation_swap_forward_tool` (folder name === MCP function name — no alias bridge needed)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Category:** `forward_rate`
**Mockups:** [`./mockups/Compact.png`](./mockups/Compact.png) + [`./mockups/Extended.png`](./mockups/Extended.png) — committed alongside the module per the mockup-first workflow.

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier; backend ships in `_PRIMITIVE_SPECS`.
- **`custom_build_surface`** — capability tier with the **dual-view** contract:
  - **Extended Build view** ([`surfaces/BuildExtended.tsx`](surfaces/BuildExtended.tsx)) — full canvas mounted when this tool is the focus of a single-tool query (Library "Open in Build", single-tool Ask handoff, direct `?context=` deep-link).  Identity row (e.g. *EUR 5Y5Y ZCIS Forward · HICPxT*), controls strip (ZCIS Curve / Forward / Lookback / Field), 9-cell KPI strip (FORWARD ZCIS / 1D / 5D / 1M CHANGE / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS), top-right Z-Score / Percentile / Inflation-Anchor cards, main chart with z-score band overlays, stretch-context panel, methodology card sourcing the wire-honesty disclosure from `current_metrics.methodology_label` verbatim, lineage footer.
  - **Compact Build view** ([`surfaces/BuildCompact.tsx`](surfaces/BuildCompact.tsx)) — grid card mounted as a node body inside multi-tool query DAG visualizations (e.g. *"compare USD_ZCIS 5Y5Y vs EUR_ZCIS 5Y5Y forwards"*).  3-KPI strip (FORWARD ZCIS / 1D CHANGE / Z-SCORE), mini-chart with ±2σ z-score bands + terminal dot, short-form swap-implied / index-family caveat footer, click-to-expand affordance.
- **`monitor_surface`** — Monitor bento tile ([`surfaces/monitor/InflationSwapForwardWidget.tsx`](surfaces/monitor/InflationSwapForwardWidget.tsx)) showing one ZCIS implied forward (curve_family × forward_pair × lookback parameterised; defaults EUR_ZCIS / 5Y5Y / 252).  ZCIS forwards meet the eligibility rule per `surface_contract.md §3.4` — desk-glanceable, parked on morning-briefing boards as the secular forward-inflation-compensation anchor.  Inherently compact per `rendering_density.md §8`; no extended Monitor variant.

---

## 2. What does the user read off each surface?

### Extended Build view

The full investigation canvas.  A PM opens this when they want to drill in on ONE ZCIS forward observation.  They read, in order: (a) the title row identifying *which* curve_family + forward_pair + as-of date (the backend's `forward_window_label` like "EUR_ZCIS 5Y5Y" is the canonical naming); (b) the top-right Z-Score (252D) / Percentile (252D) / Inflation-Anchor cards for an at-a-glance "is this stretched?  which inflation regime?" read — the Inflation-Anchor card surfaces `inflation_index_family` + `index_lag` + `interpolation` from the wire so the desk reads the per-curve quirks (USD_ZCIS = CPI-U / 3M lag / Daily interpolation; EUR_ZCIS = HICPxT / 3M lag / Monthly; GBP_ZCIS = RPI / 2M lag / Monthly) without leaving the canvas; (c) the 9-cell KPI strip — FORWARD ZCIS / 1D CHANGE / 5D CHANGE / 1M CHANGE / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS — for the full numeric picture (unlike the OIS sibling which substitutes START / END SPOT for 5D / 1M change, the ZCIS schema exposes `change_1w_bps` + `change_1m_bps` directly, so the strip matches the mockup verbatim); (d) the main chart with ±2σ / ±1.5σ z-score band overlays to see the historical context for the current observation; (e) the stretch-context panel for an interpretation paragraph framing the move as hawkish/dovish forward-inflation-compensation repricing; (f) the methodology card with the wire-honesty disclosure threaded verbatim from `current_metrics.methodology_label` (the YAML's `methodology.what_it_does` — NOT a hardcoded TS literal), plus the dual-compounding geometric formula, the sign convention, and the index-family triple; (g) the lineage footer to confirm freshness + provider chain.  The controls strip lets them swap curve_family / forward_pair (1Y1Y / 2Y3Y / 3Y2Y / 5Y5Y / 10Y10Y / 10Y20Y) / lookback / field — z-score conventions are YAML-locked on this primitive (no "Advanced" override panel — mirrors the OIS forward_rate / ZCIS rate_level / curve_spread siblings).

### Compact Build view

The at-a-glance grid card.  A PM sees this when they ask a multi-tool prompt that contains this primitive among others.  They read THREE numbers + a sparkline: forward ZCIS rate (in %, 3-decimal precision matching the desk's inflation-swap quote convention), 1-day change (in bps + percent subtext, tone-coloured POSITIVE = forward inflation compensation repricing higher / NEGATIVE = lower), rolling 252d z-score (with regime caption — Normal / Elevated / Extreme).  The mini-chart shows the history with ±2σ / ±1.5σ z-score bands overlaid so the current observation's position vs. trailing range is visible without leaving the DAG.  The footer surfaces a CURATED short-form caveat ("CPI-U · Swap-implied forward; index-family caveat applies") — clearly distinct from the full wire prose, which lives on the extended methodology card.  The expand arrow opens the extended view in a modal with a breadcrumb back to the DAG.

### Monitor tile

A desk-glanceable bento tile.  At a glance: inflation index short label (CPI-U / HICPxT / RPI) + forward_pair (5Y5Y / 1Y1Y / ...) + flag in the kicker; the implied forward ZCIS rate in large mono type; a 1d Δ chip toned for hawkish (+) / dovish (−); a percentile chip; a 252d low/high range strip with a current-position marker.  The methodology disclosure surfaces as a hover tooltip on the z-score chip via the wire-sourced `methodology_label` — clicking through to Build opens the full methodology card.  The provenance footer shows the as-of date so the user can confirm freshness without opening the widget config.

---

## 3. Why these surfaces and not others?

**Why the dual-view contract** (both extended + compact REQUIRED): per [`rendering_density.md §1.1`](../../../../../docs_revamped/03_standards/rendering_density.md) multi-tool prompts are the default reality; a tool that ships only an extended view falls back to a generic artifact-type card in multi-tool DAGs, producing an inconsistent experience.  The standard explicitly OVERRIDES FM4 parsimony for the dual-view mandate.

**Why these three compact KPIs** (FORWARD ZCIS + 1D CHANGE + Z-SCORE): they're the desk-canonical "first three numbers" a PM reads off a ZCIS forward snapshot.  Alternatives considered + rejected: 252d percentile (already visible via the z-score regime + band overlay), 5D / 1M change (period context, not headline data — 1D is the canonical headline change cell on every inflation-swap and OIS sibling card), 252d high/low (range context, not headline data), start_zcis_pct / end_zcis_pct (audit-trail data, surfaced on the EXTENDED dual-compounding decomposition card — not the compact card).  The chosen three answer "where's the forward inflation compensation now / how much did it move today / is this stretched?" in one glance.  **Per Option (c) precedent (sibling OIS forward_rate ship): keep shell-standard 3-KPI density on Compact even though the mockup illustrates a richer header / sub-strip.**

**Why a typed-detail endpoint** (`/api/v1/rates/detail/inflation-swap-forward`) instead of reusing `/api/v1/tools/{name}/run`: per [`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md) every new standalone module ships its own typed bridge.  The generic `/run` route is the LLM-facing surface; the frontend's typed consumption is a separate concern with its own typed contract.

**Why a per-tool family registry** (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) instead of reusing the shared `countryCaveatFor` helper or the OIS per-tool registry: the shared registry covers the LINKER domain (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) only; the OIS per-tool registry covers nominal-policy curves.  ZCIS curve families are a DISJOINT universe — they reference distinct inflation indices (CPI-U NSA / HICPxT / RPI) with distinct lag + interpolation conventions, and the rates are NOT directly comparable cross-family without harmonising those conventions.  Mirrors the sibling `calculate_inflation_swap_rate_level_tool` / `calculate_inflation_swap_curve_spread_tool` per-tool registry pattern.

**Why a forward-pair pill selector** (1Y1Y / 2Y3Y / 3Y2Y / 5Y5Y / 10Y10Y / 10Y20Y) instead of two separate tenor dropdowns: the backend's tenor-pair input requires `start_tenor` + `end_tenor` together (the schema layer rejects partial input AND inverted pairs), AND the desk speaks canonical forward shorthand (5Y5Y, 1Y1Y).  Surfacing the pair as a single selection mirrors the desk vocabulary AND removes the "did you pick the right end_tenor for your start_tenor?" foot-gun.  The selector pre-populates `start_tenor` + `end_tenor` simultaneously on change so the URL state captures the exact backend params.

**Why NO date-pair input mode** (in contrast to the OIS forward_rate sibling): the ZCIS forward schema is tenor-pair-only in V1 — `start_tenor` + `end_tenor` are required Field()s with no Optional sibling, and the schema lacks the `_AT_LEAST_ONE_MODE` validator the OIS sibling uses.  Adding a date-mode tab to the controls strip would imply a backend mode that doesn't exist.

**Why NO z-score override controls** (mirrors the OIS forward_rate sibling): the ZCIS forward primitive's `InflationSwapForwardInput` intentionally does NOT expose `z_score_window_days` / `z_score_min_periods` / `z_score_ddof` — these are YAML-locked at compute() time only.  Mirrors the OIS forward_rate / ZCIS rate_level / curve_spread / butterfly siblings.  Adding an Advanced panel here would imply the controls do something they don't.

**Why a CURATED short caveat on the compact card** instead of inlining `current_metrics.methodology_label`: the wire prose is long-form (it spells out the full dual-compounding formula AND the "forward inflation compensation; not a clean forward expected-inflation read" caveat AND the alignment discipline) — at ~600 characters it overflows the compact footer's single-line budget.  The compact card surfaces a curated short-form caveat ("CPI-U · Swap-implied forward; index-family caveat applies") that points the desk reader at the index-family quirk, and the FULL wire-honesty prose is reachable via expand → extended methodology card.  Clearly distinct from the wire prose; mirrors the sibling-tool pattern where wire-honesty lives on the extended view and the compact card carries a curated summary.

**Why NOT a bespoke Ask card** (yet): the generic `AssistantResearchCard` (KPIs + sparkline + provenance) covers the chat-result framing for *"what's USD_ZCIS 5Y5Y?"* adequately.  A bespoke ask card would be claimed only if a domain-specific framing becomes desk-canonical.

---

## 4. What would change the design?

- **A new ZCIS curve_family** (e.g. JPY_ZCIS, CAD_ZCIS) lands in the backend universe → add the curve_family code to `FAMILY_REGISTRY` in [`surfaces/inflationSwapForwardShared.ts`](surfaces/inflationSwapForwardShared.ts).  No shell changes.
- **A new canonical forward pair** (e.g. 2Y8Y, 7Y3Y) becomes desk-canonical → append it to `ZCIS_FORWARD_PAIRS` in [`surfaces/inflationSwapForwardShared.ts`](surfaces/inflationSwapForwardShared.ts) and it auto-shows up in the Build controls strip AND the Monitor widget config form.
- **A workflow template composing ZCIS forward with `forward_breakeven_simple_tool`** (the "forward inflation compensation vs forward bond-implied breakeven" comparison) lands → the compact view fires as a node body in that workflow's dashboard.  No design change required — the compact view is workflow-agnostic by contract.
- **The backend exposes a `forward_breakeven_decomposition` block on `current_metrics`** (the explicit cross-tool composition with the linker breakeven primitive) → add a dual-decomposition card to the extended view alongside the existing dual-compounding card.
- **A date-pair forward mode** (start_date, end_date — ad-hoc windows aligned to ECB / FOMC meetings) lands in the backend → add a tabbed selector to the controls strip toggling between tenor-pair pill set and a date-range picker; today the backend's tenor-pair-only input layer makes this surface impossible.
- **High / low / range exposed in PERCENT on the wire** (today only the BPS variant ships) → drop the `bpsToPctFixed` conversion helper in `extendedKPIs` and read the percent fields directly.  Minor cleanup; doesn't change the user-visible view.

---

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name (`calculate_inflation_swap_forward_tool`) equals backend `tool_name` exactly.  No alias bridge needed; the MCP function + workflow registry + folder all align.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable` + `custom_build_surface` + `monitor_surface`; per the rendering-density standard the second REQUIRES both `buildExtended` + `buildCompact` files.
- **FM4** (tier-set parsimony) — overridden explicitly by [`rendering_density.md §1.2`](../../../../../docs_revamped/03_standards/rendering_density.md) for the dual-view mandate.  Other capability tiers (preview / ask) NOT claimed for V1 per FM4 parsimony.
- **FM5** (display metadata) — `displayName` / `category` / `oneLineSummary` mirror the backend manifest entry + ToolCard.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — `surfaces.buildExtended` + `surfaces.buildCompact` populated; corresponding files at canonical paths; Monitor widget at `surfaces/monitor/InflationSwapForwardWidget.tsx`.
- **FM9** (routing-claim disclosure) — `typedView: null` per the standalone-module pattern; this module owns its own full Build surfaces.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — [`__tests__/module.spec.ts`](__tests__/module.spec.ts) asserts the dual-view contract.
- **FM12** (loader presence) — module imported in [`src/modules/index.ts`](../../index.ts) at line 59 (alphabetical position).
- Per the project's **methodology-exposure standalone-bridge contract** ([`methodology_exposure.md §5`](../../../../../docs_revamped/03_standards/methodology_exposure.md)): own typed-detail endpoint at `/api/v1/rates/detail/inflation-swap-forward`; own service helper `fetchDetailInflationSwapForward`; own frontend type `InflationSwapForwardOutput`; no reuse of OIS forward-rate or ZCIS rate-level shells.
- Per the **rendering-density dual-view contract** ([`rendering_density.md §1`](../../../../../docs_revamped/03_standards/rendering_density.md)): both views ship + both are wired in `module.ts.surfaces` + corresponding files at canonical paths.
- Per the **methodology-disclosure wire-honesty pattern (PR10 / P5)**: the extended methodology card's "Disclosure" row sources from `current_metrics.methodology_label` (the YAML's `methodology.what_it_does`); the Monitor widget surfaces the same field as a `title=` tooltip; NEITHER hardcodes the disclosure prose as a TS literal — YAML edits flow to runtime.

---

## Mockup conformance

This module was designed mockup-first: the design intent for both views was captured as PNG mockups committed to [`./mockups/`](./mockups/) BEFORE implementation began.  Future maintainers reading this folder cold can compare the rendered surfaces against the mockup images for visual-fidelity regression checks.

- **Extended view** matches `mockups/Extended.png`: identity row pinned to "EUR 5Y5Y ZCIS Forward · HICPxT" with ECB anchor + dual-compounding badge; controls strip at canonical positions (ZCIS Curve / Forward / Lookback / Field); 9-cell KPI strip with FORWARD ZCIS / 1D / 5D / 1M CHANGE / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS — the strip matches the mockup verbatim because the ZCIS schema exposes 5D + 1M change directly (unlike the OIS sibling which substitutes START / END SPOT here).  Methodology card sources the wire-honesty disclosure from `current_metrics.methodology_label` verbatim.
- **Compact view** matches `mockups/Compact.png` at shell-standard density per the Option (c) precedent: 3 headline KPIs (FORWARD ZCIS / 1D CHANGE / Z-SCORE) + sparkline + curated short-form caveat footer + as-of stamp.  The mockup illustrates a richer footer strip (5D / 1M / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS) that we DO NOT render — those KPIs live in the EXTENDED view per the shell-standard 3-KPI ceiling.  Documented deviation; the design contract favours consistent compact density over per-tool richness.  The identity row renders the three canonical segments from the mockup — *market + curve-family stem* (primary line, e.g. "EUR ZCIS") and *forward-pair · inflation-index short* (secondary line, e.g. "5Y5Y · HICPxT") — so the index-family identity (CPI-U / HICPxT / RPI) is visible at compact density without needing to expand to the extended view.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-08 | Frontend factory dual-view + monitor implementation per `rendering_density.md`.  Shipped `surfaces/BuildExtended.tsx`, `surfaces/BuildCompact.tsx`, `surfaces/inflationSwapForwardShared.ts`, and `surfaces/monitor/InflationSwapForwardWidget.tsx`.  Tier set extended to `[generic_runnable, custom_build_surface, monitor_surface]`.  Standalone-bridge typed-detail endpoint shipped at `/api/v1/rates/detail/inflation-swap-forward`.  Per-curve ZCIS metadata registry + canonical forward-pair grid (FAMILY_REGISTRY + ZCIS_FORWARD_PAIRS) live in `inflationSwapForwardShared.ts`.  Methodology disclosure threaded from `current_metrics.methodology_label` (wire-honesty pattern). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
