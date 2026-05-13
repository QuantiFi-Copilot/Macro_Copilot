// ============================================================================
// toolNames.ts — canonical tool-name normalisation + known-tool registry.
// ----------------------------------------------------------------------------
// Several surfaces refer to the same primitive by different name shapes:
//
//   - Backend ``rates_primitive_resolver`` / executor / runPrimitive:
//     full prefixed form, ``calculate_pca_yield_curve_tool``.
//   - Library manifest YAML (``manifesto/03_tool_manifest/...``):
//     historical un-prefixed shorthand for analytical models,
//     ``pca_yield_curve_tool``, and an occasional verb mismatch like
//     ``calculate_ois_rate_level_tool`` (backend ships ``get_ois_rate_
//     level_tool``).
//   - Frontend model registry + contextDecoder lookups:
//     prefixed form, matching the backend.
//   - Persisted workspace ``node.params.tool_name``: prefixed.
//
// PR1 — Build Routing Coverage:  this module is the single source of
// truth for (1) the alias table, (2) the closed set of tool names the
// backend / manifest declares, and (3) which canonical tools have NO
// Build-side renderer yet (so we surface a clear unsupported-known
// state instead of dropping users in the orange decode-error card).
//
// Adding a new alias or marking a tool as supported / unsupported is a
// single-line edit here.  Every Build entry path (Library CTA,
// contextDecoder, Ask handoff, persisted workflow node rendering)
// MUST go through ``normalizeToolName`` before any registry lookup.
// ============================================================================

/** Manifest-shorthand → backend-canonical aliases.  Add entries here as
 *  new manifest tools appear with the un-prefixed shape OR a verb
 *  mismatch.  All values MUST match an entry in
 *  ``rates_primitive_resolver`` on the backend side (verified by
 *  inspection at PR review time and by ``KNOWN_BACKEND_TOOLS`` below).
 *
 *  The mapping is ONE-WAY (shorthand → canonical).  Reverse lookup is
 *  not supported; the canonical name is what every internal surface
 *  speaks. */
const KNOWN_TOOL_ALIASES: Record<string, string> = {
  // Analytical-model shorthand (manifest emits the un-prefixed form;
  // backend / model registry / persisted nodes use ``calculate_<x>_tool``).
  rolling_regression_tool: 'calculate_rolling_regression_tool',
  beta_adjusted_spread_tool: 'calculate_beta_adjusted_spread_tool',
  half_life_tool: 'calculate_half_life_tool',
  pca_yield_curve_tool: 'calculate_pca_yield_curve_tool',
  yield_change_attribution_pca_tool:
    'calculate_yield_change_attribution_pca_tool',
  zscore_custom_tool: 'calculate_zscore_custom_tool',
  // Verb mismatch — manifest published the OIS rate-level tool with the
  // ``calculate_`` prefix; the backend registry uses ``get_`` because
  // it's a snapshot-only primitive (no compute).  Both forms resolve
  // to the backend's canonical name.
  calculate_ois_rate_level_tool: 'get_ois_rate_level_tool',
};

/** Normalise a tool name to the backend-canonical form.
 *
 *  Behaviour:
 *    1. Empty / null input passes through unchanged (callers handle
 *       the empty case).
 *    2. If the input is a KEY in the alias table (manifest shorthand
 *       or verb mismatch), return the canonical form.
 *    3. Otherwise return the input as-is — most tools already use the
 *       canonical name on both sides and don't need rewriting.
 *
 *  This function is pure + side-effect-free; safe to call from any
 *  layer.  Used by ``hasModelMetadata``, ``getModelMetadata``,
 *  ``paramHintFor``, ``decodePrimitiveContext``, ``ToolDetailDrawer``'s
 *  "Open in Build" CTA, ``WorkspaceButton``, ``ActionRow.resolveBuildHref``,
 *  and any future surface that bridges the manifest into a Build / Ask
 *  / Library chassis lookup. */
export function normalizeToolName(name: string): string {
  if (!name) return name;
  const alias = KNOWN_TOOL_ALIASES[name];
  if (alias) return alias;
  return name;
}

/** True when the alias table needs to rewrite the name (i.e. the input
 *  was the manifest shorthand or a verb-mismatch).  Mostly useful for
 *  diagnostics / telemetry — call sites should always use
 *  ``normalizeToolName`` itself. */
export function isAliasedToolName(name: string): boolean {
  return name in KNOWN_TOOL_ALIASES;
}

// ----------------------------------------------------------------------------
// KNOWN tool registry — PR1
// ----------------------------------------------------------------------------
//
// Closed set of tool names the backend ``rates_primitive_resolver`` (or
// the catalogue / MCP surface) declares.  Used by ``contextDecoder`` to
// distinguish three cases:
//
//   1. ``Known + Build renderer`` — typed view OR rich-model builder.
//   2. ``Known + no Build renderer yet`` — surface an explicit
//      "unsupported_known" card instead of the decode-error card.
//   3. ``Truly unknown`` — only this case falls into the
//      "Could not decode workspace context" card.
//
// Values here are the BACKEND-CANONICAL names.  Manifest shorthand is
// normalised by ``normalizeToolName`` before the membership test.

