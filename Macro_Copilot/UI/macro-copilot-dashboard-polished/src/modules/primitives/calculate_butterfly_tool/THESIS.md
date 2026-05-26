# THESIS — `calculate_butterfly_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_butterfly_tool` (manifest_typed_view)
**Tier set:** `[manifest_typed_view, custom_build_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `curve_shape`

---

## 1. What surfaces does this module ship?

- **`manifest_typed_view`** — runtime-status tier.
- **`custom_build_surface`** — typed-view module: `surfaces/BuildSurface.tsx` ships the `butterfly` view, resolved by the central contextDecoder through `MODULE.typedView = 'butterfly'`.

## 2. What does the user read off each surface?

* Build (typed view): butterfly chart over time with rolling z-score band, wing-spread decomposition (belly-short / belly-long), and trailing range stats.  Identity strip shows the (short, belly, long) tenor triple.

## 3. Why these surfaces and not others?

Manifest-only tool with a backend typed-detail endpoint but no `_PRIMITIVE_SPECS` entry, so the workflow bridge cannot dispatch it.  Build's typed view is the live surface; the `manifest_typed_view` runtime tier captures this exactly.

## 4. What would change the design?

Concrete triggers:
- Backend adding the primitive to `_PRIMITIVE_SPECS` → promote the runtime tier to `generic_runnable` and let the generic builder co-exist with the typed view.
- Monitor demand → claim `monitor_surface` and ship a bento tile.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims the runtime
  tier `manifest_typed_view` and capability tiers
  `[custom_build_surface]`.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — every claimed capability tier has
  a matching populated surface file under `surfaces/`.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Three-point curvature on a sovereign curve — (2 × belly − short − long) × 100 bps — with rolling z-score, trailing range, wing-spread components, and full time series.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
