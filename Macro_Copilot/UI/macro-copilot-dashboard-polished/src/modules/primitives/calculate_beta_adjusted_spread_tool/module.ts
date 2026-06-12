// ============================================================================
// src/modules/primitives/calculate_beta_adjusted_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Consolidation (G-3.2) — migrated from the Stage 4b rich-model route
// RollingRegressionRenderer) to the dual-view standard:
//   - methodology_exposure.md §5 standalone bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/beta-adjusted-spread + own
//     surfaces composed from the rich-model grammar at
//     @/components/shared/build/model)
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// ``modelMetadata`` is REMOVED (its presence routed the decode to the
// legacy BuilderCanvas, bypassing the module-first dual-view dispatch);
// its paramHints live on the spec's own ``paramHints`` field so the
// generic-builder / Ask-handoff control inference keeps its fidelity,
// and the PM interpretation copy moved to the spec's
// ``interpretationCards``.  ``modelAdapter`` + ``surfaces.preview``
// stay UNCHANGED — the persisted-artifact RichModelWidget path still
// reads them (reconciled in the slug-unification workstream).
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import {
  DEFAULT_LOOKBACK_PRESETS,
  DEFAULT_WINDOW_PRESETS,
} from '@/lib/modelPresets';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
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
    'Open the Build surface to view the snapshot panel + the peer coefficient series.',
};

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_beta_adjusted_spread_tool',

  // FM3 — tier claims: dual-view Build (rendering_density.md §1) +
  // the persisted-artifact preview widget (kept until the slug
  // unification reconciles the persisted path).
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],

  // FM5 — display metadata
  displayName: 'Beta-Adjusted Spread',
  category: 'rolling_analytics',
  oneLineSummary:
    'Bivariate beta-adjusted RV — rolling OLS regresses one sovereign yield (target) on another (regressor); returns hedge ratio (beta), alpha (yield-percent), residual in bps, and a rolling z-score on the residual.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  // ``build`` is kept === buildExtended for the legacy
  // VirtualPrimitiveCanvas dispatcher (transitional alias).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
    preview: PreviewWidget,
  },

  // FM5d — persisted-artifact adapter (unchanged; the RichModelWidget
  // path depends on it).
  modelAdapter: MODEL_ADAPTER,

  // FM5 — control hints for the generic builder / Ask-handoff seeding
  // (moved off the retired modelMetadata block; same content).
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

  // PM interpretation copy (moved off the retired modelMetadata block;
  // rendered by the Extended methodology zone).
  interpretationCards: [
    {
      headline: 'How to read it',
      body: 'Residual = target - beta × hedge. A residual z-score >2 means the bivariate spread is rich on its own history; <-2 means cheap. The hedge ratio time series itself is the signal when betas drift.',
    },
  ],

  workspaceLabel: 'Beta-adjusted spread — residual z & hedge ratio',

  // FM5 — defaults mirror the typed-detail bridge's flat wire shape.
  defaultParams: {
    target_curve_family: 'IT_BTP',
    target_tenor: '10Y',
    regressor_curve_family: 'DE_BUND',
    regressor_tenor: '10Y',
    regression_window_days: '60',
    lookback_days: '730',
  },
};
