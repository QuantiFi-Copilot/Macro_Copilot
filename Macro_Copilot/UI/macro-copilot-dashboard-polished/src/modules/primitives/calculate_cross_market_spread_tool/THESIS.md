# THESIS — `calculate_cross_market_spread_tool`

> Same-tenor sovereign cross-market spread (e.g. UST-Bund 10Y, BTP-Bund 10Y) expressed in bps, with a fixed 1-year rolling z-score. **Migrated** from the legacy typed-renderer pattern (`typedView: 'cross_market'` + `surfaces.resultRenderer`) to the new dual-view + standalone-bridge contract.

**Version:** v3 (migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cross_market_spread_tool` (sovereign_bonds sub-agent)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Backing endpoint:** `/api/v1/rates/detail/cross-market` (per-tool standalone bridge)
**Operationalises:** [FM1–FM12](../../../../../docs_revamped/02_components/frontend_module/README.md), [PR8](../../../../../docs_revamped/02_components/primitive/README.md), [P3 · P5 · P10](../../../../../docs_revamped/00_thesis/01_non_negotiables.md), [`rendering_density.md` §1 §2 §5](../../../../../docs_revamped/03_standards/rendering_density.md), [`methodology_exposure.md` §5](../../../../../docs_revamped/03_standards/methodology_exposure.md).

---

## 1. What surfaces does this module ship?

Five surface files, three logical surfaces:

- **`surfaces/BuildExtended.tsx`** — full-canvas Build view; mounted for single-tool queries via `VirtualPrimitiveCanvas` (and from the click-to-expand modal of a compact card). Owns the controls strip (Curve A / Curve B / Tenor / Lookback / Field), the top-right z-score + percentile + sovereign-divergence cards, the bps-scale KPI strip + the two-leg PERCENT decomposition row, the main `bp`-axis chart with ±1.5σ / ±2σ bands, the methodology card, and the lineage footer.
- **`surfaces/BuildCompact.tsx`** — grid-card Build view; mounted as a node body inside multi-tool query DAG visualizations. 3 headline KPIs (SPREAD · 1D CHANGE · Z-SCORE 252D), a sparkline with the same ±σ bands, a one-line sovereign-divergence caveat footer, and the shared-infrastructure expand affordance (`onExpand` prop). ~400×280px at `size='small'`.
- **`surfaces/crossMarketSpreadShared.ts`** — cross-surface helper (data hook `useCrossMarketSpread`, sovereign family registry, KPI / methodology / reference-band builders, premium-caption + chart-row helpers). Single source of truth so the Compact + Extended views cannot drift.
- **`surfaces/monitor/CrossMarketSpreadsWidget.tsx`** — PRESERVED Monitor bento tile; pre-aggregated G3 cross-sovereign dashboard (BTP-Bund · OAT-Bund · UST-Bund at 10Y) fed by `RatesDataContext`. Catalog id `cross_market_spreads`.
- **`surfaces/monitor/CrossMarketSpreadWidget.tsx`** — PRESERVED Monitor bento tile; parameterised single-pair chart (user picks curve_family_1 + curve_family_2 + tenor + lookback_days). Catalog id `cross_market_spread`.

Tier claims: `generic_runnable` (backend in `_PRIMITIVE_SPECS`) · `custom_build_surface` (dual-view per `rendering_density.md` §1) · `monitor_surface` (two preserved widgets per `surface_contract.md` §3.4).

## 2. What does the user read off each surface?

* **Build Extended** — header chip `UST-Bund 10Y Spread` with subtitle "10Y US Treasuries yield minus 10Y German Bunds yield" + flags 🇺🇸 🇩🇪. Top-right cards show z-score (e.g. +1.71 Elevated), percentile (e.g. 71st High), and a sovereign-divergence card ("US Premium · Sovereign divergence — policy + term premium + credit/supply"). Headline KPI strip carries SPREAD (+182bp · US Premium), 1D CHANGE (+5.8bp), 1W CHANGE (+14.2bp), 1M CHANGE (+29.0bp), Z-SCORE (252D) +1.71, PERCENTILE (252D) 71st, 252D HIGH/LOW (+232/-68), OBS (365); then a TWO-LEG SOVEREIGN DECOMPOSITION row exposing UST 10Y = 4.2575% and Bund 10Y = 2.4375%. Main chart is the bps-spread history with ±1.5σ / ±2σ envelope bands and a stretch-context panel that interprets the current z + percentile into a one-paragraph regime call. Methodology card lists construction, sign convention, cross-curve invariant, pair label, field, alignment, z-score window, lookback, trailing range, sovereign curves, units, and the canonical disclosure. Lineage footer carries tool name + version + provider chain + as-of.
* **Build Compact** — header chip `SOVEREIGN CROSS-MARKET SPREAD · SNAPSHOT`, identity row `UST-Bund · 10Y` with "UST_10Y − DE_BUND_10Y" secondary and 🇺🇸 🇩🇪 flags. Three KPIs (per `rendering_density.md` §2.2 + Q3 below): SPREAD (10Y) with `<cf> Premium` caption (e.g. "US Premium" at +182bp), 1D CHANGE with sign-toned colour and percent-of-cf1 subtext, Z-SCORE (252D) with regime caption. Sparkline shows the same bps spread series with ±σ bands. Footer carries the sovereign-divergence caveat + as-of + freshness pill. Expand affordance opens the Extended view in a modal.
* **Monitor `cross_market_spreads`** — pre-aggregated G3 view: one row per pair (BTP-Bund / OAT-Bund / UST-Bund at 10Y) with pair label + sparkline + current spread bps + Δ1d + Δ1m + percentile + z-score badge. Fed by `RatesDataContext` (no per-widget fetch).
* **Monitor `cross_market_spread`** — parameterised single-pair chart: the user picks curve_family_1 + curve_family_2 + tenor + lookback_days; widget fetches `/detail/cross-market` with those params and renders kicker + headline spread + Δ1d + sparkline + endpoint-yield strip.

## 3. Why these surfaces and not others?

**Why the dual Build views are mandatory.** Multi-tool prompts ("compare UST-Bund 10Y vs BTP-Bund 10Y vs OAT-Bund 10Y") are the default reality, not an edge case. `rendering_density.md` §1.1 mandates BOTH `buildExtended` and `buildCompact` for any primitive claiming `custom_build_surface`. This module ships both from day one of the migration.

**Why these three Compact KPIs.** A desk PM looking at a cross-market spread card scans three things in order:

1. **SPREAD (10Y)** — the cross-market value itself. Primary emphasis cell. The "<cf1> Premium" / "<cf2> Premium" caption encodes the sign in one word — "US Premium" at +182bp UST-Bund tells the desk UST trades CHEAP to Bunds at the same tenor.
2. **1D CHANGE** — direction-of-move. Sign-toned colour so the PM reads "widening" / "tightening" / "neutral" without parsing the number. Percent-of-cf1-yield subtext (e.g. "(+3.29%)" at +5.8bp on UST 4.27%) keeps the bps→percent translation honest at a glance.
3. **Z-SCORE (252D)** — stretch. Normal / Elevated / Extreme regime caption ("Elevated" at z=+1.71 → spread is wider than its trailing-year norm). The fixed 252-day window is YAML-locked on this primitive.

Alternatives considered + rejected:

- *Percentile 252D in slot 3 instead of z-score*. Rejected: the desk read combines "where in the range" (percentile) with "how stretched relative to its own distribution" (z); the Extended view shows both, but the headline number on a cross-market card is the z because sovereign-spread distributions are well-approximated by Gaussian on a 1y window (Tuckman 4e Ch.5).
- *3M change in slot 2 instead of 1D*. Rejected: the desk read on a snapshot card is "what moved overnight," not "what's the trend"; 1W + 1M live in the Extended KPI strip alongside the trend context.

**Documented density deviation.** Mockup shows a post-chart caveat row + percentile bar + per-leg yield decomposition on the Compact view in addition to the 3 headline KPIs; implementation ships shell-standard 3-KPI density per Option (c) Batch 1 fdac7d2 precedent — the 6-cell strip + per-leg decomposition live in the Extended view's KPI row instead. Catalog `design_guardrails` explicitly authorises the deviation ("keep shell-standard density on Compact; document deviations in THESIS"); this paragraph closes the documentation requirement.

**Why two Monitor widgets and not one.** The pre-aggregated G3 view (`cross_market_spreads`) is the desk-canonical "European-periphery / transatlantic divergence monitor" — BTP-Bund, OAT-Bund, UST-Bund at the 10Y point. Every fixed-income desk in EU parks it on their morning board. The parameterised single-pair tile (`cross_market_spread`) is the "tracking my own cross-market basket" tile — desks watching e.g. JGB-UST 10Y for FX-hedged JP equity flows or AU-CA 10Y for commodity-economy slope correlation mount it custom. Both are eligible under `surface_contract.md` §3.4 (parked on a desk board, read through the day). Both have been live across the user base since pre-migration; the migration preserves their ids + paramFields verbatim per `MIGRATION_RULES.md` §6 so persisted dashboards keep rendering.

**Why NOT Ask surface.** Ask handoff is via `KNOWN_BACKEND_TOOLS` → context handoff to Build; no bespoke Ask card needed.

**Why NOT a workspace preview widget.** The Compact view IS the workspace preview affordance for multi-tool DAGs; a separate `preview` surface would duplicate it.

## 4. What would change the design?

Concrete triggers:

- **Backend lands `methodology_label`** on `CrossMarketSpreadCurrentMetrics` (the PR10 wire-honesty cleanup that already landed in inflation_indexed_bonds / inflation_swaps). Action: switch the methodology card's "Disclosure" row + the compact footer to source from `cm.methodology_label` instead of the canonical caveat constant in `crossMarketSpreadShared.ts`. One-line edit (the TODO marker is in `crossMarketSpreadShared.ts`).
- **Backend exposes an explicit `observation_count` field** on `CrossMarketSpreadCurrentMetrics`. Action: switch the Extended KPI strip's `OBS` cell from `data.time_series.length` to consume the wire field directly. The current derivation is honest because `time_series` is wire-frozen 1-to-1 with the canonical TimeSeries; the field would just remove the indirection.
- **A third Monitor variant emerges** (e.g. a curve-shape cross-market widget that mounts 2s10s slopes of N sovereigns). Action: add an entry to `MODULE.monitorWidgets[]` with a new id; do not change the existing two ids.
- **OIS sovereign-vs-OIS divergence surfaces become first-class** (sovereign-vs-OIS basis monitoring becomes daily desk work). Action: cross-link the methodology card's interpretation to the `ois_cross_market_spread` + `swap_spread` sibling primitives so the PM can decompose the differential into (policy-path + term-premium + credit-supply).
- **The sovereign curve family enum grows** (e.g. EM addition: BRL_LTN, MXN_M, KRW_KTB). Action: add rows to `FAMILY_REGISTRY` in `crossMarketSpreadShared.ts` (one-line addition per family).

## 5. Which doctrine does this module operationalise?

- **FM1** (module identity) — folder name `calculate_cross_market_spread_tool` === backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims runtime tier `generic_runnable` + capability tiers `[custom_build_surface, monitor_surface]`.
- **FM4** (tier-set parsimony — overridden by `rendering_density.md` §1.2 for the dual-view mandate).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — every claimed capability tier has matching populated surface files (`BuildExtended.tsx`, `BuildCompact.tsx`, plus the two preserved monitor widgets).
- **FM9** (standalone-bridge contract per `methodology_exposure.md` §5) — `typedView: null`; consumes `/api/v1/rates/detail/cross-market` via `fetchDetailCrossMarket`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants` + the dual-view contract checks (both `surfaces.buildExtended` + `surfaces.buildCompact` populated, `typedView === null`, preserved widget ids present, mockup PNGs present).
- **FM12** (loader presence) — module imported in `src/modules/index.ts` at the alphabetical position (unchanged by the migration).
- **PR8** (single central methodology surface) — methodology card on Extended; one-line caveat in Compact footer; both flow through the shared `buildMethodologyRows` helper (TODO(PR10) → wire-sourced once the sovereign Output catches up).
- **`rendering_density.md` §1** (dual-view mandate) — both buildExtended + buildCompact ship.
- **`rendering_density.md` §2.2** (compact view discipline) — 3 KPIs, no controls strip, shared modal via `onExpand`, methodology reachable via footer caveat.
- **`methodology_exposure.md` §5** (standalone bridge) — typedView removed; per-tool typed-detail endpoint at `/api/v1/rates/detail/cross-market`.
- **`MIGRATION_RULES.md` §6** (monitor widget identity rule) — ids `cross_market_spreads` + `cross_market_spread` and their paramFields preserved verbatim.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-10 | Migration sweep — converted to dual-view + standalone-bridge contract. Removed `typedView: 'cross_market'` + `surfaces.resultRenderer` + `workspaceLabel`. Added `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` + `surfaces/crossMarketSpreadShared.ts`. Preserved Monitor widget ids `cross_market_spreads` + `cross_market_spread` and their paramFields verbatim. Added `time_series_spread` + `time_series_zscore` to the central `CrossMarketSpreadOutput` TS type to match the Pydantic Output. |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
