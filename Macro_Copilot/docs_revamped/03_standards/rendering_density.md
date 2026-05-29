# Rendering Density (Compact + Extended)

> Every primitive ships TWO Build-side views — a **compact view** for grid contexts and an **extended view** for full-canvas contexts — and the frontend dispatches between them based on the query's shape. Both views are MANDATORY. There is no opt-in. A primitive that ships only one view is non-compliant with this standard.

**Version:** v1
**Last reviewed:** 2026-05-28
**Status:** load-bearing primitive contractual obligation. Changes require an ADR in [`../05_decisions/`](../05_decisions/).
**Operationalises principles:** [P3](../00_thesis/01_non_negotiables.md) (consistency by contract), [P5](../00_thesis/01_non_negotiables.md) (honest disclosure), [P8](../00_thesis/01_non_negotiables.md) (closed-family discipline), [P10](../00_thesis/01_non_negotiables.md) (single source of truth), [PR8](../02_components/primitive/README.md) (single central methodology surface), [FM3](../02_components/frontend_module/README.md) (surface-tier capability declaration), [FM4](../02_components/frontend_module/README.md) (tier-set parsimony — overridden here for the dual-view mandate; see §1.2), [FM8](../02_components/frontend_module/README.md) (surface-file contract).
**See also:** [`methodology_exposure.md`](methodology_exposure.md) (standalone bridge contract that this standard composes with), [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md), [`../02_components/frontend_module/tiers.md`](../02_components/frontend_module/tiers.md), [`../02_components/surface_contract.md`](../02_components/surface_contract.md) §3.3.

---

## 1. Universal rule

> Every primitive module under this standard ships TWO Build-side view components:
>
>   - **`surfaces.buildExtended`** — the full-canvas view, mounted when the primitive is the focus of a single-tool query.
>   - **`surfaces.buildCompact`** — the grid-card view, mounted when the primitive appears as one node among many in a multi-tool query's DAG visualization.
>
> Both are REQUIRED. Both are part of the primitive's contractual obligation. A module that ships only one is non-compliant.

### 1.1 The reason both are mandatory

Multi-tool prompts are the default reality, not an edge case. A PM asking *"compare US 2s10s vs Bund 2s10s vs BTP 2s10s"* triggers three tool calls in a single supervisor turn; the frontend must render each tool's data in a grid. The grid card can NEVER be the same component as the full-canvas view (different size envelope, different chrome, different click affordances). Defaulting to a generic artifact-type fallback — as legacy modules do today — produces an inconsistent multi-tool experience where the "real yield level" tool looks like a generic Series widget instead of the desk-canonical real-yield framing.

The mandate is: every new tool ships the compact view from day one. Migrating legacy tools to this template is a separate, sequenced exercise (see §9), but **every new primitive starts compliant**.

### 1.2 Tier parsimony does not apply here

[FM4](../02_components/frontend_module/README.md) (tier-set parsimony) is the default discipline — claim the smallest tier set that satisfies the use case. This standard EXPLICITLY OVERRIDES FM4 for the dual-view mandate: the compact view is not an optional capability to be earned; it is a structural obligation. The justification is the multi-tool-query baseline (§1.1).

The standard preserves FM4 for other capability tiers (`custom_preview_widget`, `monitor_surface`, `ask_surface`) — those remain opt-in. Only the Build surface is dual-mandate.

---

## 2. The two views

### 2.1 Extended view (`surfaces.buildExtended`)

**Purpose.** The full Build canvas — the user's primary investigation surface for a single primitive.

**Mounts when.** The current Build context decodes to ONE primitive (single-tool query).

**Required content.**
- Controls strip — every Pydantic Input field that has `exposure: true` in the tool's config (per [`methodology_exposure.md`](methodology_exposure.md))
- Output canvas — chart + KPI strip + tabular detail as appropriate for the tool's output shape
- Methodology disclosure — the full per-call methodology card (sources, conventions used this run, theoretical reference, known limitations summary)
- Provenance footer — tool name + version + as-of-date + lineage hash if available

**Size envelope.** Full Build canvas — the surface owns the entire `BuildShell` content area (typical ~1024×720+).

### 2.2 Compact view (`surfaces.buildCompact`)

**Purpose.** A grid card representing the primitive's headline data, dense enough to convey the canonical desk read at a glance, sparse enough to fit alongside N siblings in a DAG visualization.

