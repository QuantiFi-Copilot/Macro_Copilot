# THESIS — `scan_extremes_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `scan_extremes_tool` (manifest_typed_view)
**Tier set:** `[manifest_typed_view, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `screening`

---

## 1. What surfaces does this module ship?

- **`manifest_typed_view`** — runtime-status tier.
- **`custom_build_surface`** — typed-view module: `surfaces/BuildSurface.tsx` ships the `scanner` view, resolved by the central contextDecoder through `MODULE.typedView = 'scanner'`.
- **`monitor_surface`** — Monitor catalog widgets shipped: `scanner`.  Component files live under `surfaces/monitor/`; catalog metadata (id, label, description, sizes, paramFields) is declared inline on `MODULE.monitorWidgets` and `src/components/monitor/registry.ts` walks the module set to build the public `WIDGET_TYPES` map.

## 2. What does the user read off each surface?

* Build (typed view): ranked scanner table with per-row identity (curve, tenor), current yield, daily change, z-score, percentile, and a signal-label chip (RICH / CHEAP / NEUTRAL).
* Monitor (`scanner`): compact bento variant of the scanner — top-N flagged instruments above the user's z-score threshold.

## 3. Why these surfaces and not others?

Manifest-only tool (typed-detail endpoint, no `_PRIMITIVE_SPECS` entry).  Build's typed view + Monitor tile are the live surfaces; the `manifest_typed_view` runtime tier captures the backend semantics exactly.

## 4. What would change the design?

Concrete triggers:
- Backend adding `_PRIMITIVE_SPECS` entry → promote runtime tier.
- Per-sub-agent scanner siblings (OIS, futures) → introduce sibling modules (e.g. `scan_ois_extremes_tool`).

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims the runtime
  tier `manifest_typed_view` and capability tiers
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

Scans every sovereign instrument in the database, ranks the top-N by absolute 252-day z-score and returns yield, daily change, z-score, percentile, and signal label per row.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
