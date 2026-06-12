// ============================================================================
// contextDecoder — translate Ask's ``?context=`` URL param into a Build view.
// ----------------------------------------------------------------------------
// PR1 (Build Routing Coverage): the decoder is the single source of
// truth for "what Build surface should we land on for tool X?".  It
// returns a deterministic ``DecodedPrimitive`` for every recognised
// tool — generic schema-driven builder, workflow-incompatible card,
// or unsupported-known card — and ``null`` only for malformed
// payloads / truly-unknown tools.
//
// History
// -------
// Phase R1.2 reintroduced the bridge that the deleted ``WorkspacePage``
// used to provide.  R6.3 added ``decodePrimitiveList`` for multi-tool
// comparison grids.  PR1 added the ``kind: 'unsupported_known'``
// variant + the ``KNOWN_BACKEND_TOOLS`` gate so the orange
// decode-error card only fires for genuinely-unparseable payloads.
// PR2 split that variant: tools with a backend run endpoint route to
// ``kind: 'generic_builder'``, while manifest-only tools still
// surface ``kind: 'unsupported_known'``.  Consolidation G-3.5 deleted
// the legacy typed-view and rich-model decode branches: per-tool
// bespoke surfaces are now selected EXCLUSIVELY by module-first
// dispatch (``getPrimitiveModule(tool).surfaces.buildExtended`` /
// ``buildCompact``) downstream of the decode — the decode kind only
// answers "is there a run path?".
//
// Decoder flow per tool entry
// ---------------------------
//   1. Normalise the tool name via ``normalizeToolName``.
//   2. If the tool has a backend run endpoint
//      (``isRunnablePrimitive``) → ``kind: 'generic_builder'``
//      (schema-driven editable surface that calls
//      ``POST /tools/{name}/run``; module-first dispatch may mount a
//      bespoke dual-view surface instead).
//   3. Else if the tool is workflow-incompatible → ``kind:
//      'workflow_incompatible'`` (honest card; the tool ships on the
//      backend but its output shape isn't bridge-compatible).
//   4. Else if the tool is in ``KNOWN_BACKEND_TOOLS`` → ``kind:
//      'unsupported_known'`` (known but not runnable; honest paused
//      card).
//   5. Else → drop the entry (truly-unknown name).
//
// When context carries mixed tools, the single-best lookup prefers a
// runnable surface over an honest card: generic-builder beats
// workflow-incompatible beats unsupported-known.  Among equal-priority
// entries the LAST in original order wins (score >= bestScore).
// ============================================================================

import type {
  WorkspaceContext,
  WorkspaceContextEdge,
  WorkspaceContextTool,
} from '@/types/copilot';
import {
  isKnownBackendTool,
  isRunnablePrimitive,
  isWorkflowIncompatibleTool,
  normalizeToolName,
} from '@/lib/toolNames';

/** Result of decoding a ``?context=`` URL param.  Three discriminated
 *  variants:
 *    - ``generic_builder`` (PR2) — caller renders the schema-driven
 *      ``GenericPrimitiveBuilder`` (left rail: form generated from the
 *      tool's ``ToolCard.input_fields``; centre: run output via the
 *      auto-renderer) UNLESS the owning module ships a dual-view Build
 *      surface, in which case module-first dispatch mounts it.
 *    - ``workflow_incompatible`` (Stage 1) — caller renders the
 *      ``UnsupportedKnownToolCanvas`` card with the per-tool
 *      workflow-incompatible reason.  The tool ships on the backend
 *      (callable via MCP) but its output shape isn't a Series or
 *      Panel artifact, so the generic builder's
 *      ``POST /tools/{name}/run`` route returns the FastAPI
 *      ``{"ok": false, "error": "..."}`` envelope (per
 *      ``api/routes/workflows/execute.py``).  Distinct from
 *      ``unsupported_known`` (paused / unbuilt) — the surface is the
 *      same but the per-tool reason text explains the output-shape
 *      gap rather than a missing implementation.
 *    - ``unsupported_known`` (PR1) — caller renders the
 *      ``UnsupportedKnownToolCanvas`` card so the user sees an honest
 *      "tool is real but Build has no run path yet" affordance instead
 *      of the orange decode-error card.  Reserved for known tools that
 *      AREN'T runnable through ``/tools/{name}/run`` (manifest-only,
 *      paused).
 */
