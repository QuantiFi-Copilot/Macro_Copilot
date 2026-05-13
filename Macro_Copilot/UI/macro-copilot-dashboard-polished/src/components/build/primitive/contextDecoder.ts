// ============================================================================
// contextDecoder — translate Ask's ``?context=`` URL param into a Build view.
// ----------------------------------------------------------------------------
// Phase R1.2 reintroduces the bridge that the deleted ``WorkspacePage`` used
// to provide.  When the user clicks "Open in Build" on an Ask answer, the
// chat encodes the supervisor turn's ``workspaceContext`` (a list of
// {tool, params} from MCP tool calls) into ``/workspace?context=<encoded
// JSON>``.  BuildShell hands the raw string to this decoder which:
//
//   1. JSON-parses it (URI-decoded) into a ``WorkspaceContext``.
//   2. Picks the most informative tool call from the context — chart-bearing
//      primitives beat scanners which beat regimes which beat forwards.
//   3. Stringifies the chosen tool's params dict.
//   4. Returns a ``DecodedPrimitive`` discriminated union the canvas can
//      dispatch on.
//
// Returns ``null`` for malformed payloads (bad JSON / empty tools list /
// unknown tool) — caller falls back to the Build empty shell.
//
// IMPORTANT: this decoder ONLY knows how to route to the typed primitive
// surface.  Model-class primitives (PCA / rolling regression / attribution)
// will be reintroduced in phase R3 — until then they fall through to ``null``
// and Build shows the empty shell.
// ============================================================================

import type { WorkspaceContext } from '@/types/copilot';

/** Closed family of typed primitive views supported on Build today. */
export type PrimitiveViewKind =
  | 'spread'
  | 'cross_market'
  | 'butterfly'
  | 'yield'
  | 'regime'
  | 'scanner'
  | 'forward';

/** Result of decoding a ``?context=`` URL param. */
export interface DecodedPrimitive {
  kind: PrimitiveViewKind;
  /** Original MCP tool name; surfaced as the kicker chip in the view. */
  toolName: string;
  /** Flat string-only params dict — keys/values pulled off the original
   *  MCP call.  Matches the shape the typed-detail endpoints expect. */
  params: Record<string, string>;
}

/** Map MCP tool names → typed primitive view kinds.  Order maps directly
 *  onto the dispatcher in ``VirtualPrimitiveCanvas`` — adding a new entry
 *  is a single-line change here + a new view component + one branch in
 *  the dispatcher. */
const TOOL_TO_VIEW: Record<string, PrimitiveViewKind> = {
  calculate_curve_spread_tool: 'spread',
  calculate_cross_market_spread_tool: 'cross_market',
  calculate_butterfly_tool: 'butterfly',
  get_yield_levels_tool: 'yield',
  classify_curve_move_tool: 'regime',
  scan_extremes_tool: 'scanner',
  // OIS forward-rate primitive — view ships an explicit "not wired"
  // placeholder until ``/detail/forward`` lands.  Listed here so the
  // dispatcher reads consistently.
  calculate_ois_forward_rate_tool: 'forward',
};

/** Priority when context carries multiple tools.  Chart-bearing
 *  primitives win over scanner-tabular over regime-classification over
 *  forward-placeholder. */
const VIEW_PRIORITY: Record<PrimitiveViewKind, number> = {
  spread: 5,
  cross_market: 5,
  butterfly: 5,
  yield: 4,
  regime: 3,
  scanner: 2,
  forward: 1,
};

/** Public entry point.  Returns the chosen primitive view + its params
 *  dict, or ``null`` when the context is unparseable / empty / contains
 *  only unrecognised tools. */
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
    const kind = TOOL_TO_VIEW[t.tool];
    if (!kind) continue;
    const score = VIEW_PRIORITY[kind];
    if (score >= bestScore) {
      bestScore = score;
      best = {
        kind,
        toolName: t.tool,
        params: stringifyParams(t.params),
      };
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
