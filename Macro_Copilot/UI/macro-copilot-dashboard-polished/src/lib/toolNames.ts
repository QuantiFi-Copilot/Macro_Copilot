// ============================================================================
// Stage 3 hybrid derivation (see end of file)
// ----------------------------------------------------------------------------
// The four data registries below (KNOWN_BACKEND_TOOLS,
// RUNNABLE_PRIMITIVE_TOOLS, UNSUPPORTED_KNOWN_TOOLS,
// UNSUPPORTED_KNOWN_REASONS) became HYBRID at Stage 3: the existing
// hand-authored literals are renamed ``_HAND_AUTHORED_*`` and a
// module-derived contribution is computed from ``ALL_PRIMITIVE_MODULES``.
// The public exports are the unions.  WORKFLOW_INCOMPATIBLE_TOOLS and
// the workflow registry stay hand-authored only (preserves the Stage 1
// decoder routing semantics — the module's tier-level classification
// is bookkeeping, the decoder's set determines routing).
// ============================================================================

import { ALL_PRIMITIVE_MODULES } from '@/modules';

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
const _HAND_AUTHORED_KNOWN_BACKEND_TOOLS = new Set<string>([
  // ----------------------------------------------------------------
  // Stage 4a — sovereign + OIS entries (build_sovereign_yield_panel,
  // calculate_{beta_adjusted_spread, breakeven_inflation, butterfly,
  // cross_market_spread, curve_spread, half_life, ois_butterfly,
  // ois_cross_market_spread, ois_curve_spread, ois_forward_rate,
  // pca_yield_curve, rolling_regression, swap_spread,
  // yield_change_attribution_pca, zscore_custom}, classify_curve_move,
  // compute_financing_rate, get_{ois_rate_level, yield_levels},
  // scan_extremes, scan_ois_extremes) were removed from this hand-
  // authored set.  Every removed entry now lives under
  // src/modules/primitives/<name>/module.ts and the module-derived
  // contribution below feeds them back into the union, so the public
  // KNOWN_BACKEND_TOOLS export is unchanged.
  // ----------------------------------------------------------------
  // Factory-ported bond_futures primitives (ADR 0013)
  'get_futures_price_level_tool',
  'get_futures_volume_oi_tool',
  'scan_bond_futures_extremes_tool',
  // Factory-ported policy_futures primitives (ADR 0013)
  'build_policy_futures_strip_panel_tool',
  'get_scan_policy_futures_extremes_tool',
  'policy_futures_get_futures_butterfly_simple_tool',
  'policy_futures_get_futures_calendar_spread_tool',
  'policy_futures_get_futures_cross_market_spread_tool',
  'policy_futures_get_futures_pack_average_simple_tool',
  'policy_futures_get_futures_price_level_tool',
  'policy_futures_get_futures_strip_snapshot_tool',
  'policy_futures_get_volume_open_interest_snapshot_tool',
  // Factory-ported inflation_indexed_bonds primitives (ADR 0013)
  'build_linker_panel_tool',
  'scan_inflation_linkers_extremes_tool',
  // Factory-ported inflation_swaps primitives (ADR 0013)
  'build_zcis_panel_tool',
  'scan_inflation_swaps_extremes_tool',
  // ----------------------------------------------------------------
  // Stage 1 — backend-runnable primitives missing from the registry.
  // These tools exist in ``rates_agent.workflows._PRIMITIVE_SPECS``
  // on ``build`` today but were never added to the frontend
  // registries.  Adding them here AND to ``RUNNABLE_PRIMITIVE_TOOLS``
  // below routes them through the generic schema-driven builder
  // (``GenericPrimitiveBuilder``) so the user can configure + execute
  // them honestly from Library / Ask handoff.
  // ----------------------------------------------------------------
  // Phase-3 cash-bond + event primitives
  'calculate_otr_ofr_spread_tool',
  'calculate_cpi_surprise_tool',
  'calculate_nfp_surprise_tool',
  // PR #177 inflation_indexed_bonds primitives
  'get_real_yield_level_tool',
  'calculate_breakeven_inflation_simple_tool',
  'calculate_forward_breakeven_simple_tool',
  'calculate_breakeven_curve_spread_tool',
  'calculate_cross_country_breakeven_spread_simple_tool',
  'calculate_real_yield_curve_spread_tool',
  'calculate_cross_country_real_yield_spread_simple_tool',
  'calculate_real_yield_butterfly_tool',
  'calculate_breakeven_butterfly_tool',
  // PR #177 inflation_swaps primitives
  'calculate_inflation_swap_rate_level_tool',
  'calculate_inflation_swap_curve_spread_tool',
  'calculate_inflation_swap_forward_tool',
  'calculate_cross_market_inflation_swap_spread_tool',
  'calculate_swap_breakeven_basis_simple_tool',
  'calculate_inflation_swap_butterfly_tool',
  // ----------------------------------------------------------------
  // Stage 1 — workflow-incompatible tools (backend ships them in
  // ``WORKFLOW_INCOMPATIBLE_TOOLS`` but they cannot be routed through
  // ``/tools/{name}/run`` because their output shape isn't a Series
  // or Panel.  Surface via the honest ``workflow_incompatible`` decoder
  // kind, NOT the generic builder.  ``classify_curve_move_tool`` is
  // also workflow-incompatible but already has a typed-view path
  // (``regime``) so it's listed above.
  // ----------------------------------------------------------------
  'get_otr_history_tool',
  'calculate_wirp_meeting_pricing_tool',
]);

