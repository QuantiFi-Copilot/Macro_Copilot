// ============================================================================
// contextDecoder — translate Ask's ``?context=`` URL param into a Build view.
// ----------------------------------------------------------------------------
// Phase R1.2 reintroduced the bridge that the deleted ``WorkspacePage`` used
// to provide.  When the user clicks "Open in Build" on an Ask answer, the
// chat encodes the supervisor turn's ``workspaceContext`` (a list of
// {tool, params} from MCP tool calls) into ``/workspace?context=<encoded
// JSON>``.  BuildShell hands the raw string to this decoder which:
//
//   1. JSON-parses it (URI-decoded) into a ``WorkspaceContext``.
//   2. Normalises every tool name through ``normalizeToolName`` so
//      manifest shorthand (e.g. ``half_life_tool``) resolves to the
//      backend-canonical form (``calculate_half_life_tool``).  R6.1.
//   3. Routes to one of two surfaces based on the chosen tool:
//        - rich-model tool (has model-registry metadata) → emits
//          ``{kind: 'builder', toolName}`` so the caller navigates to
//          ``?builder=<toolName>`` (the standalone PCA / RollingRegression
//          / Attribution / HalfLife / BetaAdjustedSpread builder).
//        - typed primitive tool (spread / cross_market / butterfly /
//          yield / regime / scanner / forward) → emits a
//          ``{kind: <view>, toolName, params}`` so the caller mounts
//          the virtual primitive canvas with the typed-detail fetch.
//   4. Picks the most informative tool when context carries multiple
//      — chart-bearing primitives beat scanner-tabular beats regime-
//      classification beats forward-placeholder, with the rich-model
//      surface always winning when present (the user explicitly
//      invoked an analytical tool, so the playground is the right
//      landing).
//
// Returns ``null`` for malformed payloads (bad JSON / empty tools list /
// unknown tool) — caller falls back to the Build empty shell.
// ============================================================================

import type { WorkspaceContext } from '@/types/copilot';
import { hasModelMetadata } from '@/lib/modelRegistry';
import { normalizeToolName } from '@/lib/toolNames';

/** Closed family of typed primitive views supported on Build today. */
export type PrimitiveViewKind =
  | 'spread'
  | 'cross_market'
  | 'butterfly'
  | 'yield'
  | 'regime'
  | 'scanner'
  | 'forward';

/** Result of decoding a ``?context=`` URL param.  Two discriminated
 *  variants:
 *    - ``primitive`` kinds (spread / cross_market / …) — caller mounts
 *      the ``VirtualPrimitiveCanvas`` with the typed-detail fetch.
 *    - ``builder`` — caller navigates to ``/workspace?builder=<toolName>``
 *      and the standalone model playground (R4) materialises.
 */
export type DecodedPrimitive =
  | {
      kind: PrimitiveViewKind;
      /** Backend-canonical tool name; surfaced as the kicker chip. */
      toolName: string;
      /** Flat string-only params dict.  Matches the typed-detail endpoint
       *  query-param shape. */
      params: Record<string, string>;
    }
  | {
      kind: 'builder';
      /** Backend-canonical tool name (e.g. ``calculate_pca_yield_curve_tool``).
       *  Caller appends ``?builder=<toolName>`` to the URL. */
      toolName: string;
      /** Flat string-only params dict forwarded to the builder as deep-
       *  link initial form values (e.g. ``?builder=...&curve_family=UST``). */
      params: Record<string, string>;
    };

/** Map MCP tool names → typed primitive view kinds.  Adding a new entry
 *  is a single-line change here + a new view component + one branch in
 *  the dispatcher.  Names are the BACKEND-CANONICAL prefixed form;
 *  manifest shorthand is resolved by ``normalizeToolName`` before lookup. */
const TOOL_TO_VIEW: Record<string, PrimitiveViewKind> = {
  calculate_curve_spread_tool: 'spread',
  calculate_cross_market_spread_tool: 'cross_market',
  calculate_butterfly_tool: 'butterfly',
  get_yield_levels_tool: 'yield',
  classify_curve_move_tool: 'regime',
  scan_extremes_tool: 'scanner',
  // OIS forward-rate primitive — view ships an explicit "not wired"
  // placeholder until ``/detail/forward`` lands.
  calculate_ois_forward_rate_tool: 'forward',
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

/** Public entry point.  Returns the chosen view (primitive or builder)
 *  + its params dict, or ``null`` when the context is unparseable / empty
 *  / contains only unrecognised tools.
 *
 *  When the context carries multiple tools, the highest-priority one
 *  wins (builder > chart primitives > scanner > regime > forward).
 *  The lower-priority entries are dropped; multi-card composition on
 *  the canvas is the follow-up (R6.3). */
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
    const canonical = normalizeToolName(t.tool);
    const params = stringifyParams(t.params);

    // Rich-model tools always win.
    if (hasModelMetadata(canonical)) {
      if (BUILDER_PRIORITY >= bestScore) {
        bestScore = BUILDER_PRIORITY;
        best = { kind: 'builder', toolName: canonical, params };
      }
      continue;
    }

    // Typed primitive views.
    const kind = TOOL_TO_VIEW[canonical];
    if (!kind) continue;
    const score = VIEW_PRIORITY[kind];
    if (score >= bestScore) {
      bestScore = score;
      best = { kind, toolName: canonical, params };
    }
  }
  return best;
}

/** Coerce an MCP params dict into a flat string-only dict.  Drops nulls
 *  and stringifies numbers / booleans; skips objects/arrays since the
 *  typed-detail endpoints accept scalar query params only. */
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
