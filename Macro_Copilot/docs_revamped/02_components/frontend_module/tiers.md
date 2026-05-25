# Frontend Module — Surface Tiers (Closed Family)

> Full per-tier semantics, mutually-exclusive rules, when to claim each tier, and the surfaces each tier ships. Authoritative reference for `MODULE.tiers` validation.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. The tier set is a closed family per P8; extensions require an ADR in [`../../05_decisions/`](../../05_decisions/).
**Operationalises principles:** [FP3](../../00_thesis/03_frontend_thesis.md), [FM3](README.md), P8 (closed-family discipline).
**See also:** [`README.md`](README.md) — module contract; [`runbook.md`](runbook.md) — how to add a module.

---

## The closed family

```ts
type SurfaceTier =
  // Runtime status — choose EXACTLY ONE per module:
  | 'generic_runnable'
  | 'workflow_incompatible'
  | 'paused'
  | 'deferred'

  // Capability tiers — combine freely (subject to per-tier rules below):
  | 'custom_build_surface'
  | 'custom_preview_widget'
  | 'monitor_surface'
  | 'ask_surface'
;
```

The tier set is closed. Adding a new tier requires an ADR amending this file + [`../../05_decisions/0014-frontend-module-architecture.md`](../../05_decisions/0014-frontend-module-architecture.md). Tier names use snake_case to mirror backend identifier conventions.

## Validation rules (mechanical)

A module's `MODULE.tiers` is valid iff:

1. It is a non-empty subset of the closed family.
2. It contains exactly one of `{generic_runnable, workflow_incompatible, paused, deferred}` (the runtime-status group).
3. For each capability tier in the set, the corresponding `surfaces/<Name>.tsx` exists and is referenced from `MODULE.surfaces.<key>`.
4. For each capability tier NOT in the set, the corresponding `surfaces/<Name>.tsx` does NOT exist and `MODULE.surfaces.<key>` is undefined.
5. If the set contains `workflow_incompatible | paused | deferred`, `MODULE.unsupportedReason` is non-null with all three fields non-empty.
6. If the set is exactly `{deferred}`, no capability tier is present, no `surfaces/` files exist, and THESIS.md is the minimal reservation form.

The per-module round-trip test (FM11) asserts all six.

---

## Runtime-status tiers

A module claims exactly one. The runtime status is what the user effectively gets when they try to run this primitive through the standard path.

### `generic_runnable`

**Means.** The backend has registered this primitive in `_PRIMITIVE_SPECS`. The frontend can route through `GenericPrimitiveBuilder` + `AutoRenderer` to configure + run + render the output via `POST /api/v1/tools/{name}/run`.

**Verify by claiming this tier.**
- The backend's `rates_agent/workflows/__init__.py` registers this `tool_name` in `_PRIMITIVE_SPECS`.
- A live call to `GET /api/v1/tools/{tool_name}` returns a `ToolCard` with `input_fields`, `output_artifact_type`, and `methodology`.
- A live call to `POST /api/v1/tools/{tool_name}/run` with valid params returns a result the AutoRenderer can render (Series, Panel, etc.).

**Surface contribution.** When the user opens this tool from Library or Ask handoff, the Build canvas mounts `GenericPrimitiveBuilder` against this tool. The module MAY additionally claim `custom_build_surface` to provide a bespoke builder instead; the generic remains the fall-through.

**When to claim.** Default for any newly-shipped primitive whose backend `_PRIMITIVE_SPECS` entry returns a Series or Panel artifact.

**When NOT to claim.** The backend has the primitive in `WORKFLOW_INCOMPATIBLE_TOOLS` instead of `_PRIMITIVE_SPECS` (output shape is not bridge-compatible). Use `workflow_incompatible`.

**Mutually exclusive with.** `workflow_incompatible`, `paused`, `deferred`.

**Implies (about `unsupportedReason`).** Must be `null` / omitted.

### `workflow_incompatible`

