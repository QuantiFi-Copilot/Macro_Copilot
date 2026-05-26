# THESIS — `calculate_cross_market_spread_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cross_market_spread_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `cross_market_rv`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — typed-view module: `surfaces/BuildSurface.tsx` ships the `cross_market` view, resolved by the central contextDecoder through `MODULE.typedView = 'cross_market'`.
- **`monitor_surface`** — Monitor catalog widgets shipped: `cross_market_spreads`, `cross_market_spread`.  Component files live under `surfaces/monitor/`; catalog metadata (id, label, description, sizes, paramFields) is declared inline on `MODULE.monitorWidgets` and `src/components/monitor/registry.ts` walks the module set to build the public `WIDGET_TYPES` map.

## 2. What does the user read off each surface?

* Build (typed view): chart of the cross-market spread over time with rolling z-score, daily change, percentile, and identity strip showing curve_family_1 / curve_family_2 / tenor.
* Monitor (`cross_market_spreads`): G3 dashboard (BTP-Bund, OAT-Bund, UST-Bund) at 10Y with daily/monthly change, percentile, z-score per row.
* Monitor (`cross_market_spread`): parameterised single-pair chart for any (curve A, curve B, tenor, lookback) combination.

## 3. Why these surfaces and not others?

Cross-market spreads are the canonical sovereign-RV signal (BTP-Bund especially); the desk needs both the at-a-glance dashboard view and the ability to surface a custom pair on demand.  Build owns the deep chart; Monitor owns the always-visible bento tile.

## 4. What would change the design?

Concrete triggers:
- Backend extending output to include a leg-decomposition (per-curve Z scores side-by-side) → extend the typed view body.
- Demand for a 3-way spread (e.g. BTP-Bund-OAT) → introduce a sibling primitive (this one is intentionally 2-curve).

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims the runtime
  tier `generic_runnable` and capability tiers
  `[custom_build_surface, monitor_surface]`.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — every claimed capability tier has
  a matching populated surface file under `surfaces/`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Yield differential between the same tenor on two sovereign curves (e.g. BTP-Bund 10Y), in basis points, with rolling 252-day z-score, daily / weekly / monthly change, trailing range, and full time series.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
