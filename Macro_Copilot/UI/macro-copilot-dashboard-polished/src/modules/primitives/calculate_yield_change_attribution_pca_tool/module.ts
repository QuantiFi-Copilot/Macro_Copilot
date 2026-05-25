// ============================================================================
// src/modules/primitives/calculate_yield_change_attribution_pca_tool/module.ts — Stage 4b rich-model module.
// ----------------------------------------------------------------------------
// Stage 4b — claims ``custom_build_surface`` + ``custom_preview_widget``;
// sets ``richModel: true``; carries the full ``ModelMetadata`` block
// that the central ``modelRegistry.MODELS`` array now derives from.
//
// Attribution registers the per-tool preview widget against TWO
// artifact types (``Series`` AND ``Panel``).  The widgets/index.ts
// barrel handles the dual registration by walking
// ``ALL_PRIMITIVE_MODULES`` and reading per-module hints — see the
// barrel's per-tool override block for attribution's dual-type entry.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelMetadata } from '@/lib/modelRegistry';
import BuildSurface from './surfaces/BuildSurface';
import PreviewWidget from './surfaces/PreviewWidget';

const MODEL_METADATA: ModelMetadata = {
  toolName: 'calculate_yield_change_attribution_pca_tool',
  displayName: 'Yield-Change Attribution · PCA',
  category: 'attribution',
  modelKind: 'snapshot_model',
  outputRenderer: 'attribution',
  oneLineSummary:
    "Decomposes a single tenor's yield change between two dates into per-PCA-component contributions in basis points.",
  defaultParams: (() => {
    // Default window: ~last quarter, ending today.  ISO YYYY-MM-DD.
    const today = new Date();
    const start = new Date(today);
    start.setDate(start.getDate() - 90);
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    return {
      curve_family: 'UST',
      target_tenor: '10Y',
      start_date: fmt(start),
      end_date: fmt(today),
      n_components: '3',
    };
  })(),
  paramHints: {
    curve_family: { control: 'curve_family', label: 'Curve family' },
    target_tenor: { control: 'tenor', label: 'Target tenor' },
    start_date: {
      control: 'date',
      label: 'Window start',
      help: 'Calendar start of the change window. The tool resolves to the nearest trading day on or after.',
    },
    end_date: {
      control: 'date',
      label: 'Window end',
      help: 'Calendar end of the change window. The tool resolves to the nearest trading day on or before.',
    },
    n_components: {
      control: 'enum',
      label: 'Components',
      help: '3 captures level/slope/curvature on a normal sovereign panel.',
    },
    pasted_loadings: {
      control: 'auto',
      hidden: true,
    },
    pasted_loadings_input: {
      control: 'auto',
      hidden: true,
    },
  },
  interpretationCards: [
    {
      headline: 'Interpretation',
      body: "Total change at the target tenor = sum of component contributions + residual. A residual >5bp on a 3-component decomposition signals atypical curve behaviour the level/slope/curvature basis cannot capture.",
    },
    {
      headline: 'Sign convention',
      body: "Each PC's loading at the longest tenor is locked non-negative. So a positive PC1 contribution on a 10Y means the level component drove a yield rise; a negative PC2 contribution means the slope component compressed (flattening).",
    },
  ],
};

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_yield_change_attribution_pca_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],
  displayName: 'yield_change_attribution_pca',
  category: 'model_fits',
  oneLineSummary:
    'Decompose a sovereign yield change at a given tenor over a window into per-PCA-component contributions in bps.  Loadings come from an inline PCA fit or a caller-supplied pasted payload.',
  richModel: true,
  modelMetadata: MODEL_METADATA,
  surfaces: {
    build: BuildSurface,
    preview: PreviewWidget,
  },
};
