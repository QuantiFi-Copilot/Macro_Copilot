// ============================================================================
// contextDecoder — translate Ask's ``?context=`` URL param into a Build view.
// ----------------------------------------------------------------------------
// PR1 (Build Routing Coverage): the decoder is the single source of
// truth for "what Build surface should we land on for tool X?".  It
// returns a deterministic ``DecodedPrimitive`` for every recognised
// tool — typed primitive view, model-builder redirect, generic
// schema-driven builder, or unsupported-known card — and ``null``
// only for malformed payloads / truly-unknown tools.
//
// History
// -------
// Phase R1.2 reintroduced the bridge that the deleted ``WorkspacePage``
// used to provide.  R6.1 added name normalisation + the rich-model
// (``kind: 'builder'``) variant.  R6.3 added ``decodePrimitiveList``
// for multi-tool comparison grids.  PR1 added the
// ``kind: 'unsupported_known'`` variant + the ``KNOWN_BACKEND_TOOLS``
// gate so the orange decode-error card only fires for genuinely-
// unparseable payloads.  PR2 splits that variant: tools with a backend
// run endpoint and no typed view route to ``kind: 'generic_builder'``
// (configurable schema-driven surface), while manifest-only tools
// (no ``PrimitiveSpec`` entry) still surface ``kind: 'unsupported_known'``
// because trying to ``POST /tools/{name}/run`` against them would 500.
//
// Decoder flow per tool entry
// ---------------------------
//   1. Normalise the tool name via ``normalizeToolName``.
//   2. If model-registry metadata exists → ``kind: 'builder'`` (highest
//      priority — explicit analytical playground).
//   3. Else if the tool maps to a typed view → ``kind: <view>``.
//   4. Else if the tool has a backend run endpoint
//      (``isRunnablePrimitive``) → ``kind: 'generic_builder'`` (PR2 —
//      schema-driven editable surface that calls
//      ``POST /tools/{name}/run``).
//   5. Else if the tool is in ``KNOWN_BACKEND_TOOLS`` → ``kind:
//      'unsupported_known'`` (known but not runnable; honest paused
//      card).
//   6. Else → drop the entry (truly-unknown name).
//
// Builder always wins when context carries mixed tools (the user
// explicitly invoked an analytical primitive, so the standalone
// playground is the right landing).  Among typed primitives, chart-
// bearing views beat scanner-tabular beats regime-classification beats
// forward-placeholder.  Generic-builder sits BELOW every typed-view
// priority (a typed chart is more informative than a configure-and-run
// form) but ABOVE unsupported-known (a runnable surface beats a paused
// card).  Among unsupported-known entries we surface the FIRST in
// original order.
// ============================================================================

import type { WorkspaceContext } from '@/types/copilot';
import { hasModelMetadata } from '@/lib/modelRegistry';
import {
  isKnownBackendTool,
  isRunnablePrimitive,
  normalizeToolName,
} from '@/lib/toolNames';

/** Closed family of typed primitive views supported on Build today. */
export type PrimitiveViewKind =
  | 'spread'
  | 'cross_market'
  | 'butterfly'
  | 'yield'
  | 'regime'
  | 'scanner'
  | 'forward';

/** Result of decoding a ``?context=`` URL param.  Four discriminated
 *  variants:
 *    - typed primitive (chart-bearing / scanner / regime / forward
 *      placeholder) — caller mounts ``VirtualPrimitiveCanvas`` with
 *      the typed-detail fetch.
 *    - ``builder`` — caller navigates to ``/workspace?builder=<toolName>``
 *      and the standalone model playground (R4) materialises.
 *    - ``generic_builder`` (PR2) — caller renders the schema-driven
 *      ``GenericPrimitiveBuilder`` (left rail: form generated from the
 *      tool's ``ToolCard.input_fields``; centre: run output via the
 *      auto-renderer).  Used for runnable primitives without a bespoke
 *      typed view (OIS curve / cross-market / forward, swap spread,
 *      breakeven, sovereign yield panel, financing rate, zscore_custom,
 *      OIS rate level).
 *    - ``unsupported_known`` (PR1) — caller renders the
 *      ``UnsupportedKnownToolCanvas`` card so the user sees an honest
 *      "tool is real but Build has no run path yet" affordance instead
 *      of the orange decode-error card.  Reserved for known tools that
 *      AREN'T runnable through ``/tools/{name}/run`` (manifest-only).
 */
