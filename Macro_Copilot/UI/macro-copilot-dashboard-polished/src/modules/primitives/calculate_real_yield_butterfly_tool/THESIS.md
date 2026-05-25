# THESIS — `calculate_real_yield_butterfly_tool`

> Stage 3 minimum-viable module — runtime tier only.  Surface code lives in its
> legacy page-folder location until the Stage 4 refactor moves it into this folder.

**Version:** v1 (Stage 3 scaffold)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_real_yield_butterfly_tool` (generic_runnable)
**Tier set:** `[generic_runnable]`
**Backend sub-agent:** `inflation_indexed_bonds` · **Category:** `curve_shape`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier only (Stage 3 minimal scaffold).
  Backend ships this primitive in `_PRIMITIVE_SPECS`; it runs via `POST /api/v1/tools/{name}/run` through the workflow bridge.

(Capability tiers — `custom_build_surface`, `custom_preview_widget`, `monitor_surface`,
`ask_surface` — are NOT claimed in Stage 3.  The existing UI continues to render
via the legacy page-folder code in `src/components/build/`, `src/components/monitor/`,
etc.  Stage 4 PRs add capability tiers as surfaces move into this folder.)

## 2. What does the user read off each surface?

Stage 3 surfaces today: (none — Stage 3 ships the minimum-viable module).

User-facing read at the runtime tier level: opening this tool from Library →
`Open in Build` decodes per `contextDecoder.ts` and lands on the matching shared
surface (generic builder / typed view / unsupported card) per the current routing
priority.  The decoder lookup is unchanged by Stage 3 — the module spec contributes
to the central registries via the hybrid derivation but the resulting set
membership is identical to the Stage 1 hand-authored entries.

## 3. Why these surfaces and not others?

Stage 3 is a pure scaffolding stage.  Per the migration roadmap
([06_roadmap/frontend_migration.md](../../../../../../docs_revamped/06_roadmap/frontend_migration.md)),
every primitive that will eventually have a module gets its folder
materialised in this stage so Stage 4 refactor PRs have a destination
to move legacy surface code INTO.  Claiming capability tiers + populating
`surfaces.*` happens in Stage 4 alongside the actual code move (no
half-states between stages).

## 4. What would change the design?

Planned Stage 4+ capabilities: (none planned beyond the current runtime tier).

Concrete triggers:
- Backend output-shape changes → re-evaluate the runtime tier.
- New typed-detail endpoint shipped → claim `custom_build_surface` and
  point `MODULE.typedView` at the new kind.
- Per-tool persisted-artifact preview needed → claim `custom_preview_widget`
  and add `surfaces/PreviewWidget.tsx`.
- Tool surfaces frequently in daily desk read → claim `monitor_surface`
  and add `surfaces/MonitorWidget.tsx`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — exactly one runtime-status
  tier (`generic_runnable`); no capability tiers in Stage 3.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.
- Stage 3 of the migration roadmap (`docs_revamped/06_roadmap/frontend_migration.md`).

---

## Stage 3 implementation notes

Stage 3 ships this module with only the `generic_runnable` runtime tier.  Capability tiers + surface refs land in the relevant Stage 4 PR (4a sovereign + OIS, 4b rich-model, 4c futures); existing UI continues to render via legacy page-folder code until then.
---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
