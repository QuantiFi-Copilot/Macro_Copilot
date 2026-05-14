// ============================================================================
// persistedModelAdapters.ts — payload-shape adapters for persisted rich-model
//                              artifacts (PR1 — new plan).
// ----------------------------------------------------------------------------
// Closes the persisted-vs-fresh-run shape mismatch identified by Codex's
// PR8 audit: the model-builder live-run path (``ModelWorkspacePage``)
// emits the rich primitive *Output* dict (current_metrics + time_series_*
// fields).  But the SAVED artifact for those tools is the substrate's
// canonical Series body — a single ``time_series_*`` field lifted by
// ``output_field``.  Loadings / variance / current_factor_levels /
// snapshot scalars are NOT in the persisted body.
//
// Backend matrix (verified at PR review time against the actual schemas
// in ``rates_agent/sovereign_bonds/tools/*/schemas.py`` and the
// PrimitiveSpec registrations in ``rates_agent/workflows/__init__.py``):
//
// ┌─────────────────────────────────────────────┬───────────────────────────┬──────────────────────┬──────────────────────────────────────────────┐
// │ Tool name                                   │ Persisted artifact (today)│ Output_artifact_type │ Rich fields ONLY available at live-run        │
// ├─────────────────────────────────────────────┼───────────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
// │ calculate_pca_yield_curve_tool              │ Series (one factor)       │ default ("Series")    │ current_metrics.loadings,                     │
// │                                              │                           │                       │ current_metrics.variance_explained,           │
// │                                              │                           │                       │ current_metrics.current_factor_levels,        │
// │                                              │                           │                       │ current_metrics.tenors_used,                  │
// │                                              │                           │                       │ ALL other factor series                       │
// ├─────────────────────────────────────────────┼───────────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
// │ calculate_rolling_regression_tool            │ Series (one field)        │ default ("Series")    │ current_metrics.latest_betas,                 │
// │                                              │                           │                       │ current_metrics.condition_number,             │
// │                                              │                           │                       │ peer time_series_* fields not lifted          │
// ├─────────────────────────────────────────────┼───────────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
// │ calculate_yield_change_attribution_pca_tool  │ NOT PERSISTABLE today     │ default ("Series")    │ Whole snapshot (output is current_metrics    │
// │                                              │  — output_class has no    │  (would crash on     │  only; no time_series field to lift).         │
// │                                              │  time_series_* field      │  Series-bridge lift) │                                              │
// ├─────────────────────────────────────────────┼───────────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
// │ calculate_half_life_tool                     │ NOT PERSISTABLE today     │ default ("Series")    │ Whole snapshot (output is current_metrics    │
// │                                              │  — pure snapshot output   │                       │  only; no time_series field).                 │
// ├─────────────────────────────────────────────┼───────────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
// │ calculate_beta_adjusted_spread_tool          │ Series (one field)        │ default ("Series")    │ current_metrics (current beta, alpha,         │
// │                                              │                           │                       │ residual z-score)                             │
// └─────────────────────────────────────────────┴───────────────────────────┴──────────────────────┴──────────────────────────────────────────────┘
//
// What this module does
// ---------------------
// Per-tool ``ModelAdapter`` records that:
//   - identify the persisted artifact's role ("factor scores", "rolling
//     beta", "rolling spread"…)
//   - state explicitly which rich fields are NOT in the saved body
//   - point the user at the live builder for the missing-detail view
//   - flag tools whose output class has no time_series field (the
//     "not persistable" branch) so the widget can render a clear
//     "this tool is pure-snapshot today" callout instead of a fake
//     time series
//
// The adapter is PURE: input (toolName, artifactType) → output
// (metadata).  Easy to unit-test.  The actual rendering lives in
// ``PersistedModelView``.
//
// Adding a new model
// ------------------
// 1. Add a backend PrimitiveSpec entry.
// 2. Add a one-record entry to ``ADAPTERS`` below with the per-tool
//    copy.  Closed-family registration; missing entries fall through
//    to ``GENERIC_MODEL_ADAPTER``.
// 3. Register the per-tool widget in ``widgets/index.ts`` if you want
//    the per-tool surface (otherwise the generic SeriesWidget
//    handles the persisted Series rendering).
// ============================================================================

import type {
  ArtifactPayloadResponse,
  ArtifactType,
} from '@/types/artifacts';

/** Closed list of model tool names this module knows about.  The
 *  union is also a runtime registry — adding a new model means
 *  adding it here AND adding an entry to ``ADAPTERS``. */
export type ModelToolName =
  | 'calculate_pca_yield_curve_tool'
  | 'calculate_rolling_regression_tool'
  | 'calculate_yield_change_attribution_pca_tool'
  | 'calculate_half_life_tool'
  | 'calculate_beta_adjusted_spread_tool';