// Stage 3 — module-derived contribution.  Every module's toolName
// joins KNOWN_BACKEND_TOOLS via the union below; today the modules
// echo the hand-authored entries verbatim so the resulting set is
// behaviour-identical to Stage 1.  From Stage 4a onward, as the
// hand-authored entries shrink and the modules carry their own
// surface refs, this derivation becomes the primary source.
const _MODULE_DERIVED_KNOWN_BACKEND_TOOLS = new Set<string>(
  ALL_PRIMITIVE_MODULES.map((m) => m.toolName),
);

/** Every tool name the backend / manifest declares.  Stage 3 hybrid:
 *  union of hand-authored (Stage 1) + module-derived (Stage 3+). */
export const KNOWN_BACKEND_TOOLS: ReadonlySet<string> = new Set<string>([
  ..._HAND_AUTHORED_KNOWN_BACKEND_TOOLS,
  ..._MODULE_DERIVED_KNOWN_BACKEND_TOOLS,
]);

/** True when ``name`` (normalised) is a tool the backend / manifest
 *  declares — i.e. NOT a truly-unknown / typo / hallucinated name.
 *  Build uses this to decide between the unsupported-known card and
 *  the decode-error card. */
export function isKnownBackendTool(name: string): boolean {
  return KNOWN_BACKEND_TOOLS.has(normalizeToolName(name));
}

// ----------------------------------------------------------------------------
// RUNNABLE_PRIMITIVE_TOOLS — PR2
// ----------------------------------------------------------------------------
//
// Closed set of tools that have a working ``POST /api/v1/tools/{name}/run``
// endpoint — i.e. they're registered in
// ``rates_agent.workflows._PRIMITIVE_SPECS`` and can execute with the
// schema-driven ``runPrimitive`` call.  PR2's generic primitive
// builder gates on this set: a runnable primitive without a typed
// view gets the configurable schema-driven surface; a known-but-
// not-runnable tool (manifest-only / no ``PrimitiveSpec`` entry)
// still surfaces the honest ``unsupported_known`` card.
//
// Source of truth: ``grep -E '^        tool_name=' rates_agent/workflows/__init__.py``
// + the ``_PRIMITIVE_SPECS`` dict keys.  Verified at PR review time;
// adding a primitive to the backend = add it here too (the
// routingCoverage test asserts the count + names).

