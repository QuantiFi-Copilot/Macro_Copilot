# THESIS — `__smoke_test_tool`

> Stage 5 acceptance-test fixture.  Not a real backend tool.

**Version:** v1 (Stage 5 fixture)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** none — fixture only
**Tier set:** `[paused, custom_build_surface, custom_preview_widget, monitor_surface, ask_surface]`
**Category:** `snapshots` (placeholder; never user-visible)

---

## 1. What surfaces does this module ship?

- **`paused`** — runtime-status tier.  Backend doesn't ship this tool;
  the module exists for Stage 5's structural acceptance test only.
- **`custom_build_surface`** — `surfaces/BuildSurface.tsx` ships a
  marker component (`data-smoke-surface="build"`).  Mounted by
  `VirtualPrimitiveCanvas` AND `BuildShell` via the Stage 5 module-
  first dispatch — proves that path walks to the module shelf
  without central-routing edits.
- **`custom_preview_widget`** — `surfaces/PreviewWidget.tsx` ships a
  marker component (`data-smoke-surface="preview"`).  Registered by
  the `widgets/index.ts` walker through `MODULE.surfaces.preview`.
- **`monitor_surface`** — `surfaces/monitor/SmokeWidget.tsx` ships a
  marker component (`data-smoke-surface="monitor"`).  Registered by
  the Monitor registry walker through `MODULE.monitorWidgets[0]`.
- **`ask_surface`** — `surfaces/AskCard.tsx` ships a marker component
  (`data-smoke-surface="ask"`).  Resolved by
  `ConversationCanvas.resolveAssistantCard` when an assistant
  message's terminal tool is `__smoke_test_tool`.

## 2. What does the user read off each surface?

Nothing — these are test fixtures.  The acceptance test scans the
bundled source for the `data-smoke-surface` markers and asserts the
right surface is reached for each dispatch path.

## 3. Why these surfaces and not others?

To prove Stage 5's module-first dispatch architecture works end-to-
end without per-tool editing of central routing files.  This module
exercises every claimed surface kind at once.

## 4. What would change the design?

Stage N+ cleanup may delete this fixture once the architecture is
considered stable across multiple new-feature primitives (Stage 5
delivers the dispatch; Stage 5+ ships real new-feature primitives
that exercise the dispatch in production).

## 5. Which backend doctrine does this module operationalise?

- **FM1** (module identity) — folder name equals the synthetic
  `__smoke_test_tool` id exactly.
- **FM3** (surface-tier capability declaration) — claims the
  `paused` runtime tier + all 4 capability tiers.
- **FM6** (unsupported-reason gating) — `unsupportedReason`
  populated per the `paused` tier.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM8** (surface-file contract) — every claimed capability tier
  has a matching populated surface file.
- **FM10** (THESIS discipline) — this file.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` runs the
  standard invariant helper.
- **FM12** (loader presence) — registered in `src/modules/index.ts`.

The cross-side parity check at `tools/check_module_parity.py`
filters tool names starting with `__` so this fixture isn't compared
to the backend's primitive registry.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-26 | Stage 5 — initial fixture for the module-first dispatch acceptance test. |