**Means.** The backend has the primitive in the `WORKFLOW_INCOMPATIBLE_TOOLS` dict in [`rates_agent/workflows/__init__.py`](../../../rates_agent/workflows/__init__.py) (search for `WORKFLOW_INCOMPATIBLE_TOOLS`). The primitive is real, shipped, callable via the MCP boundary — but its output shape is intentionally NOT bridge-compatible (categorical labels, list of meeting snapshots, SCD2 transition logs). The generic `POST /tools/{name}/run` route in [`api/routes/workflows/execute.py`](../../../api/routes/workflows/execute.py) returns an HTTP 200 envelope `{"ok": false, "tool_name": "...", "error": "...", "known_tool_names": [...]}` for unregistered primitives — not a 500 — but the result is still useless to the user because there is no live execution path. The `workflow_incompatible` tier exists so the UI surfaces an honest paused/typed-detail card instead of routing the user into that empty envelope.

Today the closed set is three tools:
- `classify_curve_move_tool` — categorical regime label.
- `get_otr_history_tool` — SCD2 transition log + identifier snapshot.
- `calculate_wirp_meeting_pricing_tool` — per-meeting WIRP snapshots.

**Verify by claiming this tier.**
- The backend's `WORKFLOW_INCOMPATIBLE_TOOLS` dictionary contains this `tool_name`.
- The `MODULE.unsupportedReason.reason` field quotes (or paraphrases) the backend's published rationale.

**Surface contribution.** Build renders the `UnsupportedKnownToolCanvas` (honest paused-style card) with the module's `unsupportedReason` text. The module SHOULD additionally claim `custom_build_surface` to provide a bespoke canvas that calls a typed-detail endpoint when one exists (e.g. `classify_curve_move_tool` has a typed `regime` view backed by `/api/v1/rates/detail/regime`).

**When to claim.** A primitive in the backend's `WORKFLOW_INCOMPATIBLE_TOOLS` dict.

**When NOT to claim.** The primitive is fully runnable via the bridge. Use `generic_runnable`.

**Mutually exclusive with.** `generic_runnable`, `paused`, `deferred`.

**Implies (about `unsupportedReason`).** Must be present with all three fields.

### `paused`

**Means.** The primitive is declared in the manifest YAML (the Library shows it as a catalogue entry) but has no `_PRIMITIVE_SPECS` entry AND no entry in `WORKFLOW_INCOMPATIBLE_TOOLS`. The backend cannot run it through any path today — it is a placeholder / unfinished / unblocked-pending-data tool.

Today the closed set is one tool:
- `scan_ois_extremes_tool` — declared in the OIS manifest; no compute implementation; no typed-detail endpoint.

**Verify by claiming this tier.**
- The tool appears in `manifesto/03_tool_manifest/rates_agent/*.yml` as a `tool_function:` entry.
- The tool does NOT appear in `_PRIMITIVE_SPECS`.
- The tool does NOT appear in `WORKFLOW_INCOMPATIBLE_TOOLS`.

**Surface contribution.** Build renders the paused card with `unsupportedReason.reason` describing what's missing and `unsupportedReason.whatWorksNow` pointing the user at the closest available alternative (sibling primitive, Ask path).

**When to claim.** A primitive that's manifest-only with no backend implementation.

**When NOT to claim.** The primitive ships through MCP but not through the workflow bridge — that's `workflow_incompatible`, not `paused`.

**Mutually exclusive with.** `generic_runnable`, `workflow_incompatible`, `deferred`.

**Implies (about `unsupportedReason`).** Must be present with all three fields.

### `deferred`

**Means.** A reserved name. The backend doesn't ship this primitive in any form, but the name is reserved for a planned feature (e.g. an enum slot in `WORKFLOW_ARCHETYPES` that doesn't yet have a template module). The frontend module exists to:
- Reserve the folder name (FM1 — no naming conflicts when the backend ships it).
- Document the reservation rationale (THESIS Question 5).
- Render a "reserved name" card if the URL is ever hit.

Today the closed set is workflow-level only:
- `attribution_decomposition` (workflow_module) — TD #35.
- `cross_sectional_screen` (workflow_module) — Phase 2.5.

**Verify by claiming this tier.**
- The name appears in a backend reservation surface (`WORKFLOW_ARCHETYPES` enum slot, an open TD entry, an ADR future-direction).
- No backend implementation exists (no template module, no `_PRIMITIVE_SPECS` entry).

**Surface contribution.** Build renders a "reserved name" card explaining the reservation. The module ships NO capability tiers and NO `surfaces/` files.

**When to claim.** A reserved name with a documented backend intent and no implementation.

**When NOT to claim.** The primitive / workflow is real but paused — use `paused`. The primitive / workflow is in development on a non-build branch — wait until it lands; don't anticipate.

