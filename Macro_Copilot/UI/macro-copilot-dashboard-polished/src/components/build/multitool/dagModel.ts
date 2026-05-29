// ============================================================================
// dagModel.ts — Pure model for the generic multi-tool DAG container.
// ----------------------------------------------------------------------------
// Stage D.  This is the FINANCE-BLIND, TOOL-AGNOSTIC core of the multi-tool
// page: it turns a decoded ``?context=`` payload into a node/edge model the
// DAG canvas renders.  It knows NOTHING about real yields, breakevens, or any
// specific tool — it works off the generic decode (tool name + params + kind
// + source status) so ANY combination of tools renders consistently.
//
// Per docs_revamped/03_standards/rendering_density.md §10 the DAG
// infrastructure owns: node layout, edge rendering, the query-root summary,
// per-call ordinals (CallMeta), and layout density (size by node count).
// This module computes all of those as a pure value; the React components
// (MultiToolDagGraph / DagNodeBody / MultiToolDagCanvas) are a projection of
// it (MultiToolDagGraph layers it via dagLayout.ts).
//
// Boundary cases (rendering_density §3.4) handled here:
//   - 0 nodes  → empty model; the canvas shows the unsupported affordance.
//   - 1 node   → ``showStrip=false`` (degenerate; BuildShell dispatch routes
//                single-tool to the extended view, so this is defensive).
//   - N copies of the same (tool, params) → CallMeta ordinals.
//   - edges present → ``multistep`` archetype; absent → ``parallel``.
// ============================================================================

import {
  decodeWorkspaceContextDetailed,
  type DecodedContextEntry,
  type DecodedPrimitive,
} from '../primitive/contextDecoder';

/** Per-call ordinal when ≥2 nodes share the same (toolName, params)
 *  signature — surfaced as a "call N of M" chip on the node so two
 *  identical cards don't look like a rendering bug. */
export interface CallMeta {
  n: number;
  m: number;
}

export type DagNodeStatus = 'ok' | 'error';

/** One node in the multi-tool DAG.  ``id`` is the source tool's index in
 *  the ORIGINAL context ``tools`` array — stable across renders and used
 *  for React keys, edge resolution, and scroll-to-card anchoring. */
export interface DagNode {
  id: number;
  decoded: DecodedPrimitive;
  toolName: string;
  params: Record<string, string>;
  /** Defined only when ≥2 nodes share this node's (toolName, params). */
  callMeta?: CallMeta;
  /** Mirrored from the source ``WorkspaceContextTool.status``; 'error'
   *  surfaces an error tile in place of the compact card (R2 — error
   *  inheritance). */
  status: DagNodeStatus;
  /** Source tool error message when ``status === 'error'``. */
  errorMessage?: string;
  /** Originating domain chip ('rates' / 'ois' / …) when the trace
   *  recorded it. */
  domain?: string;
}

/** A resolved dependency edge — both endpoints are present in ``nodes``
 *  (edges referencing dropped entries are filtered out). */
export interface DagEdge {
  from: number;
  to: number;
  label: string;
}

export type DagArchetype = 'parallel' | 'multistep';

export interface DagModel {
  nodes: DagNode[];
  edges: DagEdge[];
  /** ``multistep`` when dependency edges exist; ``parallel`` otherwise. */
  archetype: DagArchetype;
  /** Card density per rendering_density.md §10: ``medium`` for ≤3 nodes,
   *  ``small`` for ≥4.  Passed to each node's ``buildCompact`` ``size``. */
  size: 'small' | 'medium';
  /** Originating user prompt, when the supervisor recorded it. */
  prompt?: string;
  /** Human-readable DAG summary, e.g. "3 tool calls · 1 domain · parallel". */
  summary: string;
  /** Distinct domains represented across the nodes (for the summary). */
  domains: string[];
  /** Whether the DAG strip (node/edge map) should render — only for N ≥ 2. */
  showStrip: boolean;
}

const DEFAULT_EDGE_LABEL = 'feeds';

/** Stable signature for CallMeta grouping — the sorted params dict so two
 *  cards sharing a tool name but different params aren't conflated. */
