// ============================================================================
// src/modules/primitives/calculate_half_life_tool/module.ts — dual-view
// rich-model migration (consolidation target #4).
// ----------------------------------------------------------------------------
// MIGRATED off the legacy rich-model chassis (BuilderCanvas /
// false, so contextDecoder no longer routes this tool to the legacy
// BuilderCanvas — the dual-view surfaces below own the Build experience.
// ``modelAdapter`` + ``surfaces.preview`` are KEPT untouched: the
// persisted-artifact RichModelWidget path is orthogonal to the Build
// routing and still needs the per-tool honesty copy.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// Doctrine: rendering_density.md §1 dual-view mandate;
// methodology_exposure.md §5 standalone bridge
// (/api/v1/rates/detail/half-life).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import PreviewWidget from './surfaces/PreviewWidget';

// KEPT UNTOUCHED from the pre-migration module — the persisted-artifact
// RichModelWidget still dispatches on this per-tool copy.
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

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_half_life_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * custom_preview_widget — persisted-artifact card (KEPT from the
  //     pre-migration module; the RichModelWidget path is unchanged)
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],

  // FM5 — display metadata
  displayName: 'Mean-Reversion Half-Life',
  category: 'model_fits',
  oneLineSummary:
    'Ornstein-Uhlenbeck / AR(1) fit on a supplied series.  Returns half-life of mean reversion (trading days), long-run mean, current deviation, OU β with confidence interval, and a delta-method CI on the half-life itself.',

  // route reads the modelRegistry, which derives from ``modelMetadata``
  // — both removed so the dual-view dispatch wins).

  // FM5d — persisted-artifact adapter (KEPT — see header note).
  modelAdapter: MODEL_ADAPTER,

  workspaceLabel: 'Mean-reversion half-life diagnostics',

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build``
  // is kept === buildExtended as the transitional alias for the legacy
  // VirtualPrimitiveCanvas dispatcher; ``preview`` is unchanged.
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
    preview: PreviewWidget,
  },

  // FM5 — defaults mirror the GET /detail/half-life bridge params
  // (single-series mode; pair mode adds curve_family_2 via the mode
  // toggle).  lookback_days default mirrors the route's 1825
  // (~5y calendar — OU CIs at shorter windows are very wide).
  // FM5 — control hints for the generic builder / Ask-handoff seeding
  // (moved off the retired modelMetadata block; same content —
  // paramHintFor reads the spec first).
  paramHints: {
    series_spec: { control: 'series_spec', label: 'Series', hidden: false },
    pair_spec: { control: 'auto', hidden: true },
    pasted_series: { control: 'auto', hidden: true },
  },

  defaultParams: {
    curve_family: 'UST',
    tenor: '10Y',
    lookback_days: '1825',
    field_name: 'YLD_YTM_MID',
  },
};
