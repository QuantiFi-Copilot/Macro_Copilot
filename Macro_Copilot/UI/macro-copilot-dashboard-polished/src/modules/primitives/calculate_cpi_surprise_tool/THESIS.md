# THESIS — `calculate_cpi_surprise_tool`

> Stage 5 reference implementation — the first new-feature primitive shipped through the module-first dispatch architecture.

**Version:** v3 (Stage 5)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cpi_surprise_tool` (generic_runnable)
**Tier set:** `[generic_runnable, monitor_surface, ask_surface]`
**Category:** `economic_release_surprises`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; the generic schema-driven builder (`GenericPrimitiveBuilder`) handles the Build entry path.  No `surfaces.build` claim — the generic builder + AutoRenderer is sufficient.
- **`monitor_surface`** — bespoke bento card at `surfaces/monitor/CpiSurpriseWidget.tsx`.  Registered via `MODULE.monitorWidgets[0]` with widget id `cpi_surprise_latest`.  Stage 4d's catalog walker discovers it through the module spec.
- **`ask_surface`** — bespoke assistant card at `surfaces/AskCard.tsx`, referenced by `MODULE.surfaces.ask`.  Stage 5's `ConversationCanvas.resolveAssistantCard` reaches it when an assistant turn's terminal tool is `calculate_cpi_surprise_tool`.

## 2. What does the user read off each surface?

* **Build (generic builder).** Controls rail generated from the backend `ToolCard.input_fields` (country, event_type, window length, etc.).  Output canvas renders the per-release surprise time series via `AutoRenderer`.
* **Monitor (`cpi_surprise_latest`).** Latest CPI release per country — actual vs consensus, surprise in percentage points, rolling-z signal.  Reads from `RatesDataProvider` context (live aggregated rates-page payload); falls back to honest "no context" tile when mounted outside the provider.
* **Ask card.** Bespoke chat-result card with a CPI-themed kicker.  Inherits the standard research-card chrome but provides per-tool framing.

## 3. Why these surfaces and not others?

CPI Surprise is the canonical event-signal primitive — the desk reads it at a glance every release (warrants the Monitor card), and "what's the latest CPI surprise" is a high-frequency Ask query (warrants the bespoke chat card).  No bespoke Build surface today because the generic builder + AutoRenderer renders the per-release series natively; the typed-detail endpoint return shape doesn't need a custom chart layout yet.

## 4. What would change the design?

- The release-by-release chart gains rich decomposition (per-component contributions, peer-country overlay) → claim `custom_build_surface` and ship `surfaces/BuildSurface.tsx`.
- The Monitor card gains a parameterised variant (user picks country + window) → add a second entry to `MODULE.monitorWidgets[]`.
- Persisted-artifact preview needs per-tool framing (e.g. "latest release · z=2.3") → claim `custom_preview_widget` and ship `surfaces/PreviewWidget.tsx` + `MODULE.modelAdapter`.

## 5. Which backend doctrine does this module operationalise?

- **FM1** — folder name equals backend `tool_name` exactly.
- **FM3** — claims runtime tier `generic_runnable` + capabilities `[monitor_surface, ask_surface]`.
- **FM7** — `module.ts` exports a pure value.
- **FM8** — `monitor_surface` populated via `MODULE.monitorWidgets[]` (Stage 4d shape); `ask_surface` populated via `MODULE.surfaces.ask`.
- **FM10** — this file.
- **FM11** — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** — module imported in `src/modules/index.ts`.

---

## One-line summary

Per-release CPI surprise series (actual − consensus_median, in percentage points of YoY CPI) for one country's headline CPI YoY print, plus a rolling z-score over a window of N releases.

---

## Stage 5 reference acceptance

Adding the bespoke Monitor + Ask surfaces required ZERO edits to:
- `src/components/build/BuildShell.tsx`
- `src/components/build/primitive/VirtualPrimitiveCanvas.tsx`
- `src/components/ask/ConversationCanvas.tsx`
- `src/components/monitor/registry.ts`
- `src/components/monitor/WidgetRenderer.tsx`
- `src/lib/toolNames.ts` (only DELETION of the Stage 1 hand-authored entry, never an edit-to-add-a-tool)

The module-first dispatch architecture walks to this folder automatically.  Adding the next CPI-like primitive = run `tools/scaffold_module.py` + edit the placeholder surfaces.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-05-26 | Stage 5 — added `monitor_surface` + `ask_surface` tiers and the bespoke surfaces.  First reference implementation of module-first dispatch. |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
