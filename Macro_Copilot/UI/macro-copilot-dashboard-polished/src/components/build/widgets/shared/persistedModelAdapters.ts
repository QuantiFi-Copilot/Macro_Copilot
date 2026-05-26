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

/** Tool name that an adapter applies to.  Pre-Stage-4d this was a
 *  closed literal union enumerating the 5 known rich-model tools;
 *  Stage 4d moved the per-tool records onto each owning module's
 *  ``MODULE.modelAdapter`` field, so the closed enumeration is gone
 *  and any module may contribute an adapter under its
 *  backend-canonical tool name. */
export type ModelToolName = string;

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

// ----------------------------------------------------------------------------
// Stage 4d — the per-tool adapter records (PCA, rolling regression,
// attribution, half-life, beta-adjusted spread) moved onto each
// owning module's ``MODULE.modelAdapter`` field.  This file now
// derives the registry via ``getPrimitiveModule(toolName)?.modelAdapter``
// instead of holding a hand-authored ``ADAPTERS`` record.  Adding a
// new rich-model tool with a bespoke adapter = ship it on the module
// spec; no further edit to this file.
// ----------------------------------------------------------------------------

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

// Lazy module lookup — defer the ``getPrimitiveModule`` call to first
// invocation so this file's module-init never reads ``@/modules``
// directly (avoids the persistedModelAdapters ↔ modules ↔ RichModelWidget
// cycle through each rich-model module's PreviewWidget surface).
import { getPrimitiveModule } from '@/modules';

/** Returns ``true`` if the tool name has a per-tool adapter declared
 *  on its owning module's spec (``MODULE.modelAdapter``). */
export function isModelTool(toolName: string): toolName is ModelToolName {
  return getPrimitiveModule(toolName)?.modelAdapter != null;
}

/** Look up the per-tool adapter.  Reads ``MODULE.modelAdapter`` from
 *  the owning primitive module; falls back to ``GENERIC_MODEL_ADAPTER``
 *  when the tool has no module entry or no adapter declared. */
export function getModelAdapter(toolName: string): ModelAdapter {
  return getPrimitiveModule(toolName)?.modelAdapter ?? GENERIC_MODEL_ADAPTER;
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
