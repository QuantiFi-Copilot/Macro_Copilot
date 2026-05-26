# THESIS — `calculate_nfp_surprise_tool`

> Stage 6 reference implementation — second new-feature primitive through the module-first dispatch architecture.

**Version:** v3 (Stage 6)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_nfp_surprise_tool` (generic_runnable)
**Tier set:** `[generic_runnable, monitor_surface, ask_surface]`
**Category:** `economic_release_surprises`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; the generic schema-driven builder (`GenericPrimitiveBuilder`) handles the Build entry path.  No `surfaces.build` claim — the generic builder + AutoRenderer is sufficient for the per-release surprise series.
- **`monitor_surface`** — bespoke bento card at `surfaces/monitor/NfpSurpriseWidget.tsx`.  Registered via `MODULE.monitorWidgets[0]` with widget id `nfp_surprise_latest`.  Stage 4d's catalog walker discovers it through the module spec.
- **`ask_surface`** — bespoke assistant card at `surfaces/AskCard.tsx`, referenced by `MODULE.surfaces.ask`.  Stage 5's `ConversationCanvas.resolveAssistantCard` reaches it when an assistant turn's terminal tool is `calculate_nfp_surprise_tool`.

## 2. What does the user read off each surface?

* **Build (generic builder).** Controls rail generated from the backend `ToolCard.input_fields` (window length, etc.; country + event_type are YAML-locked to US + nfp).  Output canvas renders the per-release surprise time series via `AutoRenderer`.
* **Monitor (`nfp_surprise_latest`).** Latest US nonfarm-payrolls release — actual vs consensus in thousands, surprise direction, rolling-z signal.  Mounts inside `RatesDataProvider` context on the live Monitor page; falls back to honest "no context" tile in test environments.
* **Ask card.** Bespoke chat-result card with an NFP-themed kicker.  Inherits the standard research-card chrome but provides per-tool framing for chat queries.

## 3. Why these surfaces and not others?

NFP is the macro print most directly read into the front-end Treasury curve — the desk watches the release on NFP Friday at the second it lands.  That justifies the Monitor card.  "What's the latest NFP surprise" is a high-frequency Ask query → bespoke chat card.  No bespoke Build surface today because the generic builder + AutoRenderer renders the per-release series natively.

## 4. What would change the design?

- Per-release decomposition (sector breakdown, revision impact) → claim `custom_build_surface` and ship `surfaces/BuildSurface.tsx`.
- Cross-release dashboard (CPI + NFP + ECI side-by-side) → either add a parameterised variant to `MODULE.monitorWidgets[]` or ship a sibling dashboard module.

## 5. Which backend doctrine does this module operationalise?

- **FM1** — folder name equals backend `tool_name` exactly.
- **FM3** — claims runtime tier `generic_runnable` + capabilities `[monitor_surface, ask_surface]`.
- **FM7** — `module.ts` exports a pure value.
- **FM8** — `monitor_surface` populated via `MODULE.monitorWidgets[]`; `ask_surface` populated via `MODULE.surfaces.ask`.
- **FM10** — this file.
- **FM11** — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** — module imported in `src/modules/index.ts`.

---

## One-line summary

Per-release US nonfarm-payrolls (NFP) surprise series (actual − consensus_median, in thousands of jobs) plus a rolling z-score over a window of N releases.

---

## Stage 6 reference acceptance

Adding the bespoke Monitor + Ask surfaces required ZERO edits to:
- `src/components/build/BuildShell.tsx`
- `src/components/build/primitive/VirtualPrimitiveCanvas.tsx`
- `src/components/ask/ConversationCanvas.tsx`
- `src/components/monitor/registry.ts`
- `src/components/monitor/WidgetRenderer.tsx`
- `src/components/build/widgets/index.ts`
- `src/lib/toolNames.ts` (only DELETION of the Stage 1 hand-authored entry, never an edit-to-add-a-tool)

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-26 | Stage 6 — added `monitor_surface` + `ask_surface` tiers and the bespoke surfaces. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