const _HAND_AUTHORED_RUNNABLE_PRIMITIVE_TOOLS = new Set<string>([
  // ----------------------------------------------------------------
  // Stage 4a — sovereign + OIS runnable entries (the 18 generic-
  // runnable tools that used to live here verbatim) were removed.
  // Each one now ships as a module under src/modules/primitives/
  // with tiers including ``generic_runnable``; the module-derived
  // contribution below feeds them back into the union, so the
  // public RUNNABLE_PRIMITIVE_TOOLS export is unchanged.
  // ----------------------------------------------------------------
  // Factory-ported bond_futures primitives (ADR 0013)
  'get_futures_price_level_tool',
  'get_futures_volume_oi_tool',
  'scan_bond_futures_extremes_tool',
  // Factory-ported policy_futures primitives (ADR 0013)
  'build_policy_futures_strip_panel_tool',
  'get_scan_policy_futures_extremes_tool',
  'policy_futures_get_futures_butterfly_simple_tool',
  'policy_futures_get_futures_calendar_spread_tool',
  'policy_futures_get_futures_cross_market_spread_tool',
  'policy_futures_get_futures_pack_average_simple_tool',
  'policy_futures_get_futures_price_level_tool',
  'policy_futures_get_futures_strip_snapshot_tool',
  'policy_futures_get_volume_open_interest_snapshot_tool',
  // Factory-ported inflation_indexed_bonds primitives (ADR 0013)
  'build_linker_panel_tool',
  'scan_inflation_linkers_extremes_tool',
  // Factory-ported inflation_swaps primitives (ADR 0013)
  'build_zcis_panel_tool',
  'scan_inflation_swaps_extremes_tool',
  // ----------------------------------------------------------------
  // Stage 1 — the 18 backend-runnable primitives that ship in
  // ``_PRIMITIVE_SPECS`` on ``build`` today but were missing from
  // the frontend registry.  All route through the generic
  // schema-driven builder via the contextDecoder's
  // ``isRunnablePrimitive`` branch.
  // ----------------------------------------------------------------
  // Phase-3 cash-bond + event primitives
  'calculate_otr_ofr_spread_tool',
  'calculate_cpi_surprise_tool',
  'calculate_nfp_surprise_tool',
  // PR #177 inflation_indexed_bonds primitives
  'get_real_yield_level_tool',
  'calculate_breakeven_inflation_simple_tool',
  'calculate_forward_breakeven_simple_tool',
  'calculate_breakeven_curve_spread_tool',
  'calculate_cross_country_breakeven_spread_simple_tool',
  'calculate_real_yield_curve_spread_tool',
  'calculate_cross_country_real_yield_spread_simple_tool',
  'calculate_real_yield_butterfly_tool',
  'calculate_breakeven_butterfly_tool',
  // PR #177 inflation_swaps primitives
  'calculate_inflation_swap_rate_level_tool',
  'calculate_inflation_swap_curve_spread_tool',
  'calculate_inflation_swap_forward_tool',
  'calculate_cross_market_inflation_swap_spread_tool',
  'calculate_swap_breakeven_basis_simple_tool',
  'calculate_inflation_swap_butterfly_tool',
]);

// Stage 3 — module-derived contribution: every module that claims the
// ``generic_runnable`` runtime-status tier.
const _MODULE_DERIVED_RUNNABLE_PRIMITIVE_TOOLS = new Set<string>(
  ALL_PRIMITIVE_MODULES
    .filter((m) => m.tiers.includes('generic_runnable'))
    .map((m) => m.toolName),
);

/** Every tool with a backend run endpoint.  Stage 3 hybrid: union of
 *  hand-authored (Stage 1) + module-derived (Stage 3+). */
export const RUNNABLE_PRIMITIVE_TOOLS: ReadonlySet<string> = new Set<string>([
  ..._HAND_AUTHORED_RUNNABLE_PRIMITIVE_TOOLS,
  ..._MODULE_DERIVED_RUNNABLE_PRIMITIVE_TOOLS,
]);