**Mounts when.** The current Build context decodes to ≥2 primitives (multi-tool query). Each primitive in the query renders as one node in the DAG visualization; that node's body is the primitive's compact view.

**Required content.**
- **Tool identity chip** — kicker showing `<displayName>` + instrument selector summary (e.g. `"Real Yield Level · USD_TIPS 10Y"`)
- **Headline metric(s)** — the canonical "first three numbers" a PM reads off this tool. For a level tool: current value + period change + z-score. For a spread tool: current spread + change. For a regime classifier: current state + confidence.
- **Sparkline** — a small chart of the most-relevant time series (typically the same series the extended view's main chart shows, but condensed)
- **Methodology disclosure (compact form)** — a one-line caveat OR an `(i)` info icon that surfaces the full methodology card on hover/click
- **Expand affordance** — an explicit button or click-target ("Open extended view" / "↗") that opens the extended view in a modal/drawer overlay with a breadcrumb back to the DAG
- **Tone cues** — sign-convention coloring (e.g. positive change in green/red per desk convention; |z| ≥ 1.5 in amber; |z| ≥ 2.0 in coral/mint per direction)

**Size envelope.** Target ~400×280px (small), with optional larger variants up to ~600×420px (medium). The component MUST adapt to a `size` prop passed by the parent DAG renderer.

**What the compact view is NOT.**
- It is NOT a "smaller version of the extended view." It is a deliberately-curated condensed representation of the tool's headline data, NOT a shrink-to-fit of every KPI.
- It does NOT carry the controls strip. Editing parameters is done via the click-to-expand path, which opens the extended view with editable controls.
- It does NOT show the full methodology card inline; that goes into the hover-tooltip / extended view.
- It does NOT fetch the tool's full payload if a smaller summary endpoint exists — but in practice the typed-detail endpoint returns the same payload to both views (the compact view just renders less).

---

## 3. Context dispatch — single-tool vs multi-tool

The frontend's `BuildShell` resolves which view to mount based on the decoded Build context.

### 3.1 Single-tool query

**Trigger.** The URL's `?context=` (or workspace replay) decodes to exactly ONE primitive's tool call.

Examples:
- Library "Open in Build" for `get_real_yield_level_tool`
- Ask handoff for *"where's TIPS 10Y real yield?"* → 1 tool call
- Direct deep-link with one `?context=` entry

**Dispatch.** Build canvas mounts `MODULE.surfaces.buildExtended` directly as the entire canvas content.

**Click-through to compact.** Not applicable — the extended view IS what's shown.

### 3.2 Multi-tool query

**Trigger.** The URL's `?context=` (or workspace replay) decodes to ≥2 primitive tool calls.

Examples:
- Ask handoff for *"compare US 2s10s vs Bund 2s10s vs BTP 2s10s"* → 3 tool calls (same primitive, different params)
- Ask handoff for *"show me TIPS 10Y real yield AND breakeven inflation at 10Y"* → 2 tool calls (different primitives)
- Future ad-hoc-DAG workspace replays

**Dispatch.** Build canvas mounts a **DAG visualization** containing N nodes. Each node's body is the corresponding primitive's `MODULE.surfaces.buildCompact`.

The DAG visualization itself is FRONTEND INFRASTRUCTURE — shared chrome that all multi-tool queries use, NOT a per-primitive concern. It owns: the node layout (sibling-grid for parallel calls; topological for dependent calls), the edge rendering, the query-root header, the "expand-all / collapse-all" affordances, and the global breadcrumb. Per-primitive modules contribute the NODE BODIES via `buildCompact`; they do not own the DAG itself.

### 3.3 The expand affordance (click-to-extended)

Inside a multi-tool DAG, every compact node MUST expose an expand affordance. Clicking it opens the EXTENDED view of that specific primitive in a modal / drawer overlay.

Contract:
- The modal mounts the module's `surfaces.buildExtended` component, hydrated with the same params the compact card was using
- The modal carries a breadcrumb chip linking back to the multi-tool DAG (e.g. *"← Back to <query>"*)
- The modal is dismissible (close button, ESC key, click-outside per UX convention)
- Closing the modal returns the user to the DAG view with the originating node visually highlighted
- The expand affordance is rendered by the per-primitive `buildCompact` component (so the per-tool design can place the button where it fits the layout); the modal mounting + breadcrumb is shared infrastructure

The implementation may choose modal vs side-drawer vs full-screen-takeover; the contract requires only the semantic affordance + breadcrumb + dismissal. The compact view's expand action invokes a shared `openExtendedView(toolName, params)` infrastructure helper rather than the per-tool component implementing modal logic.

### 3.4 Boundary cases

- **N=1 multi-tool query** — a context with exactly one decoded primitive is treated as a single-tool query (dispatch to extended). The N=1 case is degenerate; do NOT render a 1-node DAG.
- **All-N-are-same-primitive** — e.g. 3 curve_spread calls. Still a multi-tool query (multiple tool calls); each gets a compact card. Duplicate-call ordinals can be surfaced inside the compact card per the existing `CallMeta` pattern in [`MultiPrimitiveCanvas`](../../UI/macro-copilot-dashboard-polished/src/components/build/primitive/MultiPrimitiveCanvas.tsx).
- **Decoded context contains 1 primitive + N operators** — the operators are intermediate-data nodes. The DAG visualization is mounted (≥2 nodes). The primitive node uses `buildCompact`; operator nodes use the artifact-type widget registry. Operators do not have frontend modules and are not bound by this contract.
- **Decoded context contains 0 primitives (only operators or only unsupported tools)** — render the existing unsupported / paused affordance per the surface contract; the dual-view mandate doesn't apply because there's no primitive to render.

---

## 4. Multi-tool queries vs workflow templates — two different things

Two distinct concepts that share the word "multi-tool" but are NOT the same backend concern:

| Concept | Source | Scope of this standard |
|---|---|---|
| **Multi-tool query (ad-hoc DAG)** | The LLM stitches together a sequence of primitive calls AT QUERY TIME to answer an open-ended multi-step prompt. The DAG is implicit in the supervisor turn's tool-call sequence. No backend template defines it; it's emergent. | **IN SCOPE.** §3.2 defines the dispatch. |
| **Workflow template** (e.g. `event_study`, `regime_conditioned_relationship`, `backtest`) | A backend-declared DAG recipe at `rates_agent/workflows/<template_id>/template.yaml`. The structure is pre-defined; users only fill in slot values. The supervisor invokes the whole template as one logical call. | **DEFERRED.** Future workflow contract (analogous to the primitive contract) will define its own surface rules. **Provisional rule:** workflow templates also mount per-primitive `buildCompact` for each primitive node in the DAG; never the extended view inline. The full workflow contract may extend or override this. |

The two are NOT collapsed. Workflow templates have their own backend artifacts (`WorkflowTemplate`, `template.yaml`, role schemas, slot schemas, archetype-specific dashboards) that the future workflow contract will govern.

Per-primitive modules under this standard only need to ship `{buildExtended, buildCompact}` — both views are surface-agnostic about whether they're being mounted from an ad-hoc multi-tool query or a workflow-template DAG. The dispatch is the frontend's job, not the primitive's.

---

## 5. Frontend module spec extension

The new contractual fields on `PrimitiveModuleSpec`:

```ts
export interface PrimitiveModuleSpec {
  // ... existing fields ...

  surfaces?: {
    // NEW — both REQUIRED when the module claims custom_build_surface
    // under this standard.  Legacy modules carve-out documented in §9.
    /** Full-canvas Build view — mounted for single-tool queries. */
    buildExtended?: ComponentType<BuildExtendedProps>;
    /** Grid-card Build view — mounted as a node body inside multi-
     *  tool query DAG visualizations.  Carries the expand affordance
     *  that opens buildExtended in a modal overlay. */
    buildCompact?: ComponentType<BuildCompactProps>;

    // Existing fields (unchanged):
    build?: ComponentType<any>;            // LEGACY — pre-density-contract; will be renamed → buildExtended in the implementation PR
    resultRenderer?: ComponentType<any>;   // LEGACY — typed-view payload renderer
    preview?: ComponentType<any>;          // unchanged — persisted-artifact preview
    monitor?: ComponentType<MonitorWidgetProps>;  // unchanged — Monitor surface is inherently compact
    ask?: ComponentType<any>;              // unchanged — Ask surface is inherently compact
  };
}
```

### 5.1 Surface-prop interfaces

```ts
/** Extended Build view props.  Same shape as today's
 *  ``BuildSurfaceProps`` — full decoded context, toolName, params,
 *  askHandoff flag.  Surfaces own the entire canvas: controls,
 *  fetch dispatch, output rendering. */
export interface BuildExtendedProps {
  toolName: string;
  params: Record<string, string>;
  decoded: DecodedPrimitive;
  askHandoff?: boolean;
}

/** Compact Build view props.  Adds ``size`` for the DAG renderer
 *  to negotiate envelope, and ``onExpand`` for the click-to-extended
 *  affordance.  The compact component does NOT receive controls
 *  state (editing is done via the expanded modal).  ``callMeta``
 *  surfaces the per-call ordinal when N copies of the same primitive
 *  appear in one DAG (e.g. 3 curve_spread calls). */
export interface BuildCompactProps {
  toolName: string;
  params: Record<string, string>;
  size: 'small' | 'medium';
  /** Invoked when the user clicks the compact view's expand
   *  affordance.  Shared infrastructure handles modal mounting +
   *  breadcrumb; the per-tool component just renders the trigger. */
  onExpand: () => void;
  /** Per-call ordinal when ≥2 cards of the same (toolName, params)
   *  appear in one DAG.  Undefined for unique cards. */
  callMeta?: { n: number; m: number };
}
```

### 5.2 The Stage-5 `surfaces.build` / `surfaces.resultRenderer` legacy fields

The current `PrimitiveModuleSpec` has both `surfaces.build` (full canvas) and `surfaces.resultRenderer` (payload-only — for tools that route through a shared typed view). These remain as LEGACY-COMPATIBLE fields during the migration window.

Under this standard:
- New modules MUST populate `surfaces.buildExtended` + `surfaces.buildCompact`.
- New modules MUST NOT populate `surfaces.build` (the legacy field) — `buildExtended` is the new name.
- New modules MUST NOT populate `surfaces.resultRenderer` — that field belongs to the legacy shared-typed-view pattern that the standalone-module bridge contract (per [`methodology_exposure.md §5`](methodology_exposure.md)) explicitly retires for new modules.
- Legacy modules using `surfaces.build` (or `surfaces.resultRenderer`) are non-compliant with this standard and listed in §9.

The TypeScript field rename (`surfaces.build` → `surfaces.buildExtended`) is an implementation-PR concern, not a contract concern. The contract specifies the SEMANTIC requirement: every new primitive ships two Build-side view components.

---

## 6. The compact view's content specification

Per §2.2, the compact view's required content is a SHORT LIST. Per-tool variation lives in the THESIS — what the desk-canonical "first three numbers" ARE for this specific tool is a per-tool design call, not a one-size-fits-all template.

The compact view's design decisions live in the module's `THESIS.md` Question 1 + Question 2 (per the [thesis template](../02_components/frontend_module/thesis_template.md)):
- **Question 1** lists "extended" and "compact" as the two Build surfaces the module ships.
- **Question 2** describes what the user reads off EACH surface — for the compact, this names the headline metrics, the sparkline series, and the expand-trigger.
- **Question 3** justifies why the compact view's curated metrics are the right choice for this specific tool.

The CONTRACT bounds the shape; the THESIS records the per-tool design rationale.

---

## 7. The extended view's content specification

Unchanged from the existing surface obligations on `surfaces.build`. The extended view is the full canvas — controls, output, methodology, provenance — and every primitive already needs one. This standard does NOT modify the extended view's requirements; it ADDS the compact view as a parallel sibling.

---

## 8. Other surfaces (Monitor, Ask, Library) are inherently compact

The density distinction is **Build-specific**. Other surfaces have their own inherent size envelopes that make a separate compact/extended split unnecessary:

| Surface | Density nature | Module obligation |
|---|---|---|
| **Build** | dual — extended + compact (per this standard) | both views required for every new module that ships a custom Build surface |
| **Monitor** | inherently compact (Monitor bento card constraint) | existing `surfaces.monitor` / `MODULE.monitorWidgets[]` — already compact |
| **Ask** | inherently compact (chat-bubble constraint) | existing `surfaces.ask` — already compact |
| **Library** | inherently compact (catalog tile constraint) | no JSX — auto-derived from manifest |

A future standard MAY introduce density distinctions for other surfaces if a real use case emerges (e.g. an "expanded Monitor" full-screen takeover). Today the only surface with a dual-density obligation is Build.

---

## 9. Legacy migration

The dual-view contract binds **new primitive modules from this standard's landing forward**. Legacy modules that ship only `surfaces.build` (or only `surfaces.resultRenderer`) are EXPLICITLY non-compliant — recorded as a known carve-out:

- Their per-tool `LIFECYCLE_CHECKLIST.md` Stage 4 row gets a `⊘ legacy carve-out` marker with a planned-migration pointer.
- Their `MODULE.surfaces.buildCompact` is absent; the frontend dispatcher falls back to the legacy artifact-type generic card (today's `MultiPrimitiveCard` / `NodeWidgetCard` artifact-type registry).
- Migration is per-tool, opportunistic per [`tool_lifecycle.md §6 Phase 3`](tool_lifecycle.md); the migration template IS this standard.

The Phase-1 pilot tools (`get_real_yield_level_tool` + `calculate_breakeven_inflation_simple_tool`) ship under this standard from day one — both views, no carve-out.

---

## 10. The DAG visualization (frontend infrastructure, NOT per-primitive)

The DAG visualization that hosts the compact views in multi-tool contexts is **shared frontend infrastructure**. Per-primitive modules contribute only the node bodies (`buildCompact`); they do NOT own the DAG container.

The DAG infrastructure owns:
- **Node layout** — sibling grid for parallel tool calls, topological layout for dependent calls
- **Edge rendering** — visual lines + labels for data dependencies (the LLM's stitching)
- **Query-root header** — the user's original question + a summary of the DAG ("3 tool calls, 1 instrument family, parallel")
- **Modal/drawer infrastructure** — the `openExtendedView(toolName, params)` helper that mounts a module's `buildExtended` in an overlay with a breadcrumb back to the DAG
- **Per-call ordinal handling** — when ≥2 cards share `(toolName, params)`, the infrastructure computes the `CallMeta` and passes it down to each compact view
- **Layout density** — choosing `size: 'small' | 'medium'` per node based on the total node count (N ≤ 3 → medium; N ≥ 4 → small; configurable)

This infrastructure is documented separately as part of the frontend platform architecture (NOT this standard). This standard SPECIFIES that such infrastructure exists and is the consumer of `buildCompact`; the implementation lives in `src/components/build/`.

---

## 11. Reviewer checks

- [ ] Every new primitive module declares BOTH `surfaces.buildExtended` and `surfaces.buildCompact` in `module.ts`.
- [ ] Both surface files exist at `surfaces/BuildExtended.tsx` and `surfaces/BuildCompact.tsx`.
- [ ] Module's THESIS.md Q1 lists BOTH "extended" and "compact" as the two Build-side surfaces shipped.
- [ ] Module's THESIS.md Q2 describes what the PM reads off EACH surface — concrete metrics for the compact view, not a generic "the user sees the result".
- [ ] Module's THESIS.md Q3 justifies the compact view's curated headline metrics (which "first three numbers" + why).
- [ ] The compact view renders within the target ~400×280px envelope at the `size='small'` setting.
- [ ] The compact view exposes an expand affordance that calls the `onExpand` prop; it does NOT mount its own modal.
- [ ] The compact view does NOT carry the controls strip — editing happens via the expanded view.
- [ ] The compact view surfaces the methodology disclosure (hover tooltip, info icon, or one-line caveat) — it does NOT silently drop it.
- [ ] The extended view's behaviour is unchanged from pre-standard expectations (full canvas, controls, output, methodology, provenance).
- [ ] The module does NOT populate `surfaces.build` or `surfaces.resultRenderer` (legacy fields — new modules use `buildExtended`).
- [ ] The per-tool `LIFECYCLE_CHECKLIST.md` Stage 4 has both view declarations as separate ☑ rows; Stage 6 has both surface files as separate ☑ rows.
- [ ] The per-module round-trip test (`__tests__/module.spec.ts`) asserts the presence of both surface fields + both files, per the FM11 invariant extension.

---

## 12. Anti-patterns

- A new primitive module that ships only `surfaces.buildExtended` and skips `surfaces.buildCompact`. **The contract requires both. No opt-in.**
- A compact view that is a literal shrink-to-fit of the extended view (same components, smaller fonts). **Compact = curated headline metrics, not a smaller copy.**
- A compact view that includes the controls strip (editable param dropdowns inline). **Controls belong in the extended view; the compact view delegates editing via the expand affordance.**
- A compact view that opens its OWN modal when the expand button is clicked. **Modal infrastructure is shared; the per-tool component only calls `onExpand()`.**
- A compact view that hides the methodology disclosure entirely. **Methodology must be reachable — tooltip, icon, or one-line caveat. Hidden methodology violates [P5](../00_thesis/01_non_negotiables.md).**
- A new primitive module that uses the legacy `surfaces.build` field instead of `surfaces.buildExtended`. **Legacy field is reserved for pre-standard tools; new modules use the new name.**
- A primitive that fetches a SMALLER summary endpoint for its compact view than for its extended view (creating two payload contracts). **Both views consume the same typed-detail endpoint; the compact view just renders less of it.**
- A frontend dispatcher that renders the extended view inside a DAG node instead of the compact view. **Multi-tool DAG nodes mount `buildCompact`, never `buildExtended` inline.**
- A workflow-template dashboard that mounts a primitive's extended view inline (instead of compact + expand-on-click). **Workflow templates follow the same compact-rendering provisional rule, pending the future workflow contract.**

---

## 13. Adoption

- **Pilot tools (Phase 1 of the `revamp` branch):** `get_real_yield_level_tool` first, then `calculate_breakeven_inflation_simple_tool`. Both ship under this standard from day one. Both views; both files; THESIS Q1–Q3 answer the dual-view design.
- **New primitives (Phase 2):** adopt from day one. Reject any new-module PR that ships only one view.
- **Existing primitives (Phase 3):** opportunistic migration per [`tool_lifecycle.md §6`](tool_lifecycle.md); each migrated tool ADDS its `buildCompact` + Stage 4 + Stage 6 rows in the per-tool checklist as part of the migration PR.

A primitive counts as **compliant with this standard** only when:

1. Both `surfaces.buildExtended` and `surfaces.buildCompact` are populated.
2. Both files exist at the canonical paths.
3. THESIS.md Q1 enumerates both; Q2 describes the per-surface read; Q3 justifies the compact view's curated metrics.
4. The module's round-trip test asserts the presence of both surface fields + both files.
5. The per-tool `LIFECYCLE_CHECKLIST.md` Stage 4 + Stage 6 are populated with both rows.

---

## 14. Links

- [P3, P5, P8, P10 — non-negotiables](../00_thesis/01_non_negotiables.md)
- [PR8 — single central methodology surface](../02_components/primitive/README.md)
- [FM3 — surface-tier capability declaration](../02_components/frontend_module/README.md)
- [FM4 — tier-set parsimony (overridden by this standard for the dual-view mandate)](../02_components/frontend_module/README.md)
- [FM8 — surface-file contract](../02_components/frontend_module/README.md)
- [FM10 — THESIS discipline](../02_components/frontend_module/README.md)
- [FM11 — round-trip test](../02_components/frontend_module/README.md)
- [`methodology_exposure.md`](methodology_exposure.md) — the standalone-module bridge contract that composes with this standard
- [`lifecycle_checklist_template.md`](lifecycle_checklist_template.md) — Stage 4 + Stage 6 rows that consume this standard
- [`../02_components/frontend_module/tiers.md`](../02_components/frontend_module/tiers.md) — tier vocabulary
- [`../02_components/frontend_module/thesis_template.md`](../02_components/frontend_module/thesis_template.md) — THESIS template Q1 / Q2 / Q3
- [`../02_components/surface_contract.md`](../02_components/surface_contract.md) §3.3 — Build states; single-tool vs multi-tool dispatch
- [`tool_lifecycle.md`](tool_lifecycle.md) §2 axis 6 — frontend surfaces

---

## 15. Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-28 | Initial standard.  Mandates the dual Build-view contract (`surfaces.buildExtended` + `surfaces.buildCompact`) for every new primitive — no opt-in.  Defines context-dispatch rules: single-tool queries mount the extended view; multi-tool queries mount a DAG visualization with each primitive's compact view + a click-to-expand affordance.  Distinguishes ad-hoc multi-tool queries (in scope) from workflow templates (deferred to future workflow contract).  Legacy modules carved out with explicit per-tool migration tracking.  Adopted by the Phase-1 pilot tools `get_real_yield_level_tool` + `calculate_breakeven_inflation_simple_tool`. |
