// ============================================================================
// nodeRendererRegistry.ts — pick a widget renderer for a workspace node.
// ----------------------------------------------------------------------------
// THE central abstraction of the Build surface.  Every node in a
// persisted workspace's DAG is rendered as a polished card whose
// inner body is produced by a renderer chosen here.  Adding a new
// artifact type (or a per-tool specialised view) is a single new
// entry — no switch statements in the consuming components, no
// "if (tool === '...')" chains.
//
// Selection key
// -------------
// Look-up is a two-step priority chain:
//
//   1. ``(artifact_type, tool_name)`` — a per-tool specialised
//                                       renderer.  Reserved for
//                                       primitives whose generic
//                                       artifact-type rendering
//                                       would hide their main signal
//                                       (e.g. ``calculate_pca_yield_curve_tool``
//                                       deserves a loadings chart
//                                       even though it emits
//                                       ``Series``).
//
//   2. ``artifact_type`` only         — the default per-type
//                                       rendering.  Most nodes go
//                                       through here.
//
//   3. ``FallbackWidget``             — when no entry matches (the
//                                       substrate added a new
//                                       artifact type the UI hasn't
//                                       caught up with yet).  Shows
//                                       the artifact's identity bits
//                                       so the cards still render
//                                       meaningfully.
//
// Renderer contract
// -----------------
// A renderer is a React component that consumes a `NodeRenderProps`
// object — the workspace's parent ``NodeWidgetCard`` shell handles
// the WidgetCard chrome, gradient rail, provenance footer, and edit-
// mode actions.  Renderers focus exclusively on the body of the card.
//
// Future-proofing
// ---------------
// PR A ships 7 generic renderers (Series, SeriesSet, EventSet, Panel,
// WindowedPanel, ScalarMetric, TradeSet).  PR B will register per-tool
// specialised renderers for PCA, RollingRegression, Attribution
// (those renderers already live under ``model-workspace/renderers/``
// and can be lifted into this registry verbatim).  The point of this
// file is that lifting them is a one-entry change to ``REGISTRY``.
// ============================================================================

import type { ComponentType } from 'react';
import type {
  ArtifactSummary,
  NodeSummary,
  WorkspaceDetail,
} from '@/services/workspaceApi';
import type { StageCategory } from './buildTypes';

// ----------------------------------------------------------------------------
// Renderer contract
// ----------------------------------------------------------------------------

/**
 * Props every node-body renderer receives.  Renderers see the same
 * normalised shape regardless of where they sit in the registry, so
 * a registry swap (e.g. promoting a generic renderer to a per-tool
 * specialisation) doesn't require call-site changes.
 *
 * Renderers are pure presentational — they MUST NOT mutate state,
 * fire side-effects, or call hooks beyond ``useMemo`` / ``useState``
 * for local rendering optimizations.  Anything beyond that belongs
 * in the parent ``NodeWidgetCard`` or the page-level hooks.
 */
export interface NodeRenderProps {
  /** The DAG node being rendered.  Includes ``params`` + the
   *  ``artifact_hash`` of the executed output. */
  node: NodeSummary;
  /** Artifact summary already fetched by the parent — guaranteed
   *  non-null when this renderer is invoked. */
  artifact: ArtifactSummary;
  /** Visual category derived by ``stageCategoryForNode`` — used by
   *  renderers that want to tone-shift their chart colors to match
   *  the parent card's rail. */
  category: StageCategory;
  /** Parent workspace context.  Carried for renderers that need to
   *  cross-reference sibling nodes (e.g. a SeriesSet renderer
   *  showing a per-member sparkline strip). */
  workspace: WorkspaceDetail;
  /** Display sizing hint inherited from the parent grid layout.
   *  Renderers MAY use this to switch between dense / sparse
   *  variants (e.g. a Panel might show a 5-row preview at "medium"
   *  but a single-row summary at "small"). */
  size: 'small' | 'medium' | 'wide' | 'tall';
}

/** A renderer is just a React component that takes ``NodeRenderProps``.
 *  Kept untyped (no ``React.FC``) so renderer files can colocate
 *  their helper components without conflicting with the registry
 *  signature. */
export type NodeRenderer = ComponentType<NodeRenderProps>;

// ----------------------------------------------------------------------------
// Registry shape
// ----------------------------------------------------------------------------

/** Per-tool specialisation key.  An entry here OVERRIDES the
 *  generic per-artifact-type entry for a single tool, so the
 *  registry can elevate "the PCA factor renderer" above "the
 *  generic Series renderer" without affecting any other Series-
 *  emitting primitive. */
export interface PerToolRendererKey {
  artifactType: string;
  toolName: string;
}

interface RendererRegistry {
  /** Generic per-artifact-type entries.  Required surface: one
   *  entry per artifact type the substrate emits (``Series``,
   *  ``SeriesSet``, ``EventSet``, ``Panel``, ``WindowedPanel``,
   *  ``ScalarMetric``, ``TradeSet``). */
  byArtifactType: Map<string, NodeRenderer>;
  /** Per-tool overrides.  Optional surface. */
  byTool: Map<string, NodeRenderer>;
  /** Renderer used when no entry matches. */
  fallback: NodeRenderer;
}

