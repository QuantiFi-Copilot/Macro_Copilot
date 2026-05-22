// ============================================================================
// buildTypes.ts — domain types specific to the Build (Workspace) surface.
// ----------------------------------------------------------------------------
// Types that don't fit cleanly into ``services/`` (which is wire shape only)
// nor into ``types/`` (which collects cross-surface types).  Things here:
//
//   - ``BuildShellMode``        — empty / building / completed
//   - ``StageCategory``         — primitive vs operator vs output
//                                 (drives the rail / badge color)
//   - ``BuildEmptyCategory``    — taxonomy of the 6 starter tiles in the
//                                 empty state (Mockup A)
//
// We intentionally keep these narrow — every new surface that needs Build
// concepts should add its own type next to the consuming component
// rather than centralising into a god-file.
// ============================================================================

/** Which mode the Build canvas should render.  Decided by
 *  ``BuildShell`` from the URL + workspace detail state. */
export type BuildShellMode = 'empty' | 'building' | 'completed';

/** Visual category for a DAG stage / per-node widget card.
 *
 *  - ``input``      — a primitive that fetches raw data (e.g. yield_levels).
 *                     Cards get the "data" rail color (ice/blue).
 *  - ``transform``  — an operator that reshapes / aggregates artifacts
 *                     (e.g. rolling_zscore, threshold_events).
 *                     Cards get the "analysis" rail color (violet).
 *  - ``output``     — the terminal node OR a node that produces a chart-
 *                     shaped artifact.  Cards get the "anomaly" rail
 *                     color (amber) to emphasise the final result.
 *
 *  This is purely a UI grouping — the substrate doesn't use this
 *  vocabulary.  ``stageCategoryForNode`` (in stageCategory.ts) derives
 *  it from the persisted ``NodeSummary.kind`` + the workspace's
 *  ``focus_node`` (terminal marker). */
export type StageCategory = 'input' | 'transform' | 'output';

/** One entry in the Build empty-state's starter grid.  See Mockup A
 *  for the canonical 6-tile layout. */
export interface BuildEmptyCategory {
  /** Stable id used for analytics / tile keying.  Also the slug
   *  fragment a "launch" handler can route by. */
  id: string;
  label: string;
  description: string;
  /** Lucide icon name — kept as a render-time prop so a future tile
   *  schema fetched from a manifest can supply icons by string. */
  iconName:
    | 'TrendingUp'
    | 'Activity'
    | 'PieChart'
    | 'Filter'
    | 'GitCompare'
    | 'Code';
  /** When true, the tile renders dimmed with a "soon" badge.  Set on
   *  archetypes paused for substrate / data reasons (e.g. backtest
   *  while data prerequisites are pending). */
  soon?: boolean;
  /** One-line caveat shown on dimmed tiles — explains WHY the tile is
   *  paused.  Hidden for non-soon tiles. */
  soonReason?: string;
  /** Pre-filled composer prompt when the tile is clicked.  The
   *  composer drops it in and the user can edit before sending. */
  promptSeed: string;
  /** Optional: the MCP tool name to launch directly in the standalone
   *  model builder when the tile is clicked.  When set, clicking the
   *  tile navigates to ``/workspace?builder=<tool_name>`` (the R4
   *  builder canvas path) instead of seeding the composer.  Reserved
   *  for tiles that map to a single primitive (e.g. "Decompose a move"
   *  → PCA); category-level tiles that span multiple tools should
   *  leave this unset and seed the composer instead. */
  builderTool?: string;
}
