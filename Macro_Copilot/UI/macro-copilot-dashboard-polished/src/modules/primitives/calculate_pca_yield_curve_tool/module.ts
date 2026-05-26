// ============================================================================
// src/modules/primitives/calculate_pca_yield_curve_tool/module.ts — Stage 4b rich-model module.
// ----------------------------------------------------------------------------
// Stage 4b — claims ``custom_build_surface`` (rich-model BuilderCanvas
// route) AND ``custom_preview_widget`` (per-tool persisted-artifact
// card driven by ``RichModelWidget``).  Sets ``richModel: true`` so
// the central decoder treats this tool as a rich-model entry, and
// carries the full ``ModelMetadata`` block via ``modelMetadata`` —
// the central ``modelRegistry.MODELS`` array now derives from this
// field instead of carrying a duplicate hand-authored entry.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelMetadata } from '@/lib/modelRegistry';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import { DEFAULT_LOOKBACK_PRESETS } from '@/lib/modelPresets';
import BuildSurface from './surfaces/BuildSurface';
import PreviewWidget from './surfaces/PreviewWidget';

const MODEL_ADAPTER: ModelAdapter = {
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

const MODEL_METADATA: ModelMetadata = {
  toolName: 'calculate_pca_yield_curve_tool',
  displayName: 'PCA · Yield Curve',
  category: 'pca',
  modelKind: 'composite',
  outputRenderer: 'pca',
  oneLineSummary:
    "Principal-component decomposition of a sovereign curve's yield changes — surfaces level, slope, and curvature factors plus their daily scores.",
  defaultParams: {
    curve_family: 'UST',
    lookback_days: '1825',
    n_components: '3',
    change_frequency: 'daily',
    // tenors: empty array → use the curve's full tenor universe.
    tenors: [],
  },
  paramHints: {
    curve_family: {
      control: 'curve_family',
      label: 'Curve family',
      help: 'Sovereign curve identifier — UST, DE_BUND, IT_BTP, FR_OAT, ES_BONO, UK_GILT, JGB.',
    },
    tenors: {
      control: 'multi_tenor',
      label: 'Tenors (subset)',
      help: 'Optional subset of tenors. Leave empty to use the full universe.',
    },
    lookback_days: {
      control: 'lookback_slider',
      label: 'Lookback (calendar days)',
      help: 'History fetched for the fit. 5 years is the desk-canonical default.',
      presets: DEFAULT_LOOKBACK_PRESETS,
    },
    n_components: {
      control: 'enum',
      label: 'Components to return',
      help: '3 captures level/slope/curvature on a normal sovereign panel.',
    },
    change_frequency: {
      control: 'enum',
      label: 'Change frequency',
      help: 'Differencing step: daily (1d) or weekly (5d).',
    },
    field_name: {
      control: 'auto',
      label: 'Field override',
      help: 'Bloomberg field. Leave blank to use config.yaml default (typically YLD_YTM_MID).',
    },
  },
  interpretationCards: [
    {
      headline: 'PC1 = Level',
      body: 'The first principal component on a normal sovereign curve loads positive across all tenors — it captures parallel shifts in the entire curve. Daily PC1 score moves correspond to broad rate-level moves.',
    },
    {
      headline: 'PC2 = Slope',
      body: 'PC2 typically loads positive at the long end and negative at the short end — it captures steepening vs flattening. PC2 moves track 2s10s and 5s30s dynamics.',
    },
    {
      headline: 'PC3 = Curvature',
      body: 'PC3 typically loads positive at the belly and negative at the wings — it captures butterfly moves. Watch this when belly-rich/cheap views are in play.',
    },
    {
      headline: 'Variance explained',
      body: 'The first three components typically capture >97% of yield-change variance on a developed sovereign. If they do not, the curve is in an atypical regime and the residuals are themselves the signal.',
    },
  ],
};

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_pca_yield_curve_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],
  displayName: 'pca_yield_curve',
  category: 'model_fits',
  oneLineSummary:
    'PCA on the yield-CHANGES panel of one sovereign curve.  Returns per-component loadings, variance shares, factor-score time series, and per-component quality metadata (degenerate + sign-anchor flags).',
  richModel: true,
  modelMetadata: MODEL_METADATA,
  modelAdapter: MODEL_ADAPTER,
  workspaceLabel: 'PCA loadings, variance, factor scores',
  surfaces: {
    build: BuildSurface,
    preview: PreviewWidget,
  },
};
