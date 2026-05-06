// ============================================================================
// workspaceContext
// ----------------------------------------------------------------------------
// Helpers for translating between:
//   1) the WorkspaceContext payload that the copilot streams in `done` events
//      (a list of {tool, params} from MCP tool calls), and
//   2) the URL-driven workspace state (a tool view + flat parameter dict).
//
// The copilot button base64-ish-encodes the WorkspaceContext into the
// `?context=` URL param.  When the page boots and finds that param it picks
// the most informative tool out of the list (the *last* spread/butterfly
// tool, or the scanner if that's the only one) and rewrites the URL into
// `?tool=...&<params>` form so the rest of the page can treat it as an
// ordinary URL-state load.  This keeps the URL shareable.
// ============================================================================

import type { WorkspaceContext } from '@/types/copilot';
import type { WorkspaceParams, WorkspaceViewType } from '@/types/rates';
import { hasModelMetadata } from '@/lib/modelRegistry';

// Map MCP tool names → workspace view types.  Order matters for picking the
// most-interesting tool out of a multi-tool context: spread/cross-market are
// chartable, scanner is tabular, regime is classification-only.
export const TOOL_TO_VIEW: Record<string, WorkspaceViewType> = {
  calculate_curve_spread_tool: 'spread',
  calculate_cross_market_spread_tool: 'cross_market',
  calculate_butterfly_tool: 'butterfly',
  get_yield_levels_tool: 'yield',
  // Tool name was renamed from `classify_curve_regime_tool` to
  // `classify_curve_move_tool` because the tool classifies a single
  // observed move, not a statistical persistence-state regime.  The
  // workspace VIEW key stays 'regime' because that's the URL
  // parameter / view-name the user-facing Workspace surface uses,
  // and the user-facing word "regime" remains how PMs talk about
  // the output.  Only the internal tool name was renamed.
  classify_curve_move_tool: 'regime',
  scan_extremes_tool: 'scanner',
};

// Priority for picking the "headline" tool when context has multiple — higher
// rank wins.  Charts beat classifications beat scanners.
const VIEW_PRIORITY: Record<WorkspaceViewType, number> = {
  spread: 5,
  cross_market: 5,
  butterfly: 5,
  yield: 4,
  regime: 3,
  scanner: 2,
  forward: 1,
};

/** Discriminator: legacy typed-view route vs the rich model workspace. */
export type DecodedContext =
  | { kind: 'view'; view: WorkspaceViewType; params: WorkspaceParams }
  | { kind: 'model'; toolName: string; params: WorkspaceParams };

// Model-class tool calls beat the typed-view priorities — the rich
// model workspace is always the right surface when an analytical
// primitive (rolling_regression, pca_yield_curve, …) is in the context.
const MODEL_PRIORITY = 100;

/**
 * Decode a `?context=` JSON blob into a single decoded route.  Picks
 * the most informative tool when several are present:
 *   - any registered model primitive wins (routes to the model workspace)
 *   - otherwise fall back to the typed-view priority map
 * Returns null if the payload is malformed or contains no recognised tools.
 */
export function decodeWorkspaceContext(raw: string): DecodedContext | null {
  let parsed: WorkspaceContext;
  try {
    parsed = JSON.parse(decodeURIComponent(raw)) as WorkspaceContext;
  } catch {
    return null;
  }
  if (!parsed?.tools?.length) return null;

  let best: DecodedContext | null = null;
  let bestScore = -1;

  for (const t of parsed.tools) {
    // Model-class primitives (registry-backed) → rich workspace.
    if (hasModelMetadata(t.tool)) {
      if (MODEL_PRIORITY >= bestScore) {
        bestScore = MODEL_PRIORITY;
        best = {
          kind: 'model',
          toolName: t.tool,
          params: stringifyParams(t.params),
        };
      }
      continue;
    }
    // Typed-view fall-through.
    const view = TOOL_TO_VIEW[t.tool];
    if (!view) continue;
    const score = VIEW_PRIORITY[view];
    if (score >= bestScore) {
      bestScore = score;
      best = { kind: 'view', view, params: stringifyParams(t.params) };
    }
  }
  return best;
}

/**
 * Coerce an MCP params dict (Record<string, unknown>) into the flat
 * string-only WorkspaceParams shape used by the URL.  Drops null/undefined
 * and stringifies numbers / booleans.
 */
function stringifyParams(params: Record<string, unknown>): WorkspaceParams {
  const out: WorkspaceParams = {};
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === null || v === undefined) continue;
    if (typeof v === 'string') out[k] = v;
    else if (typeof v === 'number' || typeof v === 'boolean') out[k] = String(v);
    // Skip objects/arrays — the workspace view doesn't need them.
  }
  return out;
}
