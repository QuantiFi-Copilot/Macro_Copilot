# Frontend Infrastructure — Render Shells

> The shared render shells modules slot into: AutoRenderer, RichModelWidget, GenericPrimitiveBuilder, BuilderCanvas, OutputCanvas, typed primitive views (Spread / CrossMarket / Butterfly / Yield / Scanner / Regime / Forward). The per-shell Prop contracts.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** load-bearing. Changes to Prop contracts require an ADR.
**Operationalises principles:** [SI1](README.md#si1--domain-blind-dispatch), [SI3](README.md#si3--one-concept-one-shared-shell), [SI4](README.md#si4--stable-prop-contracts), [FP13](../../00_thesis/03_frontend_thesis.md).
**See also:** [`README.md`](README.md) — infrastructure contract; [`../frontend_module/README.md`](../frontend_module/README.md) — how modules consume shells.

---

## What this doc is

The detailed Prop contracts for every shared render shell. Module surfaces consume these shells; this doc is the reference for what each shell expects and what it renders.

A shared render shell is a finance-blind React component that any module can drop into. Each shell has:
- A canonical name.
- A typed Prop interface.
- A documented dispatch axis (artifact type, schema field, etc.).
- One canonical implementation (SI3).

## AutoRenderer

**Path today.** `src/components/build/model/renderers/AutoRenderer.tsx`. **Target-state Stage N+**: `src/components/shared/render/AutoRenderer.tsx` (moved under `shared/render/` per the SI3 canonical-location convention).

**Purpose.** Output rendering by shape inference. Given a raw `output` dict from `POST /api/v1/tools/{name}/run`, AutoRenderer picks scalar metrics (via `pickScalarMetrics`) into a KPI strip, picks time-series fields (via `pickTimeSeriesFields`) into a grid of mini-charts, and falls through to a raw-JSON card when neither is present.

**Dispatch axis (today).** Field-shape inference over the raw output dict (scalar numbers → KPI; time-series-shaped lists → chart). **Target-state Stage N+:** explicit dispatch on a wire `output_artifact_type` field once the backend ships it as a stable contract.

**Prop interface (today).**
```ts
type Output = Record<string, unknown>;

export function AutoRenderer({ output }: { output: Output }) { /* ... */ }
```

The single `output` prop is the raw `PrimitiveRunResult` payload's output dict. There is no separate `result` / `toolCard` plumbing today — methodology surfacing happens via separate `MethodologyPanel` components rendered alongside.

**Target-state prop interface (Stage N+, once `output_artifact_type` is a stable wire field):**
```ts
type AutoRendererProps = {
  result: PrimitiveRunResult;
  toolCard: ToolCard;
  variant?: 'full' | 'compact';
};
```

The migration from the current `({ output })` shape to the typed `({ result, toolCard })` shape is a Stage N+ refactor; module surfaces that delegate today pass `output` verbatim and adapt when the contract shifts.

**Used by.**
- `OutputCanvas` (which wraps both generic builder + rich-model builder paths).
- `MultiPrimitiveCanvas` (one card per primitive).
- Module surfaces that delegate to it (rare; most modules either use generic_builder OR ship custom build surfaces).

**What it does NOT do.** Re-render typed-detail-endpoint shapes (those are SpreadView / CrossMarketView / etc.). Compute anything (SI2).

## RichModelWidget

**Path.** `src/components/shared/render/RichModelWidget.tsx`

**Purpose.** Per-tool persisted-artifact preview for rich-model primitives (PCA, rolling regression, attribution, half-life, beta-adjusted spread). Renders the persisted Series + an honest "what's not in this snapshot" callout pointing at the live-run path for the missing components (loadings, variance, current factor levels, etc.).

**Dispatch axis.** None — it's a generic shell that takes `toolName` as input and routes through the persistedModelAdapter for that tool.

**Prop interface.**
```ts
type RichModelWidgetProps = NodeRendererProps & {
  toolName: string;
};
```

**Used by.** Per-tool `surfaces/PreviewWidget.tsx` files of rich-model modules (each is a 3-line wrapper):

```tsx
const PcaPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget {...props} toolName="calculate_pca_yield_curve_tool" />
);
```

**What it does NOT do.** Render the live-run rich-model playground (that's `BuilderCanvas`). Branch on which rich-model tool — the per-tool adapter does the per-tool shape translation in pure-data layer.

## GenericPrimitiveBuilder

**Path.** `src/components/build/primitive/GenericPrimitiveBuilder.tsx`

**Purpose.** Schema-driven primitive runner. Given a `toolName`, fetches the `ToolCard`, renders a `ParametersPanel` from the input-fields schema, runs the primitive via `POST /api/v1/tools/{name}/run`, renders the result via `OutputCanvas` (which falls through to `AutoRenderer`).

**Dispatch axis.** `ToolCard.input_fields` (per-field via `paramHintFor`).

**Prop interface.**
```ts
type GenericPrimitiveBuilderProps = {
  toolName: string;
  initialParams?: Record<string, unknown>;
  askHandoff?: boolean;
};
```

**Used by.**
- `BuildShell`'s `ContextCanvasRouter` for `kind: 'generic_builder'` decoded contexts.
- Module `surfaces/BuildSurface.tsx` files that delegate to it (when a module wants to ship the generic surface but claim `custom_build_surface` for tier-vocabulary reasons — rare).

**What it does NOT do.** Render typed-detail shapes (SpreadView etc.). Run workflow-incompatible tools (their `/tools/{name}/run` would 500).

## BuilderCanvas

**Path.** `src/components/build/model/BuilderCanvas.tsx`

**Purpose.** Rich-model playground for PCA / regression / attribution / half-life / beta-adjusted spread. Owns presets, pinned-run comparison, lineage panel, interpretation cards, the full rich-model UX.

**Dispatch axis.** `toolName` resolves to a `ModelMetadata` entry (derived from the module's spec) which drives presets, paramHints, interpretation cards.

**Prop interface.**
```ts
type BuilderCanvasProps = {
  toolName: string | null;
  initialParams: Record<string, string>;
};
```

**Used by.**
- `BuildShell.SlugFreeShell` when `?builder=<toolName>` is in the URL.
- Module `surfaces/BuildSurface.tsx` files for rich-model modules (a 3-line wrapper).

**What it does NOT do.** Render simple snapshot primitives (the generic builder is sufficient).

## OutputCanvas

**Path.** `src/components/build/model/OutputCanvas.tsx`

**Purpose.** The output side of `BuilderCanvas` and `GenericPrimitiveBuilder`. Dispatches on result shape: per-tool registered renderer (PCA loadings, rolling regression betas, attribution decomposition) when one exists, else falls through to `AutoRenderer`.

**Dispatch axis.** Tool-name lookup against `model/renderers/` registry, then artifact-type fallback.

**Prop interface.**
```ts
type OutputCanvasProps = {
  result: PrimitiveRunResult | null;
  toolName: string;
  variant?: 'full' | 'compact';
};
```

**Per-tool renderers.** Live in `src/components/build/model/renderers/`:
- `PcaLoadingsRenderer` — PCA factor loadings + variance bars + factor time series.
- `RollingRegressionRenderer` — betas + R² + condition flags.
- `AttributionRenderer` — per-component contribution bars + residual.

These renderers are shared (they live under `model/renderers/`, not in module folders) because they implement *render concepts* (loadings layout, beta strip) the modules just request. Per FP13, the renderers are domain-blind — they take a `PrimitiveRunResult` and render it; they do not branch on `toolName`.

**What it does NOT do.** Run the primitive (`BuilderCanvas` / `GenericPrimitiveBuilder` do).

## Typed primitive views

**Path.** `src/components/build/primitive/` (today; future move to `src/components/shared/render/typed/`)

**Purpose.** Per-output-shape canvases for primitives backed by typed-detail endpoints `/api/v1/rates/detail/{type}`. Each typed view is responsible for one output shape; the module claims `typedView: '<kind>'` in its spec to route through one of these.

| Typed view | File | Backing endpoint |
|---|---|---|
| Spread | `SpreadPrimitiveView.tsx` | `/api/v1/rates/detail/spread` |
| Cross-market | `CrossMarketPrimitiveView.tsx` | `/api/v1/rates/detail/cross_market` |
| Butterfly | `ButterflyPrimitiveView.tsx` | `/api/v1/rates/detail/butterfly` |
| Yield | `YieldPrimitiveView.tsx` | `/api/v1/rates/detail/yield_snapshot` (per-instrument) |
| Scanner | `ScannerPrimitiveView.tsx` | `/api/v1/rates/scanner` |
| Regime | `RegimePrimitiveView.tsx` | `/api/v1/rates/regimes` |
| Forward | `ForwardPrimitiveView.tsx` | placeholder (no backing endpoint today) |

**Prop interface (shared shape, parameterised per view).**
```ts
type TypedPrimitiveViewProps = {
  toolName: string;
  params: Record<string, string>;
  paramsStructured?: Record<string, unknown>;
  askHandoff?: boolean;
};
```

**Used by.** `VirtualPrimitiveCanvas` (single-tool) and `MultiPrimitiveCanvas` (multi-tool) dispatch on the decoder's `kind` field to one of these. Module surfaces MAY also delegate to them.

**What they do NOT do.** Run via `POST /tools/{name}/run` (they call the typed-detail endpoints, which are pre-aggregated). Render anything other than their fixed output shape.

## DagStrip

**Path.** `src/components/build/dag/DagStrip.tsx` (today; future move to `src/components/shared/dag/`)

**Purpose.** Workflow DAG visualisation. Renders nodes derived from a `Workspace`'s `metadata.lineage` field; nodes show display name + status + (on click) the node's artifact.

**Dispatch axis.** `DagNodeKind` ∈ `{primitive, operator, terminal_artifact}` + `DagWireType` (the artifact type the node emits).

**Prop interface.**
```ts
type DagStripProps = {
  lineage: WorkflowLineage;
  highlightedNodeId?: string;
  onNodeClick?: (nodeId: string) => void;
};
```

**Used by.**
- Ask `messages/DagStrip.tsx` (a thin wrapper).
- Build `BuildCompleted` view.

**What it does NOT do.** Branch on tool name (FP13). Compute anything (SI2). Re-render per-tool layouts — the strip is uniform; per-tool DAG decorations are not in scope.

## Bento grid (Monitor surface)

**Paths.** `src/components/monitor/WidgetGrid.tsx`, `src/components/monitor/WidgetCard.tsx`, `src/components/monitor/WidgetRenderer.tsx`.

**Purpose.** Monitor surface's layout engine. Reads a `LayoutState` (persisted in localStorage), renders one `WidgetCard` per widget instance, dispatches to the appropriate `WidgetRenderer` based on widget type.

**Dispatch axis.** Widget id (a string key into the `WIDGET_TYPES` catalogue).

**WIDGET_TYPES.** Derived from modules' `monitor_surface` tier claims. See [`../frontend_registries/README.md`](../frontend_registries/README.md).

**Prop interfaces.**
```ts
type WidgetGridProps = {
  layout: LayoutState;
  onLayoutChange: (next: LayoutState) => void;
};

type WidgetCardProps = {
  instance: WidgetInstance;
  meta: WidgetTypeMeta;
};

type WidgetRendererProps = {
  type: string;
  params: Record<string, unknown>;
};
```

**What it does NOT do.** Compute widget data (each widget renderer calls its own hook). Branch on tool name (the dispatch is by widget id; the widget renderer is the module's `surfaces/MonitorWidget.tsx`).

## Adding a new shared shell

A new shell is justified when no existing shell can be parameterised to handle the new case. The procedure:

1. Confirm the new shell's *render concept* is genuinely distinct from every existing shell. A new shell that's a near-duplicate of an existing one is a SI3 violation.
2. Open an ADR documenting:
   - The render concept it owns.
   - Why no existing shell can be extended.
   - The Prop interface.
   - Which modules will consume it.
3. Land the shell with full Prop typing and at least one consumer in the same PR.
4. Add an entry to this doc's table.

A new typed primitive view (e.g. when a new typed-detail endpoint ships on the backend) follows the same procedure under the *Typed primitive views* table.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial render-shell catalogue. AutoRenderer, RichModelWidget, GenericPrimitiveBuilder, BuilderCanvas, OutputCanvas, 7 typed views, DagStrip, bento grid. Prop interfaces. |