/** Sentinel "no-op" fallback installed at module init.  Replaced by
 *  ``registerFallbackRenderer`` when ``FallbackWidget`` self-
 *  registers.  We keep a stable reference to it (rather than
 *  creating a new arrow each module load) so the registry-snapshot
 *  helper can tell whether the real fallback has been installed. */
const DEFAULT_FALLBACK_SENTINEL: NodeRenderer = () => null;

const REGISTRY: RendererRegistry = {
  byArtifactType: new Map(),
  byTool: new Map(),
  // Placeholder — replaced by ``registerFallbackRenderer`` during
  // module-init of the widgets package.  Throwing here would block
  // the entire app from booting if a renderer file is missing;
  // emitting a tiny "no renderer" stub is safer for forward-compat.
  fallback: DEFAULT_FALLBACK_SENTINEL,
};

// ----------------------------------------------------------------------------
// Public registration API
// ----------------------------------------------------------------------------

/** Register the default renderer for an artifact type.
 *
 *  Registration order matters only for diagnostics — the same key
 *  registered twice keeps the LAST registration.  Widget files
 *  register themselves on import (see widgets/index.ts), so the
 *  registration order is the import order. */
export function registerArtifactRenderer(
  artifactType: string,
  renderer: NodeRenderer,
): void {
  REGISTRY.byArtifactType.set(artifactType, renderer);
}

/** Register a per-tool specialised renderer that overrides the
 *  generic per-artifact-type renderer when both apply.
 *
 *  Encoded as ``"<artifactType>:<toolName>"`` so two specialisations
 *  for the same tool but different artifact types (extremely rare —
 *  one primitive, multiple output types) stay distinct. */
export function registerToolRenderer(
  key: PerToolRendererKey,
  renderer: NodeRenderer,
): void {
  REGISTRY.byTool.set(toolKey(key), renderer);
}

/** Replace the fallback renderer.  Called once during widget-module
 *  init; calling it again later overrides. */
export function registerFallbackRenderer(renderer: NodeRenderer): void {
  REGISTRY.fallback = renderer;
}

// ----------------------------------------------------------------------------
// Lookup
// ----------------------------------------------------------------------------

/** Look up the appropriate renderer for a node, given its artifact
 *  summary.  Implements the per-tool → per-type → fallback priority
 *  chain documented at the top of this file.
 *
 *  Never throws: returns the fallback renderer when nothing else
 *  matches.  This makes the consuming components branchless. */
export function resolveNodeRenderer(
  node: NodeSummary,
  artifact: ArtifactSummary,
): NodeRenderer {
  // Per-tool override takes priority.
  const toolName = extractToolName(node);
  if (toolName) {
    const perTool = REGISTRY.byTool.get(
      toolKey({ artifactType: artifact.artifact_type, toolName }),
    );
    if (perTool) return perTool;
  }
  // Per-artifact-type default.
  const perType = REGISTRY.byArtifactType.get(artifact.artifact_type);
  if (perType) return perType;
  return REGISTRY.fallback;
}

/** Look up the artifact-type renderer registered (or fallback) WITHOUT
 *  having a NodeSummary to hand.  Used by the empty-state preview tiles
 *  and any future surface that wants to render an isolated artifact
 *  divorced from its workspace context. */
export function resolveArtifactTypeRenderer(
  artifactType: string,
): NodeRenderer {
  return REGISTRY.byArtifactType.get(artifactType) ?? REGISTRY.fallback;
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function toolKey(key: PerToolRendererKey): string {
  return `${key.artifactType}:${key.toolName}`;
}

/** Pull the tool name out of a NodeSummary's params blob.  Honors
 *  the substrate's persisted shape from
 *  ``_workflow_node_params_for_storage``: PrimitiveNode params are
 *  ``{tool_name, output_field, params}``; OperatorNode params are
 *  ``{operator_name, params}``.  For per-tool overrides we look at
 *  ``tool_name`` only — operator overrides aren't supported in
 *  PR A (operator outputs all flow through the per-type generic
 *  renderers). */
function extractToolName(node: NodeSummary): string | null {
  const params = node.params ?? {};
  if (typeof params !== 'object') return null;
  const tn = (params as Record<string, unknown>)['tool_name'];
  return typeof tn === 'string' && tn.length > 0 ? tn : null;
}

/** Snapshot for diagnostics + tests.  Returns the keys currently
 *  registered so a test can assert "all 7 artifact-type renderers
 *  are wired" without poking into module internals. */
export function registrySnapshot(): {
  artifactTypes: string[];
  tools: string[];
  hasFallback: boolean;
} {
  return {
    artifactTypes: [...REGISTRY.byArtifactType.keys()].sort(),
    tools: [...REGISTRY.byTool.keys()].sort(),
    // PR8 polish: compare against the module-scoped sentinel rather
    // than a freshly-created arrow.  The pre-PR8 code created a new
    // ``() => null`` per call, which by reference identity ALWAYS
    // produced ``true`` — esbuild correctly flagged this as a tautology.
    hasFallback: REGISTRY.fallback !== DEFAULT_FALLBACK_SENTINEL,
  };
}