/** Every tool name the backend / manifest declares.  Maintained
 *  manually but verified at PR review time against the inventory:
 *
 *    grep '^        tool_name=' rates_agent/workflows/__init__.py
 *    grep 'tool_function:' manifesto/03_tool_manifest/rates_agent/*.yml
 *
 *  The set includes manifest-only entries (e.g. ``calculate_butterfly_
 *  tool``, ``classify_curve_move_tool``, ``scan_extremes_tool``) which
 *  are NOT in the workflow primitive registry but have typed-detail
 *  endpoints on the rates API. */
export const KNOWN_BACKEND_TOOLS: ReadonlySet<string> = new Set<string>([
  // Sovereign-bond primitives (backend registry)
  'build_sovereign_yield_panel_tool',
  'calculate_beta_adjusted_spread_tool',
  'calculate_breakeven_inflation_tool',
  'calculate_cross_market_spread_tool',
  'calculate_curve_spread_tool',
  'calculate_half_life_tool',
  'calculate_pca_yield_curve_tool',
  'calculate_rolling_regression_tool',
  'calculate_swap_spread_tool',
  'calculate_yield_change_attribution_pca_tool',
  'calculate_zscore_custom_tool',
  'get_yield_levels_tool',
  // OIS primitives (backend registry)
  'calculate_ois_cross_market_spread_tool',
  'calculate_ois_curve_spread_tool',
  'calculate_ois_forward_rate_tool',
  'compute_financing_rate_tool',
  'get_ois_rate_level_tool',
  // Manifest-declared tools that aren't in the workflow registry but
  // ship typed-detail endpoints on the rates API (Build can render
  // them via the existing typed primitive canvas):
  'calculate_butterfly_tool',
  'classify_curve_move_tool',
  'scan_extremes_tool',
  // Manifest-declared, NO backend implementation yet (renders as
  // unsupported_known):
  'scan_ois_extremes_tool',
]);

/** True when ``name`` (normalised) is a tool the backend / manifest
 *  declares — i.e. NOT a truly-unknown / typo / hallucinated name.
 *  Build uses this to decide between the unsupported-known card and
 *  the decode-error card. */
export function isKnownBackendTool(name: string): boolean {
  return KNOWN_BACKEND_TOOLS.has(normalizeToolName(name));
}

// ----------------------------------------------------------------------------
// Unsupported-but-known registry
// ----------------------------------------------------------------------------
//
// Tools that ARE in ``KNOWN_BACKEND_TOOLS`` but have NO Build-side
// renderer today (no entry in ``TOOL_TO_VIEW``, no entry in the model
// registry).  Maintained explicitly so the unsupported-known card can
// surface a tool-specific "what works now" hint without the decoder
// having to encode the negation of every other registry.
//
// Membership here ⇒ Build renders the unsupported-known card with
// the per-tool reason in ``UNSUPPORTED_KNOWN_REASONS`` below.

export const UNSUPPORTED_KNOWN_TOOLS: ReadonlySet<string> = new Set<string>([
  // Sovereign-bond primitives without a typed-view OR model-builder
  // surface in Build today.
  'build_sovereign_yield_panel_tool',
  'calculate_breakeven_inflation_tool',
  'calculate_swap_spread_tool',
  'calculate_zscore_custom_tool',
  // OIS primitives — no Build views shipped yet.
  'calculate_ois_cross_market_spread_tool',
  'calculate_ois_curve_spread_tool',
  'compute_financing_rate_tool',
  'get_ois_rate_level_tool',
  // Manifest-declared but no backend implementation; clicking opens
  // an unsupported card explaining the gap.
  'scan_ois_extremes_tool',
]);

export interface UnsupportedKnownReason {
  /** Short user-facing label for the tool. */
  label: string;
  /** Why it's not in Build yet — one sentence. */
  reason: string;
  /** Where the user CAN reach this tool today (Ask, Library, etc.). */
  whatWorksNow: string;
}

/** Per-tool copy for the unsupported-known card.  Falls back to a
 *  generic message when the tool isn't listed here — but every entry
 *  in ``UNSUPPORTED_KNOWN_TOOLS`` should have a hint so the user
 *  doesn't see a bland "this tool is known" caption. */
