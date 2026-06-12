// ============================================================================
// src/components/build/lib/persistedExpand.ts — persisted-node expand helper.
// ----------------------------------------------------------------------------
// Consolidation target #2: the persisted open-DAG slug page gains the
// SAME expand→buildExtended affordance the live multi-tool DAG has.
// This helper builds the ``DecodedPrimitive`` the shared
// ``ExpandedViewModal`` consumes from a PERSISTED ``NodeSummary``.
//
// P4 / FP9 honesty contract: the expanded view is a LIVE RE-QUERY of
// the typed-detail endpoint (today's numbers, full KPIs) — the saved
// node card stays frozen, read-only by hash.  The slug page's
// ``ExpandedViewProvider`` carries the disclosure banner; this helper
// only shapes the data.  This is the settled G-3.3(c) decision:
// full desk KPIs come from the expand-to-live affordance rather than
// persisting ``current_metrics`` next to the artifact (no new
// persistence surface; zero replay-determinism risk).
//
// Pure functions — locked by build-folder tests.
// ============================================================================

import type { NodeSummary } from '@/services/workspaceApi';
import type { DecodedPrimitive } from '@/components/build/primitive/contextDecoder';
import { getPrimitiveModule } from '@/modules';

/** True when a persisted node can open an extended view: primitive
 *  nodes whose owning module ships ``surfaces.buildExtended`` (the
 *  dual-view contract).  Operator nodes have no owning module — they
 *  render through artifact-type widgets only. */
export function canExpandPersistedNode(node: NodeSummary): boolean {
  if (node.kind !== 'primitive') return false;
  const toolName = persistedNodeToolName(node);
  if (!toolName) return false;
  const mod = getPrimitiveModule(toolName);
  return Boolean(mod?.surfaces?.buildExtended);
}

/** The backend tool name of a persisted primitive node.  PR A's
 *  persistence writes it as ``params.tool_name``; the node ``name``
 *  is the human-readable fallback used by older rows. */
export function persistedNodeToolName(node: NodeSummary): string | null {
  const fromParams = node.params?.tool_name;
  if (typeof fromParams === 'string' && fromParams.length > 0) {
    return fromParams;
  }
  if (typeof node.name === 'string' && node.name.endsWith('_tool')) {
    return node.name;
  }
  return null;
}

/** Build the ``DecodedPrimitive`` the shared expand modal mounts.
 *
 *  Persisted ``node.params`` are the PR-A replay record — on the wire
 *  the bound Input lives ONE LEVEL DOWN under ``params.params``
 *  (``{tool_name, params: {…bound Input…}, output_field?}``), so the
 *  inner envelope is unwrapped FIRST: without it the expand modal
 *  would silently fall back to module defaults while the banner
 *  promises "this node's saved parameters" (the lookback_days=1825 →
 *  365d-default bug caught on ws-59be50ca).  The flat ``params``
 *  projection keeps scalars only (stringified — typed-detail endpoints
 *  accept flat string params); nested objects/arrays ride in
 *  ``paramsStructured`` verbatim so surfaces that seed from structured
 *  config (FM5 paramHints consumers) stay hydrated.  ``tool_name``
 *  itself is identity, not an Input field — stripped from both
 *  projections. */
export function decodedForPersistedNode(
  node: NodeSummary,
): DecodedPrimitive | null {
  const toolName = persistedNodeToolName(node);
  if (!toolName) return null;

  // Unwrap the PR-A envelope: the inner ``params`` dict is the bound
  // Input; envelope siblings (output_field, …) stay alongside it.
  const raw = node.params ?? {};
  const inner = raw.params;
  const source: Record<string, unknown> =
    inner != null && typeof inner === 'object' && !Array.isArray(inner)
      ? { ...(inner as Record<string, unknown>), ...withoutEnvelope(raw) }
      : (raw as Record<string, unknown>);

  const flat: Record<string, string> = {};
  const structured: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (key === 'tool_name') continue;
    structured[key] = value;
    if (
      typeof value === 'string' ||
      typeof value === 'number' ||
      typeof value === 'boolean'
    ) {
      flat[key] = String(value);
    }
  }

  return {
    kind: 'generic_builder',
    toolName,
    params: flat,
    paramsStructured: structured,
  } as DecodedPrimitive;
}

/** Envelope siblings minus the unwrapped inner ``params`` dict. */
function withoutEnvelope(
  raw: Record<string, unknown>,
): Record<string, unknown> {
  const rest: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (key === 'params') continue;
    rest[key] = value;
  }
  return rest;
}