export type DecodedPrimitive =
  | {
      kind: PrimitiveViewKind;
      /** Backend-canonical tool name; surfaced as the kicker chip. */
      toolName: string;
      /** Flat string-only params dict.  Matches the typed-detail endpoint
       *  query-param shape. */
      params: Record<string, string>;
      /** PR-B-β — full structured params dict preserving nested
       *  objects / arrays.  ``params`` (above) is the flat string-only
       *  projection typed-detail endpoints accept; ``paramsStructured``
       *  is the source-of-truth shape Ask emitted, used by surfaces
       *  that consume nested values (rich-model builder seed list,
       *  generic-builder series_spec preloading).  Always populated
       *  by the decoder — equal to ``params`` for scalar-only inputs,
       *  carries the extra entries on nested inputs.  Additive:
       *  pre-PR-B-β consumers that read only ``params`` keep working. */
      paramsStructured: Record<string, unknown>;
    }
  | {
      kind: 'builder';
      /** Backend-canonical tool name (e.g. ``calculate_pca_yield_curve_tool``).
       *  Caller appends ``?builder=<toolName>`` to the URL. */
      toolName: string;
      /** Flat string-only params dict forwarded to the builder as deep-
       *  link initial form values (e.g. ``?builder=...&curve_family=UST``). */
      params: Record<string, string>;
      /** PR-B-β — see ``PrimitiveViewKind`` variant.  Lets the rich-
       *  model builder seed ``series_spec`` / ``series_spec_list`` /
       *  multi-tenor controls from an Ask handoff that carried nested
       *  config. */
      paramsStructured: Record<string, unknown>;
    }
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
       *  correctly when Ask supplied them. */
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

/** Map MCP tool names → typed primitive view kinds.  Adding a new entry
 *  is a single-line change here + a new view component + one branch in
 *  the dispatcher.  Names are the BACKEND-CANONICAL prefixed form;
 *  manifest shorthand is resolved by ``normalizeToolName`` before lookup.
 *
 *  PR2 — ``calculate_ois_forward_rate_tool`` used to map here to a
 *  no-op ``forward`` placeholder view.  Now that the generic schema-
 *  driven builder can configure + execute any runnable primitive,
 *  the forward rate routes through it directly (real curve / tenor /
 *  date controls + a working run path).  The ``forward`` typed view
 *  is kept around for the placeholder kind but no tool maps to it
 *  today; PR3+ can revive the entry when a bespoke
 *  ``/detail/forward`` endpoint ships. */
const TOOL_TO_VIEW: Record<string, PrimitiveViewKind> = {
  calculate_curve_spread_tool: 'spread',
  calculate_cross_market_spread_tool: 'cross_market',
  calculate_butterfly_tool: 'butterfly',
  get_yield_levels_tool: 'yield',
  classify_curve_move_tool: 'regime',
  scan_extremes_tool: 'scanner',
};

/** Priority when context carries multiple tools.  Chart-bearing
 *  primitives win over scanner-tabular over regime-classification over
 *  forward-placeholder.  Rich-model (builder) routing wins over all
 *  primitive priorities — the user explicitly invoked an analytical
 *  tool, so the standalone playground is the right destination. */
const VIEW_PRIORITY: Record<PrimitiveViewKind, number> = {
  spread: 5,
  cross_market: 5,
  butterfly: 5,
  yield: 4,
  regime: 3,
  scanner: 2,
  forward: 1,
};

/** Builder routing always beats every primitive priority.  See the
 *  module docstring for rationale. */
const BUILDER_PRIORITY = 100;

