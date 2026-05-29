# shared_mockups

> Cross-tool design mockups that are NOT owned by any single primitive module.  Per-primitive mockups live in `primitives/<tool>/mockups/` (Compact.png + Extended.png per the dual-view contract); the artifacts here describe **shared frontend infrastructure** that hosts those per-tool surfaces.

## Why this folder exists (and why here)

The per-tool `mockups/` folders capture how ONE tool renders.  The multi-tool DAG view is shared chrome — it hosts every primitive's compact card as a node body and is owned by `src/components/build/`, not by any module.  Storing its mockup under a per-tool folder would imply ownership it doesn't have.  `src/modules/shared_mockups/` sits at the modules root, parallel to `primitives/`, so the cross-tool reference artifacts have an unambiguous home.  This placement is intentional and defensible — if the DAG infrastructure later grows its own component folder with a co-located `mockups/`, this file can point there instead.

## Artifacts

### `multi_tool.png`

The multi-tool query DAG visualization — the Stage-D shared infrastructure that mounts each primitive's `surfaces.buildCompact` as a node body when a query decodes to ≥2 primitives (per [`rendering_density.md §3.2 + §10`](../../../../docs_revamped/03_standards/rendering_density.md)).  The mockup shows two archetypes:

- **Parallel comparison** — N sibling nodes, no edges (e.g. *"compare US 10Y breakeven vs UK 10Y vs French 10Y"*).
- **Multi-step pipeline** — nodes with data-flow edges (one tool's output feeds another).

Each node body is the corresponding tool's compact card; the DAG owns the layout, edges, query-root header, the `openExtendedView(toolName, params)` modal infrastructure, per-call ordinals, and density (`size` per node count).

## Open design questions (resolve before building the Stage-D DAG container)

These were flagged during the mockup review and are NOT yet settled.  The DAG container implementation must resolve them:

1. **Expand-target semantics (R1).**  When a compact node's expand affordance fires, the extended view should open as a **modal/drawer overlay over the DAG** (per `rendering_density.md §3.3`: breadcrumb back to the query, ESC / click-outside dismiss, originating node highlighted on return) — NOT an in-place node expansion (which reflows siblings + re-routes edges, destroying the spatial anchor) and NOT a navigation OUT to single-tool Build.  The mockup should gain a second frame showing the expanded (modal) state to make this explicit.

2. **Multi-step dependency + partial-completion framing (R2).**  The "multi-step" archetype must make data-flow direction unambiguous (arrowhead + an "uses output of →" edge label) AND specify the partial-completion states: what a downstream node renders while an upstream tool is still running (skeleton), errored (inherited-error badge), or stale.  This is the orchestrator ⇄ renderer contract, not decoration.

3. **Query-header + "stitched by AI" disclosure (minor).**  Multi-tool queries are LLM-stitched ad-hoc plans; the DAG header should always surface the originating prompt + a "stitched by AI" disclosure so the user can see the plan that produced the topology.  A "regenerate query plan" affordance is a nice-to-have for a later iteration.

## Relationship to the dual-view contract

Per-primitive modules contribute only the node bodies (`buildCompact`); they do NOT own the DAG.  The two Stage-B/C pilot tools brought to parity with `get_real_yield_level_tool` —
`calculate_breakeven_inflation_simple_tool` and `calculate_real_yield_curve_spread_tool` — each ship a compact view designed to drop into this DAG, so by the time the DAG container is built there are 3 real primitives (same domain) to test it with a realistic multi-tool query (e.g. *"TIPS 10Y real yield + TIPS 5s10s real-yield curve + US 10Y breakeven"*).