export type DecodedPrimitive =
  | {
      kind: 'generic_builder';
      /** Backend-canonical tool name.  The builder fetches its
       *  ``ToolCard`` via ``useTool`` to render schema-driven controls
       *  and posts to ``/tools/{toolName}/run``. */
      toolName: string;
      /** Original params dict — pre-fills the builder's form so an Ask
       *  hand-off ("compare X with Y") lands with the user's intended
       *  args already populated.  When empty, the builder seeds from
       *  the schema defaults. */
      params: Record<string, string>;
      /** PR-B-β — full structured params for the generic builder's
       *  schema-driven form.  Lets fields whose schema type is
       *  ``object`` / ``array`` (e.g. ``regressor_specs``) seed
       *  correctly when Ask supplied them.  ``params`` (above) is the
       *  flat string-only projection; this preserves nested entries
       *  verbatim. */
      paramsStructured: Record<string, unknown>;
    }
  | {
      kind: 'workflow_incompatible';
      /** Backend-canonical tool name.  The card surfaces this and the
       *  per-tool ``UnsupportedKnownReason`` from ``toolNames.ts``
       *  (mirrored from the backend's ``WORKFLOW_INCOMPATIBLE_TOOLS``
       *  rationale). */
      toolName: string;
      /** Original params dict — preserved so a follow-up "Try in Ask"
       *  affordance can hand the call back to the chat with the user's
       *  intended args intact. */
      params: Record<string, string>;
      /** PR-B-β — full structured params, carried for symmetry with
       *  the other variants. */
      paramsStructured: Record<string, unknown>;
    }
  | {
      kind: 'unsupported_known';
      /** Backend-canonical tool name.  The card surfaces this and the
       *  per-tool ``UnsupportedKnownReason`` from ``toolNames.ts``. */
      toolName: string;
      /** Original params dict — preserved so a follow-up "Try in Ask"
       *  affordance can hand the call back to the chat with the user's
       *  intended args intact. */
      params: Record<string, string>;
      /** PR-B-β — full structured params, carried for symmetry with
       *  the other variants.  Unused on the unsupported card today
       *  but useful when the "Try in Ask" affordance ships. */
      paramsStructured: Record<string, unknown>;
    };

/** PR2 — generic-builder routing sits at the top of the priority
 *  ladder: a runnable surface beats every honest unsupported card.
 *  When a single context carries one runnable tool + one paused tool,
 *  the runnable surface wins; when ALL tools are unsupported, we
 *  surface the card. */
const GENERIC_BUILDER_PRIORITY = 0.5;

/** Stage 1 — workflow-incompatible routing sits between the generic
 *  builder (a runnable surface) and the unsupported-known card (a
 *  paused / unbuilt surface).  A workflow-incompatible tool is REAL
 *  on the backend — it ships and can be called via MCP — so when
 *  forced to pick between rendering its honest card vs the paused
 *  card, we prefer it. */
const WORKFLOW_INCOMPATIBLE_PRIORITY = 0.25;

/** Unsupported-known routing sits BELOW the generic builder and the
 *  workflow-incompatible card, but ABOVE "drop entirely".  When ALL
 *  tools are unsupported-known, we surface the paused card. */
const UNSUPPORTED_KNOWN_PRIORITY = 0;

/** Decode one tool entry into a ``DecodedPrimitive`` variant, or null
 *  when the tool is truly unknown (not in any registry).  Pulled out
 *  so both ``decodePrimitiveContext`` (single-best) and
 *  ``decodePrimitiveList`` (multi-card) share the per-entry logic. */
