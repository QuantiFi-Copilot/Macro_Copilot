# THESIS — `get_yield_levels_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `get_yield_levels_tool` (generic_runnable)
**Tier set:** `[generic_runnable, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `snapshots`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.
- **`custom_build_surface`** — typed-view module: `surfaces/BuildSurface.tsx` ships the `yield` view, resolved by the central contextDecoder through `MODULE.typedView = 'yield'`.
- **`monitor_surface`** — Monitor catalog widgets shipped: `yield_level`.  Component files live under `surfaces/monitor/`; catalog metadata (id, label, description, sizes, paramFields) is declared inline on `MODULE.monitorWidgets` and `src/components/monitor/registry.ts` walks the module set to build the public `WIDGET_TYPES` map.

## 2. What does the user read off each surface?

* Build (typed view): yield-level chart with z-score band, current snapshot card (yield + Δ1d/1w/1m + z-score + percentile), and identity strip (curve_family / tenor).
* Monitor (`yield_level`): parameterised single-yield tile showing current yield + Δ1d + z-score + 252-day sparkline.

## 3. Why these surfaces and not others?

Single-yield snapshot is the bread-and-butter Monitor tile AND a load-bearing typed view on Build for one-off inspection of a specific (curve, tenor) point.  The pre-aggregated yield-snapshot dashboard widget (G4 grid) is intentionally a separate non-primitive surface — it lives in the legacy monitor/widgets/ folder because it reads from the dashboard aggregate endpoint rather than this primitive's typed-detail endpoint.

## 4. What would change the design?

Concrete triggers:
- Backend adding multi-tenor snapshot output → split into a sibling primitive or extend the typed view body.
- A second Monitor variant (e.g. yield-vs-OIS overlay) → add to `MODULE.monitorWidgets`.

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

Single-tenor sovereign yield snapshot — current yield, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, observation count, and full chartable time series.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