function signatureFor(entry: DecodedContextEntry): string {
  const params = entry.decoded.params;
  const paramKey = Object.entries(params)
    .map(([k, v]) => `${k}=${v}`)
    .sort()
    .join('&');
  return `${entry.decoded.toolName}::${paramKey}`;
}

/** Build the ``CallMeta`` map keyed by ORIGINAL sourceIndex.  Only
 *  signatures appearing ≥2 times get entries; unique nodes return
 *  undefined (no chip). */
function computeCallMeta(
  entries: DecodedContextEntry[],
): Map<number, CallMeta> {
  const bySig = new Map<string, number[]>();
  for (const e of entries) {
    const sig = signatureFor(e);
    const arr = bySig.get(sig);
    if (arr) arr.push(e.sourceIndex);
    else bySig.set(sig, [e.sourceIndex]);
  }
  const out = new Map<number, CallMeta>();
  for (const indices of bySig.values()) {
    if (indices.length < 2) continue;
    indices.forEach((sourceIndex, i) => {
      out.set(sourceIndex, { n: i + 1, m: indices.length });
    });
  }
  return out;
}

function buildSummary(
  nodeCount: number,
  distinctToolCount: number,
  domainCount: number,
  archetype: DagArchetype,
): string {
  const parts: string[] = [];
  parts.push(`${nodeCount} tool call${nodeCount === 1 ? '' : 's'}`);
  if (distinctToolCount > 0 && distinctToolCount < nodeCount) {
    parts.push(`${distinctToolCount} distinct tool${distinctToolCount === 1 ? '' : 's'}`);
  }
  if (domainCount > 0) {
    parts.push(`${domainCount} domain${domainCount === 1 ? '' : 's'}`);
  }
  parts.push(archetype === 'multistep' ? 'multi-step' : 'parallel');
  return parts.join(' · ');
}

/** Build the pure DAG model from a raw ``?context=`` param.  Tool-agnostic:
 *  works off the generic decode, so any combination of tools renders. */
export function buildDagModel(contextParam: string): DagModel {
  const { prompt, edges: rawEdges, entries } =
    decodeWorkspaceContextDetailed(contextParam);

  const callMetaByIndex = computeCallMeta(entries);

  const nodes: DagNode[] = entries.map((e) => {
    const status: DagNodeStatus =
      e.source.status === 'error' ? 'error' : 'ok';
    return {
      id: e.sourceIndex,
      decoded: e.decoded,
      toolName: e.decoded.toolName,
      params: e.decoded.params,
      callMeta: callMetaByIndex.get(e.sourceIndex),
      status,
      errorMessage: status === 'error' ? e.source.error : undefined,
      domain:
        typeof e.source.domain === 'string' && e.source.domain
          ? e.source.domain
          : undefined,
    };
  });

  // Keep only edges whose BOTH endpoints resolve to a rendered node.
  const nodeIds = new Set(nodes.map((n) => n.id));
  const edges: DagEdge[] = rawEdges
    .filter(
      (e) =>
        typeof e.from === 'number' &&
        typeof e.to === 'number' &&
        e.from !== e.to &&
        nodeIds.has(e.from) &&
        nodeIds.has(e.to),
    )
    .map((e) => ({
      from: e.from,
      to: e.to,
      label: e.label?.trim() ? e.label.trim() : DEFAULT_EDGE_LABEL,
    }));

  const archetype: DagArchetype = edges.length > 0 ? 'multistep' : 'parallel';
  const size: 'small' | 'medium' = nodes.length >= 4 ? 'small' : 'medium';

  const distinctTools = new Set(nodes.map((n) => n.toolName)).size;
  const domains = Array.from(
    new Set(
      nodes
        .map((n) => n.domain)
        .filter((d): d is string => typeof d === 'string' && d.length > 0),
    ),
  ).sort();

  return {
    nodes,
    edges,
    archetype,
    size,
    prompt,
    summary: buildSummary(nodes.length, distinctTools, domains.length, archetype),
    domains,
    showStrip: nodes.length >= 2,
  };
}
