// ============================================================================
// src/modules/primitives/calculate_beta_adjusted_spread_tool/module.ts — Stage 4b rich-model module.
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

const MODEL_METADATA: ModelMetadata = {
  toolName: 'calculate_beta_adjusted_spread_tool',
  displayName: 'Beta-Adjusted Spread',
  category: 'regression',
  modelKind: 'time_series_model',
  outputRenderer: 'series_panel',
  oneLineSummary:
    'Bivariate beta-adjusted RV: rolling hedge ratio of one yield on another, residual in bps, residual z-score.',
  defaultParams: {
    target_spec: { curve_family: 'IT_BTP', tenor: '10Y' },
    hedge_spec: { curve_family: 'DE_BUND', tenor: '10Y' },
    regression_window_days: '60',
    lookback_days: '730',
  },
  paramHints: {
    target_spec: { control: 'series_spec', label: 'Target series' },
    hedge_spec: { control: 'series_spec', label: 'Hedge series' },
    regression_window_days: {
      control: 'window_slider',
      label: 'Hedge-ratio window',
      presets: DEFAULT_WINDOW_PRESETS,
    },
    lookback_days: {
      control: 'lookback_slider',
      label: 'Display lookback',
      presets: DEFAULT_LOOKBACK_PRESETS,
    },
  },
  interpretationCards: [
    {
      headline: 'How to read it',
      body: 'Residual = target - beta × hedge. A residual z-score >2 means the bivariate spread is rich on its own history; <-2 means cheap. The hedge ratio time series itself is the signal when betas drift.',
    },
  ],
};

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_beta_adjusted_spread_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],
  displayName: 'Beta-Adjusted Spread',
  category: 'rolling_analytics',
  oneLineSummary:
    'Bivariate beta-adjusted RV — rolling OLS regresses one sovereign yield (target) on another (regressor); returns hedge ratio (beta), alpha (yield-percent), residual in bps, and a rolling z-score on the residual.',
  richModel: true,
  modelMetadata: MODEL_METADATA,
  modelAdapter: MODEL_ADAPTER,
  workspaceLabel: 'Beta-adjusted spread playground',
  surfaces: {
    build: BuildSurface,
    preview: PreviewWidget,
  },
};
