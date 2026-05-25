# Library Page

> The Library page shell — `LibraryPage.tsx` + AgentStrip + InstrumentStrip + CategoryChips + LibrarySearch + ToolCardGrid + ToolDetailDrawer. Manifest-driven catalogue; surfaces every tool the backend declares.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing page-shell contract. Changes that affect the manifest schema are coordinated with backend manifest changes.
**Operationalises principles:** [FP2](../../00_thesis/03_frontend_thesis.md), [FP12](../../00_thesis/03_frontend_thesis.md), [SI1](../frontend_infrastructure/README.md), P3, P10.
**See also:** [`../frontend_module/README.md`](../frontend_module/README.md), [`../frontend_infrastructure/services_and_types.md`](../frontend_infrastructure/services_and_types.md).

---

## What this is

`LibraryPage` is the page shell for `/library`. It is the manifest-driven catalogue of every tool the backend ships. It does NOT contain primitive-specific logic.

The composition:

```
┌─────────────────────────────────────────────────────────────┐
│  LibraryHeader  (tab counts, totals)                        │
├─────────────────────────────────────────────────────────────┤
│  LibraryTabs    (Primitives | Workflows | Operators (soon)) │
├─────────────────────────────────────────────────────────────┤
│  AgentStrip     (rates / fx / credit / ...)                 │
├─────────────────────────────────────────────────────────────┤
│  InstrumentStrip (all | sovereign | OIS | bond_futures ...) │
├─────────────────────────────────────────────────────────────┤
│  CategoryChips  (all | snapshots | curve_shape | ...)       │
├─────────────────────────────────────────────────────────────┤
│  LibrarySearch  (free-text)                                 │
├─────────────────────────────────────────────────────────────┤
│  ToolCardGrid   (per-tool cards from manifest)              │
└─────────────────────────────────────────────────────────────┘

                ToolDetailDrawer (right-slide on card click)
```

## The manifest is the source of truth

The Library reads from `GET /api/v1/library/manifest` exactly once on page load. The manifest is the backend's `manifesto/03_tool_manifest/` YAMLs, aggregated server-side into a typed response.

`useLibraryManifest` returns the typed response declared in [`src/types/library.ts`](../../../UI/macro-copilot-dashboard-polished/src/types/library.ts):

```ts
type LibraryManifestResponse = {
  agents: Record<string, AgentManifest>;
  total_tools: number;
};

type AgentManifest = {
  agent: string;
  tool_count: number;
  tools: ManifestTool[];
  sub_agent_counts: Record<string, number>;
  category_counts: Record<string, number>;
};

type ManifestTool = {
  name: string;
  domain: string;
  sub_agent: string;
  bucket: string;             // "1A" | "1B" | "2"
  category: string;           // see CATEGORY_LABELS in types/library.ts
  status: string;
  implementation: ToolImplementation;
  one_liner: string;
  bucket_rationale: string;
  pm_overridable: string[];
  related_tools: string[];
  workflows: string[];
  references: string[];
  built_date: string;
  validation_status: string;
};

type ToolImplementation = {
  mcp_server: string;
  tool_function: string;
  schema_: string;            // YAML "schema"; renamed to schema_ to avoid Pydantic clash
  compute: string;
  config: string;
};
```

Methodology + conventions are NOT in the manifest payload today — those live in the per-tool `ToolCard` returned by `GET /api/v1/tools/{name}` (consumed by the `ToolDetailDrawer` when the user opens a card).

Per FP4, the Library does NOT consult `ALL_PRIMITIVE_MODULES` for its catalogue — the *backend's* manifest is the source. Modules contribute to Library indirectly via the "Open in Build" CTA (which goes through the contextDecoder which DOES read derived registries).

## Per-strip semantics

### AgentStrip

Six rounded pill buttons: rates_agent (live), fx_agent (soon), credit_agent (soon), macro_equity (soon), policy_agent (soon), pm_orch (soon). Live agents are clickable; "soon" agents are disabled with a "ships in a follow-up" tooltip.

Counts come from the manifest (`agents[<agent_slug>].tool_count`, where `<agent_slug>` is the dict key — e.g. `agents['rates_agent'].tool_count`). Dimmed agents show "—".

Adding a new agent requires:
1. Backend ships the agent's manifest entry.
2. Frontend updates the AgentStrip's `AGENTS` array (id + label + icon + status).
3. Frontend updates `AGENT_LABELS` in `src/types/library.ts`.

### InstrumentStrip (sub-agent filter)

Shows sub-agents within the active agent (sovereign_bonds, ois, inflation_indexed_bonds, inflation_swaps, bond_futures, policy_futures for rates_agent). Counts come from `agents[<agent_slug>].sub_agent_counts`.

Labels come from `SUB_AGENT_LABELS` in `src/types/library.ts`. **A backend-emitted sub-agent slug without a matching label renders as raw snake_case** — the Codex audit finding that prompted FP7's honest-disclosure enforcement at this surface. The fix is to extend `SUB_AGENT_LABELS` whenever a new sub-agent ships.

### CategoryChips

Shows the categories whose count > 0 within the current sub-agent filter. Labels come from `CATEGORY_LABELS`; tones come from `CATEGORY_TONE`. Render order is fixed in `CATEGORY_ORDER`.

Same FP7 concern: a manifest category key not present in `CATEGORY_LABELS` is silently dropped from the chip row. Fix: extend the map whenever a new category ships.

### LibrarySearch

Free-text search across each tool's name, prettyTitle, tool_function, one_liner, related_tools, workflows. Pure client-side filter on the small manifest payload.

### ToolCardGrid

Renders one card per visible tool (after sub-agent, category, search filters). Click opens `ToolDetailDrawer`.

### ToolDetailDrawer

Renders the per-tool detail: name, methodology card, conventions table, related tools, workflows. The "Open in Build" CTA routes through `contextDecoder` (which reads central registries — derived from modules) → BuildShell → matching canvas per the module's tier claims.

## How a module contributes to Library

A module contributes to Library through *one indirect path*:

| Module declaration | Library behaviour |
|---|---|
| `tiers ∋ workflow_incompatible | paused | deferred` | The drawer's "Open in Build" CTA routes to the appropriate honest card (workflow-incompatible / paused / reservation) via contextDecoder. |
| Default (most modules) | The drawer's "Open in Build" CTA routes to the appropriate canvas (typed view, rich-model, generic builder) via contextDecoder. |

A module does NOT contribute *Library card content* — the backend manifest does. The module's role at Library is enabling the "Open in Build" routing path.

## What LibraryPage does NOT do

- **Branch on tool name.** Per FP12; LibraryPage iterates manifest entries generically.
- **Compute analytical values.** Per FP9; Library renders manifest data.
- **Override the manifest.** Per FP4 + P10; the backend manifest is authoritative.
- **Hardcode the workflow count.** Today `workflowCount={2}` is a temporary hardcoded value pending workflows-in-manifest extension. Tracked under Open questions.

## Open questions

1. **Workflows in manifest.** Today the backend manifest covers primitives only; workflow templates are not in it. The Library's workflow tab is currently hardcoded with count 2. Extension: backend ships workflow-template metadata in the manifest; Library reads it the same way.
2. **Operators in manifest.** Future: operators surface in Library as a separate tab once their manifest schema lands.
3. **Manifest hot-reload.** Today the manifest is fetched once on page load. Should it be invalidated on every page navigation? Today: no — backend manifest changes are coupled to deployments; per-session caching is correct.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial Library page-shell doc. Manifest-as-source-of-truth, per-strip semantics, label-map coverage requirement, module contribution path (indirect). |