**Mutually exclusive with.** `generic_runnable`, `workflow_incompatible`, `paused`, and ALL capability tiers (a deferred module ships no surfaces).

**Implies (about `unsupportedReason`).** Must be present; `whatWorksNow` describes when the reservation may be lifted.

---

## Capability tiers

Capability tiers describe what bespoke UI the module ships beyond the runtime-status defaults. A module may claim any combination, subject to the rules below.

### `custom_build_surface`

**Means.** The module ships a bespoke React component at `surfaces/BuildSurface.tsx` (default export) that the Build canvas renders when this tool is opened. Replaces the generic `GenericPrimitiveBuilder` for this tool.

**Files.** `surfaces/BuildSurface.tsx` with default export `React.FC<BuildSurfaceProps>`.

**Spec field.** `MODULE.surfaces.build` references the component.

**When to claim.** The module's runtime status is `workflow_incompatible` (so there's no generic-run path and the user needs a real surface), OR the module is rich-model and uses `BuilderCanvas` (`richModel: true`), OR the THESIS justifies a bespoke layout the generic builder cannot achieve.

**When NOT to claim.** The generic builder + AutoRenderer renders this primitive's output acceptably and no domain-specific framing is missing. Default to NOT claiming; let the generic surface work.

**Combines with.** Any runtime-status tier except `deferred`. Strongly encouraged with `workflow_incompatible`.

**Anti-patterns.**
- Claiming this tier because the generic builder is "ugly." Improve the generic builder.
- Claiming this tier to add a single interpretation card. Use `MODULE.interpretationCards` instead — they render on the generic surface.

### `custom_preview_widget`

**Means.** The module ships a bespoke React component at `surfaces/PreviewWidget.tsx` (default export typed as `NodeRenderer` per the per-tool node-renderer-registry contract) that renders the persisted-artifact preview card for this tool in the Build completed view (the bento cards inside a workspace).

**Files.** `surfaces/PreviewWidget.tsx` with default export of type `NodeRenderer`.

**Spec field.** `MODULE.surfaces.preview` references the component.

**When to claim.** The persisted artifact for this tool has a per-tool layout that the generic per-type widgets (SeriesWidget, PanelWidget, etc.) cannot match. Examples: PCA's loadings + variance breakdown; a regime classifier's label + supporting evidence.

**When NOT to claim.** The persisted artifact is a plain Series or Panel that renders adequately via the generic per-type widget.

**Combines with.** Any runtime-status tier except `deferred`.

**Anti-patterns.**
- Claiming this tier purely for visual styling. The per-type widgets carry the styling; per-tool preview should be claimed only when the *content* is different.

### `monitor_surface`

**Means.** The module ships a bespoke React component at `surfaces/MonitorWidget.tsx` that renders this tool as a bento card on the Monitor / Rates Agent surface. Contributes one entry to the Monitor widget catalogue (`WIDGET_TYPES`).

**Files.** `surfaces/MonitorWidget.tsx` with default export `React.FC<MonitorWidgetProps>`.

**Spec field.** `MODULE.surfaces.monitor` references the component. Additional metadata: `MODULE.monitorMeta` carrying `defaultSize`, `allowedSizes`, `parameterized` flag, and `paramFields` (per the WidgetTypeMeta contract).

**When to claim.** The desk reads this tool at a glance every day; a Monitor bento card is the canonical surface. Examples: yield snapshot, curve spreads, cross-market spreads, scanner, event-feed.

**When NOT to claim.** The tool is a Build-only deep-dive surface; a Monitor card would be either too dense or too thin to be useful. Also: NEVER claim alongside `deferred` — deferred modules ship no surfaces by definition.

**Combines with.** Any runtime-status tier EXCEPT `deferred`. With `paused` is allowed (a paused tool MAY have a Monitor card that renders the paused state inline).

**Anti-patterns.**
- A Monitor widget that re-implements the Build surface in a smaller frame. Monitor surfaces serve a different purpose (at-a-glance daily read); they're not miniature Build surfaces.
- A Monitor widget that fetches the same large payload the Build surface does. Monitor widgets read aggregated / pre-summarised data; per-instrument detail belongs in Build.

### `ask_surface`