function decodeOne(
  rawToolName: string,
  rawParams: Record<string, unknown>,
): DecodedPrimitive | null {
  const canonical = normalizeToolName(rawToolName);
  const params = stringifyParams(rawParams);
  // PR-B-β — also keep a structured copy that preserves nested
  // objects / arrays.  ``stringifyParams`` drops those, but the
  // generic builder + module Build surfaces consume them when
  // seeding their forms.
  const paramsStructured = preserveStructuredParams(rawParams);

  // 1. PR2 — backend-runnable primitives get the schema-driven
  //    generic builder (or, downstream, the owning module's dual-view
  //    Build surface via module-first dispatch).  These tools have a
  //    working ``POST /api/v1/tools/{name}/run`` endpoint so the form
  //    can actually execute against the backend.
  if (isRunnablePrimitive(canonical)) {
    return {
      kind: 'generic_builder',
      toolName: canonical,
      params,
      paramsStructured,
    };
  }

  // 2. Stage 1 — workflow-incompatible: the tool ships on the backend
  //    (callable via MCP) but its output shape can't be lifted into a
  //    ``Series`` / ``Panel`` artifact for the workflow bridge.  Calling
  //    ``POST /tools/{name}/run`` would return the FastAPI ``{ok: false,
  //    error: "..."}`` envelope, so we surface an honest unsupported
  //    card instead of routing to the generic builder.  Per-tool reason
  //    text mirrors the backend's ``WORKFLOW_INCOMPATIBLE_TOOLS``
  //    rationale verbatim.
  if (isWorkflowIncompatibleTool(canonical)) {
    return {
      kind: 'workflow_incompatible',
      toolName: canonical,
      params,
      paramsStructured,
    };
  }

  // 3. Known but not runnable (manifest-only, paused, or otherwise
  //    not in ``_PRIMITIVE_SPECS``) — surface the honest paused card
  //    rather than drop into the decode-error path.
  if (isKnownBackendTool(canonical)) {
    return {
      kind: 'unsupported_known',
      toolName: canonical,
      params,
      paramsStructured,
    };
  }

  // 4. Truly unknown — caller falls through to ``null`` handling
  //    (decode-error card for single-best, drop from list for multi).
  return null;
}

/** Public entry point.  Returns the chosen view + its params dict, or
 *  ``null`` when the context is unparseable / empty / contains only
 *  truly-unknown tools.
 *
 *  Priority on multi-tool contexts: generic-builder >
 *  workflow-incompatible > unsupported-known.  The lower-priority
 *  entries are dropped from THIS lookup; ``decodePrimitiveList`` keeps
 *  all of them for multi-card composition. */
export function decodePrimitiveContext(
  raw: string,
): DecodedPrimitive | null {
  let parsed: WorkspaceContext;
  try {
    parsed = JSON.parse(decodeURIComponent(raw)) as WorkspaceContext;
  } catch {
    return null;
  }
  if (!parsed?.tools?.length) return null;

  let best: DecodedPrimitive | null = null;
  let bestScore = -1;

  for (const t of parsed.tools) {
    const decoded = decodeOne(t.tool, t.params);
    if (decoded === null) continue;
    const score = priorityOf(decoded);
    if (score >= bestScore) {
      bestScore = score;
      best = decoded;
    }
  }
  return best;
}

/** R6.3 — multi-card decoder.  Returns the FULL list of recognised
 *  entries in the order they appear in the source context, so the
 *  multi-card canvas can render N comparison cards side-by-side.
 *
 *  PR1 — unsupported-known entries ARE included in the list so the
 *  multi-card surface can render an honest "this tool is paused" card
 *  next to the working primitives.  Truly-unknown names are still
 *  filtered out. */
export function decodePrimitiveList(raw: string): DecodedPrimitive[] {
  let parsed: WorkspaceContext;
  try {
    parsed = JSON.parse(decodeURIComponent(raw)) as WorkspaceContext;
  } catch {
    return [];
  }
  if (!parsed?.tools?.length) return [];

  const out: DecodedPrimitive[] = [];
  for (const t of parsed.tools) {
    const decoded = decodeOne(t.tool, t.params);
    if (decoded === null) continue;
    out.push(decoded);
  }
  return out;
}