export const UNSUPPORTED_KNOWN_REASONS: Record<string, UnsupportedKnownReason> = {
  build_sovereign_yield_panel_tool: {
    label: 'Sovereign yield panel',
    reason:
      'Panel-shaped multi-curve / multi-tenor data; Build does not yet ship a Panel renderer.',
    whatWorksNow:
      'Ask can run it and summarise; persisted workflows that use it (event_study) render the panel as a thin summary card.',
  },
  calculate_breakeven_inflation_tool: {
    label: 'Breakeven inflation',
    reason:
      'Breakeven-inflation primitive needs a paired nominal / real curve picker; no Build view yet.',
    whatWorksNow: 'Ask can run it on demand.',
  },
  calculate_swap_spread_tool: {
    label: 'Swap spread',
    reason:
      'Swap-spread primitive (used inside event_study) has no standalone Build view.',
    whatWorksNow:
      'Ask can run it; event_study workflows that consume it persist as a workspace.',
  },
  calculate_zscore_custom_tool: {
    label: 'Custom z-score',
    reason:
      'Per-series z-score with custom window — used inside backtest; no standalone Build view.',
    whatWorksNow: 'Ask can run it.',
  },
  calculate_ois_cross_market_spread_tool: {
    label: 'OIS cross-market spread',
    reason: 'OIS cross-market view not implemented in Build yet.',
    whatWorksNow: 'Ask can run it; the sovereign-bond cross-market view is the closest equivalent.',
  },
  calculate_ois_curve_spread_tool: {
    label: 'OIS curve spread',
    reason: 'OIS curve-spread view not implemented in Build yet.',
    whatWorksNow:
      'Ask can run it; the sovereign-bond curve-spread view is the closest equivalent.',
  },
  compute_financing_rate_tool: {
    label: 'Financing rate panel',
    reason:
      'Panel-shaped financing-rate output; Build does not yet ship a Panel renderer.',
    whatWorksNow:
      'Ask can run it; persisted backtest workflows (paused) consume it.',
  },
  get_ois_rate_level_tool: {
    label: 'OIS rate level',
    reason:
      'Single-point OIS rate snapshot; no Build view yet (the sovereign-bond yield-level view is the closest equivalent).',
    whatWorksNow: 'Ask can run it.',
  },
  scan_ois_extremes_tool: {
    label: 'OIS extremes scanner',
    reason:
      'Declared in the manifest but the backend has no live route yet — this tool is paused.',
    whatWorksNow:
      'Ask cannot run it today; the sovereign-bond scanner is the closest equivalent.',
  },
};

/** Returns the per-tool unsupported-known reason or a generic fallback
 *  for tools listed in ``UNSUPPORTED_KNOWN_TOOLS`` without an explicit
 *  copy entry.  Pure / side-effect-free. */
export function unsupportedKnownReasonFor(
  toolName: string,
): UnsupportedKnownReason {
  const canonical = normalizeToolName(toolName);
  const specific = UNSUPPORTED_KNOWN_REASONS[canonical];
  if (specific) return specific;
  return {
    label: canonical,
    reason: 'Tool is known to the backend, but Build has no renderer for it yet.',
    whatWorksNow: 'Ask can run it; the Library card describes inputs / outputs.',
  };
}

/** True when ``name`` (normalised) is in the unsupported-known set.
 *  Used by ``contextDecoder`` to emit ``{kind: 'unsupported_known'}``. */
export function isUnsupportedKnownTool(name: string): boolean {
  return UNSUPPORTED_KNOWN_TOOLS.has(normalizeToolName(name));
}

// ----------------------------------------------------------------------------
// Workflow registry
// ----------------------------------------------------------------------------
//
// Closed set of workflow templates the Build surface knows about.
// Matches the catalogue endpoint (``api/routes/workflows/catalogue.py``)
// — backtest is intentionally excluded because it's paused on both
// MCP and HTTP surfaces (``rates_agent/workflows/mcp_server.py:82-95``).

export const KNOWN_WORKFLOWS: ReadonlySet<string> = new Set<string>([
  'event_study',
  'regime_conditioned_relationship',
]);

/** Workflows the backend declares but has paused.  Hit when a chat
 *  result emits ``workflow.routeDecision.template_id = 'backtest'``
 *  (it shouldn't today — backtest is gated out of routing — but the
 *  set lets the UI render an honest "paused" card if it ever appears). */
export const PAUSED_WORKFLOWS: ReadonlySet<string> = new Set<string>([
  'backtest',
]);

export interface WorkflowStatus {
  /** ``known`` — runs through the catalogue + MCP and lands at
   *   ``/workspace/:slug``.
   *  ``paused`` — declared in code but gated off the LLM surface
   *   (see backtest in ``mcp_server.py``).
   *  ``unknown`` — never heard of it; treat as decode error. */
  kind: 'known' | 'paused' | 'unknown';
  templateId: string;
}

export function classifyWorkflow(templateId: string): WorkflowStatus {
  if (KNOWN_WORKFLOWS.has(templateId)) {
    return { kind: 'known', templateId };
  }
  if (PAUSED_WORKFLOWS.has(templateId)) {
    return { kind: 'paused', templateId };
  }
  return { kind: 'unknown', templateId };
}
