# THESIS — `calculate_curve_spread_tool`

> Sovereign curve spread — same-curve 2-point tenor spread (e.g. UST 2s10s, Bund 5s30s) expressed in bps, with a fixed 1-year rolling z-score. **Migrated** from the legacy typed-renderer pattern (`typedView: 'spread'` + `surfaces.resultRenderer`) to the new dual-view + standalone-bridge contract.

**Version:** v3 (migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_curve_spread_tool` (sovereign_bonds sub-agent)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Backing endpoint:** `/api/v1/rates/detail/spread` (per-tool standalone bridge)
**Operationalises:** [FM1–FM12](../../../../../docs_revamped/02_components/frontend_module/README.md), [PR8](../../../../../docs_revamped/02_components/primitive/README.md), [P3 · P5 · P10](../../../../../docs_revamped/00_thesis/01_non_negotiables.md), [`rendering_density.md` §1 §2 §5](../../../../../docs_revamped/03_standards/rendering_density.md), [`methodology_exposure.md` §5](../../../../../docs_revamped/03_standards/methodology_exposure.md).

---

## 1. What surfaces does this module ship?

Five surface files, three logical surfaces:

- **`surfaces/BuildExtended.tsx`** — full-canvas Build view; mounted for single-tool queries via `VirtualPrimitiveCanvas` (and from the click-to-expand modal of a compact card).  Owns the controls strip (curve / short tenor / long tenor / lookback / field), the top-right z-score + percentile + sovereign-caveat cards, the bps-scale KPI strip with two-leg sovereign decomposition, the main `bp`-axis chart with ±1.5σ / ±2σ bands, the methodology card, and the lineage footer.
- **`surfaces/BuildCompact.tsx`** — grid-card Build view; mounted as a node body inside multi-tool query DAG visualizations.  3 headline KPIs (SPREAD · 1D CHANGE · Z-SCORE 252D), a sparkline with the same ±σ bands, a one-line caveat footer, and the shared-infrastructure expand affordance (`onExpand` prop).  ~400×280px at `size='small'`.
- **`surfaces/curveSpreadShared.ts`** — cross-surface helper (data hook `useCurveSpread`, sovereign family registry, KPI / methodology / reference-band builders, percentile / 252d range / observation-count client-side derivations).  Single source of truth so the Compact + Extended views cannot drift.
- **`surfaces/monitor/CurveSpreadsWidget.tsx`** — PRESERVED Monitor bento tile; pre-aggregated G4 2s10s slope monitor (UST · Bund · Gilt · JGB).  Catalog id `curve_spreads`.
- **`surfaces/monitor/SpreadChartWidget.tsx`** — PRESERVED Monitor bento tile; parameterised single-pair chart (user picks curve_family + short_tenor + long_tenor + lookback_days).  Catalog id `spread_chart`.

Tier claims: `generic_runnable` (backend in `_PRIMITIVE_SPECS`) · `custom_build_surface` (dual-view per `rendering_density.md` §1) · `monitor_surface` (two preserved widgets per `surface_contract.md` §3.4).

## 2. What does the user read off each surface?

* **Build Extended** — header chip `UST 2s10s` (sovereign · pair) with the long-tenor-yield-minus-short-tenor-yield subtitle.  Top-right cards show z-score (e.g. +1.48 Elevated), percentile (e.g. 74th High), and the sovereign caveat ("Sovereign curve; cross-check swap_spread for sovereign-vs-OIS gap").  Headline KPI strip carries SPREAD (+42.0bp), 1D CHANGE (+2.6bp), 5D, 1M, Z-SCORE, PERCENTILE (252D), 252D HIGH, 252D LOW, OBSERVATIONS — then a TWO-LEG SOVEREIGN DECOMPOSITION row exposing SHORT (2Y UST) = 4.27% and LONG (10Y UST) = 4.69%.  Main chart is the bps-spread history with ±1.5σ / ±2σ envelope bands and a stretch-context panel that interprets the current z + percentile into a one-paragraph regime call.  Methodology card lists construction, sign convention, valid tenor pair, pair label, field, z-score window, lookback, sovereign curve, units, same-curve invariant, and the canonical disclosure.  Lineage footer carries tool name + version + provider chain + as-of.
* **Build Compact** — header chip `SOVEREIGN CURVE SPREAD · SNAPSHOT`, identity row `UST · 2s10s` with "10Y UST yield minus 2Y UST yield" subtitle.  Three KPIs (per `rendering_density.md` §2.2 + Q3 below): SPREAD (2s10s) with steeper/inverted caption + percent-of-short subtext, 1D CHANGE with sign-toned colour, Z-SCORE (252D) with regime caption.  Sparkline shows the same bps spread series with ±σ bands.  Footer carries the caveat line + as-of + freshness pill.  Expand affordance opens the Extended view in a modal.
* **Monitor `curve_spreads`** — pre-aggregated 2s10s slope monitor: one row per G4 curve (UST · Bund · Gilt · JGB) with curve label + spread label + sparkline + current spread bps + Δ1d + z-score badge.  Fed by `RatesDataContext` (no per-widget fetch).
* **Monitor `spread_chart`** — parameterised single-pair chart: the user picks curve_family + short_tenor + long_tenor + lookback_days; widget fetches `/detail/spread` with those params and renders kicker + headline spread + Δ1d + sparkline + endpoint-yield strip.

## 3. Why these surfaces and not others?

**Why the dual Build views are mandatory.** Multi-tool prompts ("compare UST 2s10s vs Bund 2s10s vs BTP 2s10s") are the default reality, not an edge case.  `rendering_density.md` §1.1 mandates BOTH `buildExtended` and `buildCompact` for any primitive claiming `custom_build_surface`.  This module ships both from day one of the migration.

**Why these three Compact KPIs.** A desk PM looking at a curve-spread card scans three things in order:

1. **SPREAD (2s10s)** — the curve-shape value itself.  Primary emphasis cell.  Sign caption ("Steeper" / "Inverted" / "Flat") encodes the regime in one word.  Percent-of-short subtext (e.g. "(0.42%)" for +42bp on a 2Y at 4.27%) keeps the bps→percent translation honest at a glance.
2. **1D CHANGE** — direction-of-move.  Sign-toned colour so the PM reads "steepening" / "flattening" / "neutral" without parsing the number.
3. **Z-SCORE (252D)** — stretch.  Regime+direction caption ("Elevated Steeper" at z=+1.48 → curve is steeper than its trailing-year norm).  The fixed 252-day window is YAML-locked on this primitive.

Alternatives considered + rejected:

- *Percentile 252D in slot 3 instead of z-score*.  Rejected: the desk read combines "where in the range" (percentile) with "how stretched relative to its own distribution" (z); the Extended view shows both, but the headline number on a curve-spread card is the z because curve-shape distributions are well-approximated by Gaussian on a 1y window (Tuckman 4e Ch.5).
- *3M change in slot 2 instead of 1D*.  Rejected: the desk read on a snapshot card is "what moved overnight," not "what's the trend"; 3M lives in the Extended KPI strip alongside 5D / 1M for trend context.

**Documented density deviation.**  Mockup shows a 6-cell post-chart KPI strip (5D, 1M, 252D PCTL, 252D HIGH, 252D LOW, OBSERVATIONS) on the Compact view in addition to the 3 headline KPIs; implementation ships shell-standard 3-KPI density per Option (c) Batch 1 fdac7d2 precedent — the 6-cell strip lives in the Extended view's KPI row instead.  Catalog `design_guardrails` explicitly authorises the deviation ("keep shell-standard density on Compact; document deviations in THESIS"); this paragraph closes the documentation requirement.

**Why two Monitor widgets and not one.**  The pre-aggregated G4 view (`curve_spreads`) is the desk-canonical "global slope monitor" — every desk parks it on their morning board.  The parameterised single-pair tile (`spread_chart`) is the "tracking my own basket" tile — desks watching e.g. EM peripheral pairs (BTP 5s30s) or AUD/CAD slope mount it custom.  Both are eligible under `surface_contract.md` §3.4 (parked on a desk board, read through the day).  Both have been live across the user base since pre-migration; the migration preserves their ids + paramFields verbatim per `MIGRATION_RULES.md` §6 so persisted dashboards keep rendering.

**Why NOT Ask surface.**  Ask handoff is via `KNOWN_BACKEND_TOOLS` → context handoff to Build; no bespoke Ask card needed.

**Why NOT a workspace preview widget.**  The Compact view IS the workspace preview affordance for multi-tool DAGs; a separate `preview` surface would duplicate it.

## 4. What would change the design?

Concrete triggers:

- **Backend lands `methodology_label`** on `CurveSpreadCurrentMetrics` (the PR10 wire-honesty cleanup that already landed in inflation_indexed_bonds / inflation_swaps).  Action: switch the methodology card's "Disclosure" row to source from `cm.methodology_label` instead of the canonical caveat constant in `curveSpreadShared.ts`.  One-line edit (the TODO marker is in `curveSpreadShared.ts`).
- **Backend adds `weekly_change_bps` / `monthly_change_bps` / `percentile_252d` / `high_252d_bps` / `low_252d_bps` / `observation_count`** to the Output.  Action: switch the Extended KPI strip's client-side derivations (`change5dBps` / `change1mBps` / `trailingPercentile252d` / `trailingHigh252d` / `trailingLow252d` / `observationCount`) to consume the wire fields directly.  The current client-side derivations are an honest stop-gap because the sovereign Output is leaner than its linker siblings.
- **A third Monitor variant emerges** (e.g. multi-curve 5s30s overlay).  Action: add an entry to `MODULE.monitorWidgets[]` with a new id; do not change the existing two ids.
- **A "rich-model" curve-fitting variant ships** (Nelson-Siegel fitted curve replacing the raw 2-point spread).  Action: introduce a sibling module (`calculate_fitted_curve_spread_tool`) rather than overloading this one; `rich_model: false` on this module stays.
- **The sovereign curve family enum grows** (e.g. EM addition).  Action: add a row to `FAMILY_REGISTRY` in `curveSpreadShared.ts` (one-line addition).

## 5. Which doctrine does this module operationalise?

- **FM1** (module identity) — folder name `calculate_curve_spread_tool` === backend `tool_name`.
- **FM3** (surface-tier capability declaration) — claims runtime tier `generic_runnable` + capability tiers `[custom_build_surface, monitor_surface]`.
- **FM4** (tier-set parsimony — overridden by `rendering_density.md` §1.2 for the dual-view mandate).
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value; no side effects.
- **FM8** (surface-file contract) — every claimed capability tier has matching populated surface files (`BuildExtended.tsx`, `BuildCompact.tsx`, plus the two preserved monitor widgets).
- **FM9** (standalone-bridge contract per `methodology_exposure.md` §5) — `typedView: null`; consumes `/api/v1/rates/detail/spread` via `fetchDetailSpread`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants` + the dual-view contract checks (both `surfaces.buildExtended` + `surfaces.buildCompact` populated, `typedView === null`, mockup PNGs present).
- **FM12** (loader presence) — module imported in `src/modules/index.ts` at the alphabetical position.
- **PR8** (single central methodology surface) — methodology card on Extended; one-line caveat in Compact footer; both flow through the shared `buildMethodologyRows` helper.
- **`rendering_density.md` §1** (dual-view mandate) — both buildExtended + buildCompact ship.
- **`rendering_density.md` §2.2** (compact view discipline) — 3 KPIs, no controls strip, shared modal via `onExpand`, methodology reachable via footer caveat.
- **`methodology_exposure.md` §5** (standalone bridge) — typedView removed; per-tool typed-detail endpoint at `/api/v1/rates/detail/spread`.
- **`MIGRATION_RULES.md` §6** (monitor widget identity rule) — ids `curve_spreads` + `spread_chart` and their paramFields preserved verbatim.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-10 | Migration sweep — converted to dual-view + standalone-bridge contract.  Removed `typedView: 'spread'` + `surfaces.resultRenderer` + `workspaceLabel`.  Added `surfaces/BuildExtended.tsx` + `surfaces/BuildCompact.tsx` + `surfaces/curveSpreadShared.ts`.  Preserved Monitor widget ids `curve_spreads` + `spread_chart` and their paramFields verbatim. |
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
