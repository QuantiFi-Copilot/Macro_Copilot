// ============================================================================
// src/modules/primitives/calculate_half_life_tool/module.ts — Stage 4b rich-model module.
// ----------------------------------------------------------------------------
// Stage 4b — claims ``custom_build_surface`` + ``custom_preview_widget``;
// sets ``richModel: true``; carries the full ``ModelMetadata`` block
// that the central ``modelRegistry.MODELS`` array now derives from.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelMetadata } from '@/lib/modelRegistry';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import BuildSurface from './surfaces/BuildSurface';
import PreviewWidget from './surfaces/PreviewWidget';

const MODEL_ADAPTER: ModelAdapter = {
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
  builderHint: 'Open the half-life builder to view the live snapshot.',
};

const MODEL_METADATA: ModelMetadata = {
  toolName: 'calculate_half_life_tool',
  displayName: 'Mean-Reversion Half-Life',
  category: 'mean_reversion',
  modelKind: 'snapshot_model',
  outputRenderer: 'auto',
  oneLineSummary:
    'Fits an Ornstein-Uhlenbeck / AR(1) process and reports the half-life of mean reversion — how long it takes a deviation to decay by half.',
  defaultParams: {
    series_spec: { curve_family: 'UST', tenor: '10Y' },
  },
  paramHints: {
    series_spec: { control: 'series_spec', label: 'Series', hidden: false },
    pair_spec: { control: 'auto', hidden: true },
    pasted_series: { control: 'auto', hidden: true },
  },
};

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_half_life_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],
  displayName: 'half_life',
  category: 'model_fits',
  oneLineSummary:
    'Ornstein-Uhlenbeck / AR(1) fit on a supplied series.  Returns half-life of mean reversion (trading days), long-run mean, current deviation, OU β with confidence interval, and a delta-method CI on the half-life itself.',
  richModel: true,
  modelMetadata: MODEL_METADATA,
  modelAdapter: MODEL_ADAPTER,
  workspaceLabel: 'Mean-reversion half-life diagnostics',
  surfaces: {
    build: BuildSurface,
    preview: PreviewWidget,
  },
};
