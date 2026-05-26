// ============================================================================
// categoryDefinitions.ts — the 6 starter tiles on the empty state.
// ----------------------------------------------------------------------------
// Single source of truth for what the empty-state grid offers.  Each
// entry defines:
//   - the visible label / description / icon
//   - whether it's an active archetype (clickable) or paused (dimmed
//     with a "soon" badge and a one-line reason)
//   - the prompt seed that lands in the composer when clicked
//
// The 6 here mirror Mockup A.  As archetypes come online (FX, vol,
// credit), new tiles slot in; this stays the one file to edit.
// ============================================================================

import type { BuildEmptyCategory } from '../lib/buildTypes';

export const BUILD_CATEGORIES: BuildEmptyCategory[] = [
  {
    id: 'run_a_backtest',
    label: 'Run a backtest',
    description:
      'Build and test a trading or risk-management strategy over historical data.',
    iconName: 'TrendingUp',
    soon: true,
    soonReason:
      'Paused while data prerequisites (DV01, OTR, CPI carry, real O/N OIS) are in flight. Conditional forward-move studies still ship in the meantime.',
    promptSeed:
      'Backtest a thesis: when {signal} crosses {threshold}, go long {leg_a} and short {leg_b}, hold {N} business days.',
  },
  {
    id: 'analyze_a_spread',
    label: 'Analyze a spread',
    description:
      'Visualize and decompose spreads, basis, and cross-market relationships.',
    iconName: 'Activity',
    promptSeed:
      'Show me the UST 2s10s spread over the last 2 years with a 252-day rolling z-score.',
  },
  {
    id: 'decompose_a_move',
    label: 'Decompose a move',
    description:
      'Break down a yield or FX move into contributing factors and components.',
    iconName: 'PieChart',
    promptSeed:
      'Attribute the recent UST 10Y yield move to PCA level / slope / curvature factors.',
    // Phase R4 — tile launches the PCA model builder directly so the
    // user lands on the rich loadings / variance / factor-scores view
    // with the form pre-mounted, instead of round-tripping through the
    // composer.
    builderTool: 'calculate_pca_yield_curve_tool',
  },
  {
    id: 'screen_a_universe',
    label: 'Screen a universe',
    description:
      'Scan across instruments for extremes, regimes, or relative value.',
    iconName: 'Filter',
    promptSeed:
      'Scan G10 sovereigns for instruments above the 1.5σ z-score threshold today.',
  },
  {
    id: 'compare_regimes',
    label: 'Compare regimes',
    description:
      'Explore how relationships change across different market regimes.',
    iconName: 'GitCompare',
    promptSeed:
      'Compare the rolling beta of UST 10Y to 2Y SOFR across steepening vs flattening 2s10s regimes.',
    // Phase R4 — tile launches the rolling-regression builder where the
    // user can pick target + regressor specs and the window, then run
    // to see β / α / R² histories.
    builderTool: 'calculate_rolling_regression_tool',
  },
  {
    id: 'build_custom_dag',
    label: 'Build custom DAG',
    description:
      'Compose your own workflow from primitives and operators.',
    iconName: 'Code',
    soon: true,
    soonReason:
      'Unconstrained DAG composition arrives with the Tier 2 LLM reasoning surface (Phase 2).  In the meantime, describe your analysis in plain English and the copilot will assemble a supported workflow for you.',
    // PR3 — ``soon: true`` disables the tile so ``onSelect`` never
    // fires.  Pre-PR3 this entry carried a placeholder ``promptSeed``
    // ("Compose a new analysis from these primitives and operators: …")
    // that was unreachable code AND misleading copy: it suggested an
    // open-ended composer the backend can't yet execute.  We replace
    // it with the empty string so the only source of truth for the
    // user remains the ``soonReason`` caption.
    promptSeed: '',
  },
];