// ----------------------------------------------------------------------------
// Stage D — detailed multi-tool decode for the generic DAG container.
// ----------------------------------------------------------------------------

/** One decoded entry zipped with its source ``WorkspaceContextTool`` and
 *  its index in the ORIGINAL ``tools`` array.  ``sourceIndex`` is the
 *  original index (NOT the filtered position) so dependency edges
 *  (which reference original indices) resolve correctly even when some
 *  entries were dropped (truly-unknown). */
export interface DecodedContextEntry {
  decoded: DecodedPrimitive;
  source: WorkspaceContextTool;
  sourceIndex: number;
}

/** Full detailed decode of a multi-tool ``?context=`` payload: the
 *  originating prompt (if the supervisor recorded one), the dependency
 *  edges (if any — absent ⇒ parallel), and the decoded entries each
 *  carrying their source tool (so the DAG can surface per-node status /
 *  error / domain that the flat ``decodePrimitiveList`` drops). */
export interface DecodedWorkspaceContext {
  prompt?: string;
  edges: WorkspaceContextEdge[];
  entries: DecodedContextEntry[];
}

/** Stage D — parse the context ONCE and return the prompt + edges +
 *  decoded entries (with source meta) for the generic multi-tool DAG
 *  container.  Truly-unknown tool names are dropped.  Single source of
 *  truth for the DAG's data model — the dagModel builder consumes
 *  this. */
export function decodeWorkspaceContextDetailed(
  raw: string,
): DecodedWorkspaceContext {
  let parsed: WorkspaceContext;
  try {
    parsed = JSON.parse(decodeURIComponent(raw)) as WorkspaceContext;
  } catch {
    return { edges: [], entries: [] };
  }
  if (!parsed?.tools?.length) return { edges: [], entries: [] };

  const entries: DecodedContextEntry[] = [];
  parsed.tools.forEach((t, sourceIndex) => {
    const decoded = decodeOne(t.tool, t.params);
    if (decoded === null) return;
    entries.push({ decoded, source: t, sourceIndex });
  });

  const prompt =
    typeof parsed.prompt === 'string' && parsed.prompt.trim()
      ? parsed.prompt.trim()
      : undefined;

  return {
    prompt,
    edges: Array.isArray(parsed.edges) ? parsed.edges : [],
    entries,
  };
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function priorityOf(decoded: DecodedPrimitive): number {
  if (decoded.kind === 'generic_builder') return GENERIC_BUILDER_PRIORITY;
  if (decoded.kind === 'workflow_incompatible')
    return WORKFLOW_INCOMPATIBLE_PRIORITY;
  return UNSUPPORTED_KNOWN_PRIORITY;
}

/** PR-B-β — preserve EVERY non-null param verbatim, including nested
 *  objects / arrays.  ``stringifyParams`` (below) is the flat
 *  string-only projection; this preserves the full structure for
 *  surfaces that consume nested config (``series_spec`` /
 *  ``regressor_specs``, generic builder's structured form fields).
 *
 *  Pure: returns a fresh dict — does not mutate ``params``.  Null /
 *  undefined values are dropped so consumers don't have to defend
 *  against them.  Nested values are JSON-shaped on the wire, so we
 *  pass them through unchanged — JSON.parse already gave us plain
 *  objects / arrays. */
function preserveStructuredParams(
  params: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === null || v === undefined) continue;
    out[k] = v;
  }
  return out;
}

/** Coerce an MCP params dict into a flat string-only dict.  Drops nulls
 *  and stringifies numbers / booleans; skips objects/arrays.
 *
 *  PR-B-β — kept as the flat scalar projection.  Surfaces that need
 *  nested values read ``DecodedPrimitive.paramsStructured`` instead. */
function stringifyParams(
  params: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === null || v === undefined) continue;
    if (typeof v === 'string') out[k] = v;
    else if (typeof v === 'number' || typeof v === 'boolean') out[k] = String(v);
  }
  return out;
}
