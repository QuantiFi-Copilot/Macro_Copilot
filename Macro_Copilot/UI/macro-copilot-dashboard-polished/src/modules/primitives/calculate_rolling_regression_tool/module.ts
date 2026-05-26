// ============================================================================
// src/modules/primitives/calculate_rolling_regression_tool/module.ts — Stage 4b rich-model module.
// ----------------------------------------------------------------------------
// Stage 4b — claims ``custom_build_surface`` + ``custom_preview_widget``;
// sets ``richModel: true``; carries the full ``ModelMetadata`` block
// that the central ``modelRegistry.MODELS`` array now derives from.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelMetadata } from '@/lib/modelRegistry';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import {
  DEFAULT_LOOKBACK_PRESETS,
  DEFAULT_WINDOW_PRESETS,
} from '@/lib/modelPresets';
import BuildSurface from './surfaces/BuildSurface';
import PreviewWidget from './surfaces/PreviewWidget';

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
    'Re-run from the model builder to inspect the snapshot panel + the peer coefficient series.',
};

const MODEL_METADATA: ModelMetadata = {
  toolName: 'calculate_rolling_regression_tool',
  displayName: 'Rolling Regression',
  category: 'regression',
  modelKind: 'time_series_model',
  outputRenderer: 'rolling_regression',
  oneLineSummary:
    'Trailing-window OLS of one sovereign yield series on one or more regressor yield series. Single methodological knob: window length.',
  defaultParams: {
    target_spec: { curve_family: 'UST', tenor: '10Y' },
    regressor_specs: [{ curve_family: 'UST', tenor: '5Y' }],
    regression_window_days: '60',
    lookback_days: '730',
  },
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
};

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_rolling_regression_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],
  displayName: 'rolling_regression',
  category: 'rolling_analytics',
  oneLineSummary:
    'Rolling OLS regression of one sovereign yield on one or more regressor yields via numpy.linalg.lstsq, returning per-regressor betas, alpha, residual, in-window R², and a condition-number quality flag.',
  richModel: true,
  modelMetadata: MODEL_METADATA,
  modelAdapter: MODEL_ADAPTER,
  workspaceLabel: 'Rolling regression playground',
  surfaces: {
    build: BuildSurface,
    preview: PreviewWidget,
  },
};
