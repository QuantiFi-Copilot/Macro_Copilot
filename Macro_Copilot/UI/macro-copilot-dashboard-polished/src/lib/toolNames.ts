// ============================================================================
// toolNames.ts — canonical tool-name normalisation.
// ----------------------------------------------------------------------------
// Several surfaces refer to the same primitive by different name shapes:
//
//   - Backend ``rates_primitive_resolver`` / executor / runPrimitive:
//     full prefixed form, ``calculate_pca_yield_curve_tool``.
//   - Library manifest YAML (``manifesto/03_tool_manifest/...``):
//     historical un-prefixed shorthand, ``pca_yield_curve_tool``.
//   - Frontend model registry + contextDecoder lookups:
//     prefixed form, matching the backend.
//   - Persisted workspace ``node.params.tool_name``: prefixed.
//
// The Library page reads the manifest verbatim, so its ``Open in Build''
// CTA was passing un-prefixed names into ``hasModelMetadata`` and the
// ``?context=`` decoder — both of which key on the prefixed names —
// producing the orange "Could not decode workspace context" card the
// user audit flagged.
//
// This module is the single source of truth for the alias table.  Every
// surface that interops with both the Library and the backend should
// call ``normalizeToolName`` before doing a lookup.  Adding a new
// alias is a single-line edit to ``KNOWN_TOOL_ALIASES`` below.
// ============================================================================

/** Manifest-shorthand → backend-canonical aliases.  Add entries here as
 *  new manifest tools appear with the un-prefixed shape.  All values
 *  MUST match an entry in ``rates_primitive_resolver`` on the backend
 *  side (verified by manual inspection at PR review time).
 *
 *  The mapping is ONE-WAY (shorthand → canonical).  Reverse lookup is
 *  not supported; the canonical name is what every internal surface
 *  speaks. */
const KNOWN_TOOL_ALIASES: Record<string, string> = {
  rolling_regression_tool: 'calculate_rolling_regression_tool',
  beta_adjusted_spread_tool: 'calculate_beta_adjusted_spread_tool',
  half_life_tool: 'calculate_half_life_tool',
  pca_yield_curve_tool: 'calculate_pca_yield_curve_tool',
  yield_change_attribution_pca_tool:
    'calculate_yield_change_attribution_pca_tool',
  zscore_custom_tool: 'calculate_zscore_custom_tool',
};

/** Normalise a tool name to the backend-canonical form.
 *
 *  Behaviour:
 *    1. If the input is already in the alias table's VALUE set (i.e.
 *       already canonical), return it unchanged.
 *    2. If the input is a KEY in the alias table (manifest shorthand),
 *       return the canonical form.
 *    3. Otherwise return the input as-is — most tools already use the
 *       canonical name on both sides and don't need rewriting.
 *
 *  This function is pure + side-effect-free; safe to call from any
 *  layer.  Used by ``hasModelMetadata``, ``getModelMetadata``,
 *  ``paramHintFor``, ``decodePrimitiveContext``, ``ToolDetailDrawer``'s
 *  "Open in Build" CTA, and any future surface that bridges the
 *  Library manifest into a Build / Ask / Build chassis lookup. */
export function normalizeToolName(name: string): string {
  if (!name) return name;
  const alias = KNOWN_TOOL_ALIASES[name];
  if (alias) return alias;
  return name;
}

/** True when the alias table needs to rewrite the name (i.e. the input
 *  was the manifest shorthand).  Mostly useful for diagnostics /
 *  telemetry — call sites should always use ``normalizeToolName``
 *  itself. */
export function isAliasedToolName(name: string): boolean {
  return name in KNOWN_TOOL_ALIASES;
}