**Means.** The module ships a bespoke React component at `surfaces/AskCard.tsx` that the Ask `ConversationCanvas` renders for results from this tool. Replaces the generic `AssistantResearchCard` for this tool.

**Files.** `surfaces/AskCard.tsx` with default export `React.FC<AskCardProps>`.

**Spec field.** `MODULE.surfaces.ask` references the component.

**When to claim.** The tool's result needs domain-specific framing inline in the chat that the generic card cannot provide. Examples: event-signal primitives (CPI / NFP surprise — the chat-card layout shows the latest release inline with surprise/z-score band); regime classifier (label + confidence in chat).

**When NOT to claim.** The generic `AssistantResearchCard` renders the tool's result acceptably with KPIs + a sparkline. Most primitives do not need this tier.

**Combines with.** Any runtime-status tier except `deferred`.

**Anti-patterns.**
- Claiming this tier so the result card has a custom colour. Colours are general design system concerns; per-tool ask cards are for SHAPE differences, not styling.
- Claiming this tier to embed a full Build-style canvas in the chat. The chat surface has space constraints; per-tool ask cards are summary-grade.

---

## Tier-combination matrix

The combinations observed in practice (and anticipated for the catalogue):

| Primitive shape | Typical tier set |
|---|---|
| Plain snapshot (`get_yield_levels_tool`) | `['generic_runnable']` |
| Snapshot + daily monitor (`calculate_curve_spread_tool`) | `['generic_runnable', 'monitor_surface']` |
| Scanner (`scan_extremes_tool`) | `['generic_runnable', 'monitor_surface']` (typed view via `MODULE.typedView = 'scanner'`) |
| Rich-model fit (`calculate_pca_yield_curve_tool`) | `['generic_runnable', 'custom_build_surface', 'custom_preview_widget']` (rich-model via `MODULE.richModel = true`) |
| Event signal (`calculate_cpi_surprise_tool`) | `['generic_runnable', 'monitor_surface', 'ask_surface']` |
| Workflow-incompatible categorical (`classify_curve_move_tool`) | `['workflow_incompatible', 'custom_build_surface']` (typed view via `MODULE.typedView = 'regime'`) |
| Workflow-incompatible list (`get_otr_history_tool`) | `['workflow_incompatible']` or `['workflow_incompatible', 'custom_build_surface']` |
| Workflow-incompatible per-meeting (`calculate_wirp_meeting_pricing_tool`) | `['workflow_incompatible']` or `['workflow_incompatible', 'custom_build_surface']` |
| Paused (`scan_ois_extremes_tool`) | `['paused']` |
| Deferred workflow (`attribution_decomposition`) | `['deferred']` |

These are observed patterns. New modules pick the smallest set that fits.

## Closed-family extensions

Adding a new tier requires:

1. An ADR in [`../../05_decisions/`](../../05_decisions/) recording the closed-family extension with rationale, alternatives considered, and migration plan for existing modules.
2. An amendment to this file (a new section under the appropriate group) and to [`README.md`](README.md) (the FM3 quick-index table).
3. An amendment to the `SurfaceTier` union literal in `src/modules/types.ts` (file does not exist today; lands in Stage 2 alongside the first reference module).
4. A migration sweep across existing modules to either claim or explicitly NOT claim the new tier — drift between "the tier exists" and "no module ever claims it" is a P8 violation.

The closed family exists to prevent UI-tier proliferation. Extensions are deliberate, ADR-recorded, and rare.

## Open questions

1. **Library tier.** Today the Library catalogue is purely manifest-driven; a module does not "claim" Library participation. Should there be a `library_tile` tier for bespoke per-tool Library tiles? Today: no, the manifest is sufficient.

2. **DAG-node tier.** Today the DAG renderer is finance-blind and renders every node uniformly (FP13). Should there be a `dag_node_renderer` tier for per-tool DAG decorations? Today: no, the DAG renderer should remain shared.

3. **Multi-runtime-status modules.** Could a module be simultaneously `generic_runnable` AND `workflow_incompatible` (e.g. a primitive that has a bridge-compatible default method + a non-compatible alternative)? Today: no, the mutually-exclusive rule binds; if a primitive has a method that can't bridge, that *method* is workflow-incompatible (and the primitive may need to be split per backend PR2).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial tier catalogue. 4 runtime-status + 4 capability tiers; validation rules; per-tier semantics; combination matrix. |