/** What the persisted artifact contains, semantically — used by the
 *  view to label the time series correctly ("Factor scores — first
 *  principal component" vs "Rolling beta" vs "Beta-adjusted spread"). */
export interface PersistedArtifactRole {
  /** Short headline ("PCA · factor scores", "Rolling regression · beta"). */
  headline: string;
  /** One-sentence description of what the saved Series carries. */
  description: string;
}

/** Per-tool adapter metadata.  Stable text strings so tests can
 *  assert on the user-visible copy. */
export interface ModelAdapter {
  /** Backend-canonical tool name. */
  toolName: ModelToolName | 'generic';
  /** Human-readable display name ("PCA", "Rolling regression", …). */
  displayName: string;
  /** Whether the tool's output_class even has a time_series field
   *  that could be lifted as a Series.  When ``false`` the persisted
   *  artifact (if any) is a degenerate shape and the widget renders
   *  a "pure snapshot — re-run for the snapshot view" state. */
  hasTimeSeriesOutput: boolean;
  /** Artifact type the persisted-Series branch expects.  When the
   *  loaded payload's ``artifact_type`` disagrees with this, the
   *  widget renders a type-mismatch fallback. */
  expectedArtifactType: ArtifactType;
  /** Headline + description of what the persisted Series represents
   *  semantically. */
  persistedRole: PersistedArtifactRole;
  /** A short bullet list of rich-detail fields the user might expect
   *  to see but that are NOT in the persisted body.  The view renders
   *  these as an honest "what's not here" list with a pointer to the
   *  live builder. */
  detailUnavailable: string[];
  /** Where the user should go to compute the missing rich detail. */
  builderHint: string;
}

// ----------------------------------------------------------------------------
// Per-tool adapter records
// ----------------------------------------------------------------------------

const PCA_ADAPTER: ModelAdapter = {
  toolName: 'calculate_pca_yield_curve_tool',
  displayName: 'PCA',
  hasTimeSeriesOutput: true,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'PCA · factor-score time series',
    description:
      'The persisted Series carries one principal-component factor score path (one of the multi-factor fits the tool emits).',
  },
  detailUnavailable: [
    'Per-tenor loadings matrix',
    'Variance explained per component (and cumulative)',
    'Current factor levels (latest snapshot)',
    'Component-quality / diagnostics flags',
    'Peer factor series that weren’t lifted as the artifact',
  ],
  builderHint:
    'Re-run from the model builder to view the full loadings / variance / current-factor-levels panel.',
};

const ROLLING_REGRESSION_ADAPTER: ModelAdapter = {
  toolName: 'calculate_rolling_regression_tool',
  displayName: 'Rolling regression',
  hasTimeSeriesOutput: true,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'Rolling regression · rolling coefficient time series',
    description:
      'The persisted Series carries one of the rolling fit outputs (β, α, R², residual, or condition flag) — whichever the workspace selected via output_field.',
  },
  detailUnavailable: [
    'Latest fit snapshot (current β, α, R²)',
    'Numerical-stability condition number',
    'Peer rolling series not lifted as the artifact',
  ],
  builderHint:
    'Re-run from the model builder to inspect the snapshot panel + the peer coefficient series.',
};

const ATTRIBUTION_ADAPTER: ModelAdapter = {
  toolName: 'calculate_yield_change_attribution_pca_tool',
  displayName: 'Yield-change attribution',
  // Backend output_class has NO time_series field — the substrate's
  // Series bridge would crash on lift today.  We surface this honestly
  // rather than render an empty body.
  hasTimeSeriesOutput: false,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'Attribution · snapshot decomposition (not persistable today)',
    description:
      'This tool emits a pure-snapshot output (component-level contribution / residual breakdown).  Today there is no time_series field to lift as a workspace artifact, so persistence isn’t supported end-to-end.',
  },
  detailUnavailable: [
    'Per-component contribution waterfall',
    'Residual + diagnostic flags',
    'Tenor coverage list',
  ],
  builderHint:
    'Open the attribution builder to view the live decomposition.  A future PR may extend the substrate to persist a snapshot row.',
};

const HALF_LIFE_ADAPTER: ModelAdapter = {
  toolName: 'calculate_half_life_tool',
  displayName: 'Half-life',
  hasTimeSeriesOutput: false,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'Half-life · snapshot scalar (not persistable today)',
    description:
      'The tool emits a snapshot scalar (estimated half-life + the AR(1) diagnostics).  No time_series field exists on the output, so workspace persistence isn’t supported end-to-end.',
  },
  detailUnavailable: [
    'Half-life value (in trading days)',
    'AR(1) coefficient + standard error',
    'Mean-reversion direction flag',
  ],
  builderHint:
    'Open the half-life builder to view the live snapshot.',
};

