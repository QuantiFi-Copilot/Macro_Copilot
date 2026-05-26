# THESIS — `classify_curve_move_tool`

> Migrated module — primary surface code lives in this folder.

**Version:** v2 (Stage 4e — post-migration sweep)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `classify_curve_move_tool` (workflow_incompatible)
**Tier set:** `[workflow_incompatible, custom_build_surface, monitor_surface]`
**Backend sub-agent:** `sovereign_bonds` · **Category:** `forwards_classify`

---

## 1. What surfaces does this module ship?

- **`workflow_incompatible`** — runtime-status tier.
- **`custom_build_surface`** — typed-view module: `surfaces/BuildSurface.tsx` ships the `regime` view, resolved by the central contextDecoder through `MODULE.typedView = 'regime'`.
- **`monitor_surface`** — Monitor catalog widgets shipped: `curve_classifier`.  Component files live under `surfaces/monitor/`; catalog metadata (id, label, description, sizes, paramFields) is declared inline on `MODULE.monitorWidgets` and `src/components/monitor/registry.ts` walks the module set to build the public `WIDGET_TYPES` map.

## 2. What does the user read off each surface?

* Build (typed view): regime label (BULL_STEEPENER / BEAR_FLATTENER / PARALLEL_SHIFT / TWIST / NEUTRAL) with the supporting numeric evidence (front-leg Δ, back-leg Δ, both in bps, plus the classification thresholds).
* Monitor (`curve_classifier`): G4 classification dashboard — daily and weekly move label per curve.

## 3. Why these surfaces and not others?

`workflow_incompatible` on the backend (categorical output, no Series/Panel artifact), so the typed-view + Monitor surface are the live affordances.  The generic builder is intentionally NOT offered because the workflow bridge can't dispatch the output anyway.

## 4. What would change the design?

Concrete triggers:
- Backend bridge gaining categorical-output support → revisit the runtime tier (could become `generic_runnable`).
- Multi-curve overlay or per-tenor decomposition demand → extend the typed view or add a Monitor variant.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims the runtime
  tier `workflow_incompatible` and capability tiers
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

Deterministically classify a two-point sovereign curve move over a discrete lookback into one of six canonical labels.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4e — rewrote Q2–Q5 to match the actual post-migration module state. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
