# THESIS — `calculate_curve_spread_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_curve_spread_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `curve_shape`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — typed-view module: `surfaces/BuildSurface.tsx` ships the `spread` view, resolved by the central contextDecoder through `MODULE.typedView = 'spread'`.
- **`monitor_surface`** — Monitor catalog widgets shipped: `curve_spreads`, `spread_chart`.  Component files live under `surfaces/monitor/`; catalog metadata (id, label, description, sizes, paramFields) is declared inline on `MODULE.monitorWidgets` and `src/components/monitor/registry.ts` walks the module set to build the public `WIDGET_TYPES` map.

## 2. What does the user read off each surface?

* Build (typed view): chart of the spread over time with a rolling z-score band, daily/weekly/monthly change in bps, trailing 252-day high/low/percentile, and a context strip showing the underlying short_tenor / long_tenor pair.
* Monitor (`curve_spreads`): pre-aggregated 2s10s slope monitor across G4 (UST/Bund/Gilt/JGB) — current spread, daily change, z-score, sparkline per row.
* Monitor (`spread_chart`): parameterised single-pair chart (user picks curve + short_tenor + long_tenor + lookback).

## 3. Why these surfaces and not others?

Curve spread is one of the canonical curve-shape primitives, so it earns a bespoke typed-view (the desk standard chart-with-z-score visual) in Build AND two Monitor variants (pre-aggregated dashboard view + a parameterised tile for desks tracking a specific custom pair).

## 4. What would change the design?

Concrete triggers:
- If the typed-detail endpoint adds a new field (e.g. peer-pair comparison rail), extend the typed view in `surfaces/BuildSurface.tsx`.
- A third Monitor variant (e.g. multi-curve overlay) → add an entry to `MODULE.monitorWidgets`.
- If the backend ever splits per-curve typed-detail endpoints (e.g. OIS curve-spread variant), introduce a sibling module rather than overloading this one.

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

Basis-point spread between two tenors on the same sovereign yield curve, with a fixed 1-year rolling z-score and full chartable time series.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
