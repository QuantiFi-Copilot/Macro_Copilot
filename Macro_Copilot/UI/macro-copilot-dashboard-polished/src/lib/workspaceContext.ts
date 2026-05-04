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

// Map MCP tool names → workspace view types.  Order matters for picking the
// most-interesting tool out of a multi-tool context: spread/cross-market are
// chartable, scanner is tabular, regime is classification-only.
export const TOOL_TO_VIEW: Record<string, WorkspaceViewType> = {
  calculate_curve_spread_tool: 'spread',
  calculate_cross_market_spread_tool: 'cross_market',
  calculate_butterfly_tool: 'butterfly',
  get_yield_levels_tool: 'yield',
  classify_curve_move_tool: 'regime',
  scan_extremes_tool: 'scanner',
  get_fx_spot_level_tool: 'fx_spot',
  get_fx_carry_tool: 'fx_carry',
  get_fx_forward_curve_tool: 'fx_forward_curve',
  scan_fx_extremes_tool: 'fx_scanner',
  get_fx_realized_vol_tool: 'fx_realized_vol',
  get_fx_trade_setup_tool: 'fx_trade_setup',
  get_fx_macro_risk_overlay_tool: 'fx_macro_risk_overlay',
  get_fx_correlation_beta_tool: 'fx_correlation_beta',
  classify_fx_regime_tool: 'fx_regime_classifier',
  get_fx_vol_risk_premium_tool: 'fx_vol_risk_premium',
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
  fx_scanner: 2,
  forward: 1,
  fx_forward_curve: 1,
  fx_realized_vol: 1,
  fx_trade_setup: 6,
  fx_regime_classifier: 6,
  fx_macro_risk_overlay: 5,
  fx_correlation_beta: 5,
  fx_vol_risk_premium: 4,
  fx_spot: 0,  // Ajouter fx_spot ici
  fx_carry: 0, // Ajouter fx_carry ici
};

export type DecodedContext = {
  view: WorkspaceViewType;
  params: WorkspaceParams;
};

/**
 * Decode a `?context=` JSON blob into a single (view, params) tuple.  Picks
 * the most informative tool when several are present.  Returns null if the
 * payload is malformed or contains no recognised tools.
 */
export function decodeWorkspaceContext(raw: string): DecodedContext | null {
  let parsed: WorkspaceContext;
  try {
    parsed = JSON.parse(decodeURIComponent(raw)) as WorkspaceContext;
  } catch {
    return null;
  }
  if (!parsed?.tools?.length) return null;

  // Score each tool by view priority; higher wins, last-call wins on tie.
  let best: { view: WorkspaceViewType; params: WorkspaceParams } | null = null;
  let bestScore = -1;
  for (const t of parsed.tools) {
    const view = TOOL_TO_VIEW[t.tool];
    if (!view) continue;
    const score = VIEW_PRIORITY[view];
    if (score >= bestScore) {
      bestScore = score;
      best = { view, params: stringifyParams(t.params) };
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
