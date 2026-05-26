# THESIS — `calculate_cpi_surprise_tool`

> Module shipped with runtime tier only (Stage 3 / Stage 4c-derived).  Capability surfaces are added by Stages 5+ as the desk earns them.

**Version:** v2 (Stage 4f — post-runner-fix rewrite)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cpi_surprise_tool` (generic_runnable)
**Tier set:** `[generic_runnable]`
**Category:** `economic_release_surprises`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; the generic schema-driven builder (`GenericPrimitiveBuilder`) configures + runs + renders it via `POST /api/v1/tools/{name}/run`.  No bespoke per-tool Build / Preview / Monitor / Ask surface today — Stage 5+ adds capability tiers as the desk earns them.

## 2. What does the user read off each surface?

**Build (generic builder).** The schema-driven `GenericPrimitiveBuilder` mounts when the user opens this tool from Library / Ask handoff.  Controls rail is generated from the backend `ToolCard.input_fields`; the output canvas renders via `AutoRenderer` (Series → SeriesWidget, Panel → PanelWidget, etc.).

## 3. Why these surfaces and not others?

Generic-only today because the tool's output shape is well-served by `AutoRenderer` and the input fields are well-served by the schema-driven form.  Bespoke surfaces (typed view, Monitor tile, Ask card) land in Stage 5+ if the desk's read pattern justifies them.

## 4. What would change the design?

- Frequent desk read at a glance → claim `monitor_surface` and add `surfaces/monitor/<Name>.tsx` + a `MODULE.monitorWidgets` entry.
- Output shape gains rich structure that AutoRenderer can't honour → claim `custom_build_surface` and ship `surfaces/BuildSurface.tsx`.
- Persisted-artifact preview needs per-tool framing → claim `custom_preview_widget` and add `surfaces/PreviewWidget.tsx` + a `MODULE.modelAdapter` block.
- Chat-result framing the generic `AssistantResearchCard` can't carry → claim `ask_surface` and ship `surfaces/AskCard.tsx`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable`; no capability tiers.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM6** (unsupported-reason gating) — N/A (generic_runnable; no reason field).
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** (loader presence) — module imported in `src/modules/index.ts`.

---

## One-line summary

Per-release CPI surprise series (actual − consensus_median, in percentage points of YoY CPI) for one country\

---

## Version log

| Version | Date | Change |
|---|---|---|
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
