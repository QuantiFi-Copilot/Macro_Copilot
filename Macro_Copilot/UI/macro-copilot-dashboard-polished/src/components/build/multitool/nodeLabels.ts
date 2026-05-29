// ============================================================================
// nodeLabels.ts — Generic, tool-agnostic label helpers for DAG nodes.
// ----------------------------------------------------------------------------
// Stage D.  Shared by the DAG graph (and any future node-pill surface).  All
// labels derive from the module registry's ``displayName`` + the node's
// params — NO tool names are hardcoded.
// ============================================================================

import { getPrimitiveModule } from '@/modules';

/** Standard methodology / display knobs shared across the primitive contract
 *  — excluded from the node's identity summary so a chip reads "USD_TIPS 10Y"
 *  not "USD_TIPS 10Y 365 YLD_YTM_MID 252 60 1".  These key names are
 *  contract-standard, not per-tool. */
const METHODOLOGY_KNOB_KEYS = new Set([
  'lookback_days',
  'field_name',
  'z_score_window_days',
  'z_score_min_periods',
  'z_score_ddof',
]);

/** The instrument-identity params (everything that isn't a standard
 *  methodology/display knob), so a node label reads e.g. "USD_TIPS 10Y" or
 *  "UST USD_TIPS 10Y" generically without knowing the tool's param names. */
export function compactParamSummary(params: Record<string, string>): string {
  const ids = Object.entries(params)
    .filter(([k, v]) => v && !METHODOLOGY_KNOB_KEYS.has(k))
    .map(([, v]) => v);
  return ids.slice(0, 4).join(' ');
}

/** The tool's display name from the module registry; falls back to the raw
 *  tool name for tools without a registered module. */
export function nodeDisplayName(toolName: string): string {
  return getPrimitiveModule(toolName)?.displayName ?? toolName;
}
