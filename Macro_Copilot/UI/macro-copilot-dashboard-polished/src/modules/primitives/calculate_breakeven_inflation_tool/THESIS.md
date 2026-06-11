# THESIS — `calculate_breakeven_inflation_tool`

> **PERMANENTLY RETIRED from bespoke-surface scope** (consolidation, 2026-06-11).
> This module is the documented dedup exception — see Q3/Q4.

**Version:** v3 (consolidation — G-3.1d dedup ruling)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `calculate_breakeven_inflation_tool` (generic_runnable)
**Tier set:** `[generic_runnable]`
**Category:** `cross_market_rv`

---

## 1. What surfaces does this module ship?

- **`generic_runnable`** — runtime-status tier only, **permanently**.  Backend
  ships this primitive in `_PRIMITIVE_SPECS` (the sovereign manifest preserves
  the registration "for backwards-compatible workflows that already named this
  tool under the sovereign catalogue"); the generic schema-driven builder
  (`GenericPrimitiveBuilder`) configures + runs + renders it, and DAG /
  workspace contexts render its artifacts via the finance-blind artifact-type
  widgets.  **No bespoke Build / Compact / Monitor / Ask surface will be
  added** — see Q3.

## 2. What does the user read off each surface?

**Build (generic builder).** The schema-driven `GenericPrimitiveBuilder`
mounts when the user opens this tool from Library / Ask handoff.  Controls
rail is generated from the backend `ToolCard.input_fields`; the output canvas
renders via `AutoRenderer`.  The Library card copy and `workspaceLabel` point
the user at **Breakeven Inflation** (`calculate_breakeven_inflation_simple_tool`,
inflation_indexed_bonds domain) — the canonical polished dual-view for the
same desk concept.

## 3. Why these surfaces and not others?

This is the **one true cross-domain duplicate** in the catalogue: the same
desk concept (bond-implied breakeven = nominal yield − matched-tenor linker
real yield) is registered twice — here under the sovereign sub-agent (legacy,
backwards-compatible workflows) and under `inflation_indexed_bonds` as
`calculate_breakeven_inflation_simple_tool` (the Phase-1 dual-view pilot, the
canonical surface).  Building a second bespoke UI for the same concept would
violate P10 (single source of truth) and duplicate every future design change.

A **surface redirect** (re-exporting the sibling's `BuildExtended`/
`BuildCompact` here) was evaluated and **rejected**: the two Pydantic Inputs
are not param-compatible (this tool: `real_curve_family` + `convention` +
`nominal_field_name`/`real_field_name`; the sibling: `linker_curve_family` +
single `field_name` + z-score knobs).  A param-translating wrapper would have
to silently drop or re-map fields — a P5/P6 honesty violation in a DAG context
where node params are the replayable record.

So: the tool stays honestly runnable (FM12/backend parity require the
`generic_runnable` tier while it lives in `_PRIMITIVE_SPECS`), renders
generically, and the copy routes humans to the canonical surface.  This is
the **documented exception** to the "every primitive ships dual-view" rule —
recorded in `tmp/prompt_tests/FRONTEND_CONSOLIDATION_AUDIT.md`.

## 4. What would change the design?

- **Backend retirement of the sovereign registration** (workflows migrated to
  the inflation-domain sibling) → delete this module folder in the same
  change (FM12 set-equality).
- **The two Inputs converging** (sovereign tool re-parameterised to the
  sibling's field names) → the redirect option becomes honest; revisit.
- Nothing else: desk demand for a breakeven surface is served by the sibling.

## 5. Which backend doctrine does this module operationalise?

- **P10** (single source of truth) — one canonical surface per desk concept;
  this module defers to the sibling rather than duplicating it.
- **P5 / P6** (honest disclosure / no silent failure) — the rejected
  redirect would have silently re-mapped params; the generic surface renders
  exactly what the backend computes.
- **FM1** (module identity) — folder name equals backend `tool_name` exactly.
- **FM3** (surface-tier capability declaration) — claims `generic_runnable`;
  no capability tiers, by ruling rather than by omission.
- **FM7** (pure-spec assembly) — `module.ts` exports a pure value.
- **FM10** (THESIS discipline) — this file records the dedup ruling.
- **FM11** (round-trip test) — `__tests__/module.spec.ts` calls
  `assertStandardModuleInvariants`.
- **FM12** (loader presence + parity) — module imported in
  `src/modules/index.ts`; the runnable tier mirrors `_PRIMITIVE_SPECS`.

---

## One-line summary

Bond-implied breakeven inflation surfaced via the sovereign sub-agent —
sibling of `calculate_breakeven_inflation_simple_tool` (inflation domain),
which carries the canonical Breakeven Inflation dual-view; this sovereign-side
registration is preserved for backwards-compatible workflows and renders
generically by ruling (G-3.1d dedup).

---

## Version log

| Version | Date | Change |
|---|---|---|
| v3 | 2026-06-11 | Consolidation G-3.1d — documented the permanent dedup ruling (no bespoke surface; redirect rejected on param-incompatibility; generic-only by design). |
| v2 | 2026-05-26 | Stage 4f — rewrote the body to drop stale "Stage 3 scaffold" framing and answer Q2–Q5 for real. |
| v1 | 2026-05-25 | Stage 3 scaffold — runtime tier only, no surfaces. |
