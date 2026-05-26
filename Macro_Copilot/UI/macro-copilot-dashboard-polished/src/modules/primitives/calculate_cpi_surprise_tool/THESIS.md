# THESIS — `calculate_cpi_surprise_tool`

> Event-class primitive. Surfaced through the default generic builder + generic Ask card.

**Version:** v4 (Stage 6 — surface-contract retraction)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_cpi_surprise_tool` (generic_runnable)
**Tier set:** `[generic_runnable]`
**Category:** `economic_release_surprises`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier.  Backend ships this primitive in `_PRIMITIVE_SPECS`; the generic schema-driven builder (`GenericPrimitiveBuilder`) handles the Build entry path, and the generic `AssistantResearchCard` handles the Ask result rendering.  The module ships no bespoke surface files — all four frontend surfaces (Library, Ask, Build, Monitor) are handled by the system defaults per the surface contract.

## 2. What does the user read off each surface?

* **Library.** Auto-derived from the backend manifest — the user finds this tool in the catalogue, filters by sub-agent / category, clicks through to the detail drawer.
* **Build (generic builder).** Controls rail generated from the backend `ToolCard.input_fields` (country, event_type, window length).  Output canvas renders the per-release surprise series via `AutoRenderer`.
* **Ask (generic research card).** Once the Supervisor routes CPI-surprise queries to the inflation_swaps domain (separate orchestration session), the 7-zone `AssistantResearchCard` renders the result with full chart + provenance + follow-ups.
* **Monitor.** Not surfaced — CPI Surprise is an event-class tool (one fire per release), not desk-glanceable state.  Excluded by the surface contract's Monitor eligibility rule.

## 3. Why these surfaces and not others?

CPI Surprise is a monthly-release event read.  The desk reads "what's the latest print" episodically, not as continuously-tracked board state.  That puts it firmly in the Ask + Build affordances, NOT the Monitor surface.  A bespoke Build canvas or Ask card would only add value if there were per-tool framing the generic surfaces miss; today there isn't — the generic schema-driven builder + 7-zone research card cover the user's read perfectly.

## 4. What would change the design?

- Bespoke chart layout (e.g. per-component CPI decomposition + peer-country overlay) → claim a capability tier and ship a per-tool surface file inside this folder.
- Per-tool framing on the Ask card that adds value over the generic 7-zone treatment (e.g. a structured header callout showing "surprise: +0.3pp · z=1.8" above the standard zones) → claim a capability tier and ship a per-tool surface that EXTENDS the generic, not replaces it.

## 5. Which backend doctrine does this module operationalise?

- **FM1** — folder name equals backend `tool_name` exactly.
- **FM3** — claims runtime tier `generic_runnable` only.  No capability tiers.
- **FM7** — `module.ts` exports a pure value.
- **FM10** — this file.
- **FM11** — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** — module imported in `src/modules/index.ts`.

---

## One-line summary

Per-release CPI surprise series (actual − consensus_median, in percentage points of YoY CPI) for one country's headline CPI YoY print, plus a rolling z-score over a window of N releases.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v4 | 2026-05-26 | Stage 6 — surface-contract retraction.  Dropped `monitor_surface` and `ask_surface` tier claims; deleted the stub widget + bespoke Ask card files.  Module now claims `generic_runnable` only.  Per the surface contract's containment principle: a module's tier claims must match delivery. |
| v3 | 2026-05-26 | Stage 5 — added `monitor_surface` + `ask_surface` tier claims and the bespoke surfaces.  Subsequently retracted in v4 (the shipped widget + card didn't deliver value over the defaults). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only. |
