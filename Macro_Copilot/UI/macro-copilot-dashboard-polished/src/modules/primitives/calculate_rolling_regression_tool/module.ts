// ============================================================================
// src/modules/primitives/calculate_rolling_regression_tool/module.ts
// ----------------------------------------------------------------------------
// Migrated to the dual-view rendering-density standard (consolidation
// target #4 — the rich models join the standalone-bridge + dual-view
// contract the breakeven pilot established):
//   - methodology_exposure.md §5 standalone bridge — own typed-detail
//     endpoint at GET /api/v1/rates/detail/rolling-regression, consumed
//     by BOTH Build views via ``fetchDetailRollingRegression``.
//   - rendering_density.md §1 dual-view mandate — buildExtended +
//     buildCompact both REQUIRED, composed from the shared rich-model
//     grammar at @/components/shared/build/model.
//
// ``modelMetadata`` is intentionally ABSENT (it used to live here):
// contextDecoder routes ``kind: 'builder'`` (the legacy BuilderCanvas
// playground) whenever a tool has a model-registry entry, which is
// lets dispatch fall through to ``surfaces.buildExtended`` (single-tool
// queries) and ``surfaces.buildCompact`` (multi-tool DAG nodes).  The
// PM-read copy the legacy interpretation cards carried is preserved on
// the spec's top-level ``interpretationCards`` and threaded through the
// extended view's panel descriptions + methodology zone.
//
// The PERSISTED-ARTIFACT path is unchanged: ``modelAdapter`` +
// ``surfaces.preview`` (RichModelWidget) keep rendering persisted
// Series artifacts in completed workspaces — that path never depended
// on modelMetadata.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  DEFAULT_LOOKBACK_PRESETS,
  DEFAULT_WINDOW_PRESETS,
} from '@/lib/modelPresets';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import PreviewWidget from './surfaces/PreviewWidget';

// FM5d — persisted-artifact adapter (UNCHANGED by the dual-view
// migration; consumed by RichModelWidget via getModelAdapter).
const MODEL_ADAPTER: ModelAdapter = {
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
    'Open the Build surface to inspect the latest-fit snapshot + the peer coefficient series.',
};

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_rolling_regression_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * custom_preview_widget — persisted-artifact RichModelWidget card
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],

  // FM5 — display metadata
  displayName: 'Rolling Regression',
  category: 'rolling_analytics',
  oneLineSummary:
    'Rolling OLS regression of one sovereign yield on one or more regressor yields via numpy.linalg.lstsq, returning per-regressor betas, alpha, residual, in-window R², and a condition-number quality flag.',


  // FM5 — flat-wire defaults; keys match the typed-detail endpoint's
  // query params.  The regressor lists are COMMA-JOINED strings — the
  // convention is documented in surfaces/rollingRegressionShared.ts.
  // FM5 — control hints for the generic builder / Ask-handoff seeding
  // (moved off the retired modelMetadata block; same content —
  // paramHintFor reads the spec first).
  paramHints: {
    target_spec: {
      control: 'series_spec',
      label: 'Target series',
      help: 'The y in the regression — a single sovereign (curve, tenor) yield series.',
    },
    regressor_specs: {
      control: 'series_spec_list',
      label: 'Regressor series',
      help: 'One or more (curve, tenor) yield series — the columns of X.',
    },
    regression_window_days: {
      control: 'window_slider',
      label: 'Window (trading days)',
      help: 'Trailing-window length per rolling fit. Tactical = 60, annual = 252.',
      presets: DEFAULT_WINDOW_PRESETS,
    },
    lookback_days: {
      control: 'lookback_slider',
      label: 'Display lookback (calendar days)',
      help: 'Calendar days of history to render. Does NOT change the rolling-window length.',
      presets: DEFAULT_LOOKBACK_PRESETS,
    },
  },

  defaultParams: {
    target_curve_family: 'UST',
    target_tenor: '10Y',
    regressor_curve_families: 'UST',
    regressor_tenors: '5Y',
    regression_window_days: '60',
    lookback_days: '730',
    field_name: 'YLD_YTM_MID',
  },

  // PM-read interpretation copy preserved from the retired
  // modelMetadata block (the extended view threads the same reads into
  // its panel descriptions + methodology rows).
  interpretationCards: [
    {
      headline: 'How to read the betas',
      body: 'Each beta time series is the partial elasticity of the target on that regressor at the rolling window date — the rest of the regressors held flat. A beta crossing 1 means the target moves one-for-one with the regressor in that window.',
    },
    {
      headline: 'When R² collapses',
      body: 'A sharp drop in rolling R² is a structural-break tell — the linear hedge ratio has lost predictive power for that horizon. Sustained low R² with high volatility is a regime-shift warning.',
    },
    {
      headline: 'Condition flag',
      body: 'A row flagged 1 means the design matrix was near-singular (e.g. two regressors became collinear) — that fit was suppressed and the row should be masked from interpretation.',
    },
  ],

  // FM5d — persisted-artifact path, untouched by the migration.
  modelAdapter: MODEL_ADAPTER,
  workspaceLabel: 'Rolling regression — betas, R², residual',

  // FM8 — dual Build-side surfaces (rendering_density.md §5) + the
  // persisted preview.  ``build`` is a transitional alias kept ===
  // buildExtended for the legacy dispatchers (BuildShell ``?builder=``
  // branch + assertStandardModuleInvariants' capability mapping).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
    preview: PreviewWidget,
  },
};