/** True when the tool has a backend run endpoint (the FastAPI
 *  ``POST /api/v1/tools/{name}/run`` surface, backed by
 *  ``rates_primitive_resolver``).  PR2 uses this to decide between
 *  the generic schema-driven builder and the paused / unsupported
 *  card.  ``classify_curve_move_tool``, ``calculate_butterfly_tool``,
 *  ``scan_extremes_tool``, ``scan_ois_extremes_tool`` are manifest-
 *  only and return ``false`` here. */
export function isRunnablePrimitive(name: string): boolean {
  return RUNNABLE_PRIMITIVE_TOOLS.has(normalizeToolName(name));
}

// ----------------------------------------------------------------------------
// WORKFLOW_INCOMPATIBLE_TOOLS — Stage 1.
// ----------------------------------------------------------------------------
//
// Mirrors the backend's ``rates_agent.workflows.WORKFLOW_INCOMPATIBLE_TOOLS``
// dict.  These primitives are REAL (callable via MCP, ship a tool
// function) but their output shape is intentionally NOT bridge-
// compatible (categorical labels, SCD2 transition logs, per-meeting
// snapshots — none of which lift cleanly into a ``Series`` or
// ``Panel`` artifact).  Calling ``POST /api/v1/tools/{name}/run``
// against them returns the FastAPI ``{"ok": false, "error": ...}``
// envelope (per ``api/routes/workflows/execute.py``) because the
// primitive isn't in ``_PRIMITIVE_SPECS``.
//
// The frontend surfaces them via the ``workflow_incompatible``
// contextDecoder kind so the user sees an honest unsupported card
// instead of being routed into a generic builder that would fail.
//
// ``classify_curve_move_tool`` is also workflow-incompatible on the
// backend but already has a typed-view path (``regime``) which the
// contextDecoder selects before the workflow-incompatible check,
// so it does NOT appear here.

export const WORKFLOW_INCOMPATIBLE_TOOLS: ReadonlySet<string> = new Set<string>([
  'get_otr_history_tool',
  'calculate_wirp_meeting_pricing_tool',
]);

/** True when ``name`` (normalised) is in the workflow-incompatible
 *  set — the tool ships on the backend but cannot be lifted into a
 *  Series / Panel artifact for the generic builder.  Used by
 *  ``contextDecoder`` to emit ``{kind: 'workflow_incompatible'}``. */
export function isWorkflowIncompatibleTool(name: string): boolean {
  return WORKFLOW_INCOMPATIBLE_TOOLS.has(normalizeToolName(name));
}

// ----------------------------------------------------------------------------
// Unsupported-but-known registry
// ----------------------------------------------------------------------------
//
// PR1 introduced this set as "every known tool without a Build
// renderer."  PR2 narrows the scope: a tool only lands here if Build
// CAN'T run it at all — i.e. no typed view, no model builder, and no
// backend ``POST /tools/{name}/run`` entry either.  Everything that
// USED to be on this list AND has a working ``_PRIMITIVE_SPECS`` entry
// (8 tools) now routes to PR2's schema-driven generic builder instead.
//
// Today the only entry is ``scan_ois_extremes_tool`` — declared in the
// rates manifest but with no compute implementation yet (no
// ``PrimitiveSpec``, no typed detail endpoint).  Surface it explicitly
// so the user gets an honest paused card with a "what works today"
// hint pointing at the sovereign-bond scanner.
//
// Membership here ⇒ Build renders the unsupported-known card with
// the per-tool reason in ``UNSUPPORTED_KNOWN_REASONS`` below.

const _HAND_AUTHORED_UNSUPPORTED_KNOWN_TOOLS = new Set<string>([
  // Manifest-declared but no backend implementation; clicking opens
  // an unsupported card explaining the gap.  No ``PrimitiveSpec`` ⇒
  // no ``POST /tools/{name}/run`` path ⇒ no generic builder ⇒ this
  // is the right surface.
  'scan_ois_extremes_tool',
]);

