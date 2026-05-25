# Frontend Infrastructure — DAG Renderer

> Workflow DAG visualisation: lineage parsing, node derivation, rendering. The DAG renderer is finance-blind; it visualises any workflow without per-tool branching.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes to the DAG node-kind closed family require an ADR.
**Operationalises principles:** [SI1](README.md#si1--domain-blind-dispatch), [SI3](README.md#si3--one-concept-one-shared-shell), [FP13](../../00_thesis/03_frontend_thesis.md).
**See also:** [`README.md`](README.md), [`render_shells.md`](render_shells.md#dagstrip), backend [`../workflow_template/README.md`](../workflow_template/README.md) (the workflow lineage contract this renderer consumes).

---

## Component map

| Component | Path | Purpose |
|---|---|---|
| `parseWorkflowLineage` | `src/components/build/dag/parseWorkflowLineage.ts` | Pure parser: `WorkflowLineage` (from backend) → flat list of `DagNode` records. |
| `deriveDagNodes` | `src/components/build/dag/deriveDagNodes.ts` | Composes parser output with display metadata (name, kind, status). |
| `DagStrip` | `src/components/build/dag/DagStrip.tsx` | The visual strip: one chip per node, click → preview / open artifact. |
| `DagNode` type | `src/components/build/dag/types.ts` | The typed node record. |

## The closed family — DagNodeKind

```ts
type DagNodeKind =
  | 'primitive'
  | 'operator'
  | 'terminal_artifact'
;
```

Three kinds. Closed family per P8. Adding a new kind requires an ADR.

`primitive` — a node produced by calling a backend primitive tool (`tool_name` in `_PRIMITIVE_SPECS`).
`operator` — a node produced by an operator (in `shared/operators/`).
`terminal_artifact` — the workflow's terminal output node.

Per FP13, the renderer dispatches on `kind` only — never on `tool_name`. The chip's label is `node.displayName`, sourced from the backend lineage (which knows the primitive's display name) or the operator's catalogue entry.

## The closed family — DagWireType

```ts
type DagWireType =
  | 'Series'
  | 'Panel'
  | 'SeriesSet'
  | 'EventSet'
  | 'WindowedPanel'
  | 'TradeSet'
  | 'NamedArtifact'
;
```

Mirrors backend's closed `ArtifactType` family. The renderer uses it to colour-code or icon-mark per-type; it does NOT branch on which specific artifact (all `Series` look the same regardless of which primitive produced it).

## How lineage flows

```
Backend workflow execution
         ↓
Workflow's metadata.lineage     (frozen artifact-store provenance)
         ↓
WorkspaceDetail (GET /api/v1/workspace/{slug})
         ↓
parseWorkflowLineage(detail.metadata.lineage)
         ↓
deriveDagNodes(parsed, displayMetadata)
         ↓
DagStrip renders nodes
```

The parser is pure (no IO, no side effects, deterministic for a given input). The deriver decorates with display metadata that comes from either the lineage itself (the primitive's display name is on each step) or, for operators, from a static catalogue.

## What the DAG renderer is NOT

- **Not a workflow editor.** Editing workflow templates is a backend concern (template DAGs are frozen on the backend); the renderer is read-only.
- **Not a per-tool layout.** Per-tool DAG-node decorations would violate FP13. If a primitive needs special display, it goes on the *node's artifact preview* (which lives in the module's `surfaces/PreviewWidget.tsx`), not in the DAG node itself.
- **Not finance-aware.** The renderer never reads tool-name-specific logic. The same renderer would visualise an FX workflow tomorrow.

## Module interaction

Modules do NOT contribute DAG-rendering code. A module's primitive appears as a `primitive` node in the strip; its display name comes from the module's `displayName` field (mirrored to the backend's display name); its artifact previews (when clicked) come from the module's `surfaces/PreviewWidget.tsx` if the module claims `custom_preview_widget`, else from the generic per-type widget.

This is the canonical example of SI1 in action: the DAG renderer dispatches on `kind` + artifact type; the module specialises on the artifact preview side. Each layer does what it's best at.

## Open questions

1. **Per-operator display.** Today operators render as a generic chip with the operator name. Should operator-specific iconography (e.g. an arrows icon for `align_series`) be added? Today: no — the renderer stays uniform.
2. **DAG zoom / pan.** Today the strip is a horizontal scroll. Should a richer node-graph view exist? Today: deferred; the strip is sufficient for current workflow sizes.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial DAG-renderer doc. Closed families for `DagNodeKind` (3 members) and `DagWireType` (7 members). Component map, lineage flow, module-interaction boundary. |