const BETA_ADJUSTED_SPREAD_ADAPTER: ModelAdapter = {
  toolName: 'calculate_beta_adjusted_spread_tool',
  displayName: 'Beta-adjusted spread',
  hasTimeSeriesOutput: true,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'Beta-adjusted spread · rolling residual time series',
    description:
      'The persisted Series carries one of the rolling outputs — the beta time series, the residual spread, or its z-score — whichever the workspace selected via output_field.',
  },
  detailUnavailable: [
    'Latest fit snapshot (current beta + residual z-score)',
    'Peer rolling series not lifted as the artifact',
  ],
  builderHint:
    'Re-run from the model builder to view the snapshot panel + the peer coefficient series.',
};

/** Generic fallback adapter for unregistered or unknown tool names.
 *  Used when the per-tool registry has no entry — the widget renders
 *  the saved Series body with a generic-tone "snapshot" header
 *  instead of crashing. */
export const GENERIC_MODEL_ADAPTER: ModelAdapter = {
  toolName: 'generic',
  displayName: 'persisted artifact',
  hasTimeSeriesOutput: true,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'Persisted artifact',
    description:
      'Loaded from the saved workspace’s artifact payload.',
  },
  detailUnavailable: [],
  builderHint: '',
};

/** Closed registry of model adapters, keyed by tool name. */
const ADAPTERS: Record<ModelToolName, ModelAdapter> = {
  calculate_pca_yield_curve_tool: PCA_ADAPTER,
  calculate_rolling_regression_tool: ROLLING_REGRESSION_ADAPTER,
  calculate_yield_change_attribution_pca_tool: ATTRIBUTION_ADAPTER,
  calculate_half_life_tool: HALF_LIFE_ADAPTER,
  calculate_beta_adjusted_spread_tool: BETA_ADJUSTED_SPREAD_ADAPTER,
};

/** Returns ``true`` if the tool name is one this module has an
 *  adapter for. */
export function isModelTool(toolName: string): toolName is ModelToolName {
  return toolName in ADAPTERS;
}

/** Look up the per-tool adapter.  Returns ``GENERIC_MODEL_ADAPTER``
 *  for unregistered tool names so callers always get a usable
 *  adapter shape. */
export function getModelAdapter(toolName: string): ModelAdapter {
  if (isModelTool(toolName)) return ADAPTERS[toolName];
  return GENERIC_MODEL_ADAPTER;
}

// ----------------------------------------------------------------------------
// View-shape adapter — turns (adapter, payload) into a tagged
// ``AdaptedModelView`` the view layer renders without further
// payload inspection.
// ----------------------------------------------------------------------------

/** Discriminated union the view layer dispatches on.  Each variant
 *  carries enough metadata for the renderer to produce an honest
 *  card without any further payload introspection. */
export type AdaptedModelView =
  | {
      kind: 'persisted_series';
      adapter: ModelAdapter;
      payload: ArtifactPayloadResponse;
    }
  | {
      kind: 'pure_snapshot_unavailable';
      adapter: ModelAdapter;
      payload: ArtifactPayloadResponse;
    }
  | {
      kind: 'shape_mismatch';
      adapter: ModelAdapter;
      payload: ArtifactPayloadResponse;
      expected: ArtifactType;
      got: ArtifactType;
    };

/** Inspect the loaded payload against the per-tool adapter and
 *  decide which view variant to render.  Pure function.  The view
 *  layer dispatches on ``view.kind`` only — it never re-inspects
 *  the payload type. */
export function adaptModelArtifact(args: {
  toolName: string;
  payload: ArtifactPayloadResponse;
}): AdaptedModelView {
  const adapter = getModelAdapter(args.toolName);

  // Pure-snapshot tools (attribution, half-life): even when the
  // payload claims to be a Series, the rich snapshot fields aren't
  // in the body.  Surface this honestly.
  if (!adapter.hasTimeSeriesOutput) {
    return {
      kind: 'pure_snapshot_unavailable',
      adapter,
      payload: args.payload,
    };
  }

  // Time-series-emitting tools: payload should be a Series.  If
  // it's a different type the substrate's bridge lifted something
  // unexpected; render a clear shape-mismatch fallback rather than
  // hand the wrong shape to the body.
  if (args.payload.artifact_type !== adapter.expectedArtifactType) {
    return {
      kind: 'shape_mismatch',
      adapter,
      payload: args.payload,
      expected: adapter.expectedArtifactType,
      got: args.payload.artifact_type,
    };
  }

  return {
    kind: 'persisted_series',
    adapter,
    payload: args.payload,
  };
}