/** PR2 — generic-builder routing sits BELOW every typed-view priority
 *  (a typed chart is more informative than a configure-and-run form)
 *  but ABOVE ``unsupported_known`` (a runnable surface beats a paused
 *  card).  When a single context carries one chart primitive + one
 *  generic-builder tool, the chart wins; when ALL tools are generic-
 *  builder, we surface the form. */
const GENERIC_BUILDER_PRIORITY = 0.5;

/** Unsupported-known routing sits BELOW every typed-view priority and
 *  the generic builder, but ABOVE "drop entirely".  When a single
 *  context carries one chart primitive + one unsupported-known tool,
 *  the chart wins; when ALL tools are unsupported-known, we surface
 *  the paused card. */
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
  // objects / arrays.  ``stringifyParams`` drops those (the typed-
  // detail endpoints only accept scalars), but the rich-model + the
  // generic builder surfaces consume them when seeding their forms.
  const paramsStructured = preserveStructuredParams(rawParams);

  // 1. Rich-model tools (highest priority — explicit analytical
  //    playground beats every other primitive surface).
  if (hasModelMetadata(canonical)) {
    return { kind: 'builder', toolName: canonical, params, paramsStructured };
  }

  // 2. Typed primitive views — bespoke chart / scanner / regime cards.
  const view = TOOL_TO_VIEW[canonical];
  if (view) {
    return { kind: view, toolName: canonical, params, paramsStructured };
  }

  // 3. PR2 — backend-runnable primitives without a typed view get the
  //    schema-driven generic builder.  These tools have a working
  //    ``POST /api/v1/tools/{name}/run`` endpoint so the form can
  //    actually execute against the backend.
  if (isRunnablePrimitive(canonical)) {
    return {
      kind: 'generic_builder',
      toolName: canonical,
      params,
      paramsStructured,
    };
  }

  // 4. Known but not runnable (manifest-only, paused, or otherwise
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

  // 5. Truly unknown — caller falls through to ``null`` handling
  //    (decode-error card for single-best, drop from list for multi).
  return null;
}

/** Public entry point.  Returns the chosen view (primitive, builder,
 *  or unsupported-known) + its params dict, or ``null`` when the
 *  context is unparseable / empty / contains only truly-unknown tools.
 *
 *  Priority on multi-tool contexts: builder > chart primitives >
 *  scanner > regime > forward > unsupported-known.  The lower-priority
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
 *  Excludes builder-class entries (rich-model tools).  When the context
 *  contains both a builder and one or more primitives the caller's
 *  policy is "builder wins" — they should call ``decodePrimitiveContext``
 *  first and short-circuit to the ``?builder=`` redirect; this list is
 *  only consulted on the no-builder fall-through.
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
    // Skip rich-model entries — they take the ``?builder=`` redirect
    // path and don't belong in a primitive comparison grid.
    if (decoded.kind === 'builder') continue;
    out.push(decoded);
  }
  return out;
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function priorityOf(decoded: DecodedPrimitive): number {
  if (decoded.kind === 'builder') return BUILDER_PRIORITY;
  if (decoded.kind === 'generic_builder') return GENERIC_BUILDER_PRIORITY;
  if (decoded.kind === 'unsupported_known') return UNSUPPORTED_KNOWN_PRIORITY;
  return VIEW_PRIORITY[decoded.kind];
}

/** PR-B-β — preserve EVERY non-null param verbatim, including nested
 *  objects / arrays.  ``stringifyParams`` (below) is the flat
 *  string-only projection typed-detail endpoints accept; this
 *  preserves the full structure for surfaces that consume nested
 *  config (rich-model builders' ``series_spec`` / ``regressor_specs``,
 *  generic builder's structured form fields).
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
 *  and stringifies numbers / booleans; skips objects/arrays since the
 *  typed-detail endpoints accept scalar query params only.
 *
 *  PR-B-β — kept for typed-detail endpoints' query-param contract.
 *  Surfaces that need nested values read ``DecodedPrimitive.paramsStructured``
 *  instead. */
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