// Stage 3 — module-derived contribution: every module that claims the
// ``paused`` runtime-status tier.
const _MODULE_DERIVED_UNSUPPORTED_KNOWN_TOOLS = new Set<string>(
  ALL_PRIMITIVE_MODULES
    .filter((m) => m.tiers.includes('paused'))
    .map((m) => m.toolName),
);

/** Every tool surfacing the unsupported-known card.  Stage 3 hybrid. */
export const UNSUPPORTED_KNOWN_TOOLS: ReadonlySet<string> = new Set<string>([
  ..._HAND_AUTHORED_UNSUPPORTED_KNOWN_TOOLS,
  ..._MODULE_DERIVED_UNSUPPORTED_KNOWN_TOOLS,
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
 *  doesn't see a bland "this tool is known" caption.
 *
 *  PR2 — the per-tool entries for tools that now have a working
 *  ``POST /tools/{name}/run`` endpoint (swap spread, OIS spreads,
 *  breakeven, sovereign yield panel, financing rate, zscore_custom,
 *  OIS rate level) were retired because those tools route through the
 *  generic schema-driven builder now.  Only the genuinely-paused
 *  ``scan_ois_extremes_tool`` survives. */
const _HAND_AUTHORED_UNSUPPORTED_KNOWN_REASONS: Record<string, UnsupportedKnownReason> = {
  scan_ois_extremes_tool: {
    label: 'OIS extremes scanner',
    reason:
      'Declared in the manifest but the backend has no live route yet — this tool is paused.',
    whatWorksNow:
      'Ask cannot run it today; the sovereign-bond scanner is the closest equivalent.',
  },
  // Stage 1 — workflow-incompatible reasons (mirror the backend's
  // ``WORKFLOW_INCOMPATIBLE_TOOLS`` dict rationale verbatim).
  get_otr_history_tool: {
    label: 'OTR history (transition log)',
    reason:
      'SCD2 transition log + identifier snapshot (CUSIP / ISIN / vendor_ticker / maturity_date with effective_from / effective_to windows).  List-shaped categorical output, not a numeric Series or Panel; the workflow bridge cannot lift it into an artifact for composition.',
    whatWorksNow:
      'Ask can run it via MCP and surface the transition log inline; the typed-detail endpoint is forthcoming.',
  },
  calculate_wirp_meeting_pricing_tool: {
    label: 'WIRP per-meeting pricing',
    reason:
      'List of per-meeting WIRP snapshots — each meeting carries identifier columns plus Bloomberg-ingested numeric fields surfaced verbatim per P12.  Output is the natural "N meeting snapshots" shape; not a single numeric Series or wide-format Panel; the bridge cannot dispatch it.',
    whatWorksNow:
      'Ask can run it via MCP and surface the meeting strip inline; the typed-detail endpoint is forthcoming.',
  },
};

// Stage 3 — module-derived contribution: every module that ships an
// ``unsupportedReason``.  Hand-authored entries take precedence on
// conflict (deterministic; matches the Stage 1 source-of-truth).  New
// entries from modules surface for tools whose decoder routes to a
// typed view at runtime (so the reason is documentation only); the
// extra entries are harmless because the UnsupportedKnownToolCanvas
// never mounts for them.
const _MODULE_DERIVED_UNSUPPORTED_KNOWN_REASONS: Record<
  string,
  UnsupportedKnownReason
> = Object.fromEntries(
  ALL_PRIMITIVE_MODULES
    .filter((m) => m.unsupportedReason != null)
    .map((m) => [m.toolName, m.unsupportedReason as UnsupportedKnownReason]),
);

/** Per-tool copy for the unsupported-known card.  Stage 3 hybrid:
 *  hand-authored entries WIN on conflict (Object.assign order). */
export const UNSUPPORTED_KNOWN_REASONS: Record<string, UnsupportedKnownReason> = {
  ..._MODULE_DERIVED_UNSUPPORTED_KNOWN_REASONS,
  ..._HAND_AUTHORED_UNSUPPORTED_KNOWN_REASONS,
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
