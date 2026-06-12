// ============================================================================
// src/modules/primitives/calculate_pca_yield_curve_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view migration (rendering_density.md §1) — PCA leaves the legacy
// rich-model BuilderCanvas route and ships its own
// BuildExtended/BuildCompact pair composed from the shared rich-model
// grammar (@/components/shared/build/model).
//
// ROUTING MECHANICS (THESIS Q3)
// -----------------------------
// contextDecoder routes kind='builder' (legacy BuilderCanvas) whenever
// ``hasModelMetadata(toolName)`` is true, so dropping the block is what
// lets the decode fall through to the module-first dual-view dispatch
// (VirtualPrimitiveCanvas mounts surfaces.buildExtended).
//
// ``modelAdapter`` + ``surfaces.preview`` are KEPT EXACTLY AS-IS — the
// persisted-artifact RichModelWidget path reads them; reconciling that
// path onto the grammar is a separate workstream.
//
// The PM-facing PC1=Level / PC2=Slope / PC3=Curvature interpretation
// copy moved from modelMetadata.interpretationCards onto the spec's
// top-level ``interpretationCards`` field (sourced from the shared
// per-tool constant so the Extended surface renders the same copy).
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { DEFAULT_LOOKBACK_PRESETS } from '@/lib/modelPresets';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import PreviewWidget from './surfaces/PreviewWidget';
import { PCA_INTERPRETATION_CARDS } from './surfaces/pcaYieldCurveShared';

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

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_pca_yield_curve_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * custom_preview_widget — persisted-artifact RichModelWidget card
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],

  // FM5 — display metadata
  displayName: 'PCA · Yield Curve',
  category: 'model_fits',
  oneLineSummary:
    'PCA on the yield-CHANGES panel of one sovereign curve.  Returns per-component loadings, variance shares, factor-score time series, and per-component quality metadata (degenerate + sign-anchor flags).',

  // the module-first dual-view dispatch instead of the legacy
  // BuilderCanvas redirect.

  // PM-facing interpretation copy retained from the retired
  // modelMetadata block (THESIS Q3) — the Extended surface renders the
  // same cards in its methodology zone via the shared constant.
  interpretationCards: PCA_INTERPRETATION_CARDS,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  // ``build`` is a TRANSITIONAL ALIAS kept === buildExtended for the
  // legacy dispatchers (BuildShell's ?builder= branch +
  // VirtualPrimitiveCanvas's fallback chain) until they are retired.
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
    // KEPT UNCHANGED — persisted-artifact RichModelWidget path.
    preview: PreviewWidget,
  },

  // FM5d — persisted-artifact adapter, KEPT UNCHANGED (the
  // RichModelWidget dispatcher reads MODULE.modelAdapter).
  modelAdapter: MODEL_ADAPTER,

  workspaceLabel: 'PCA loadings, variance, factor scores',

  // FM5 — defaults mirror the GET-bridged Input surface
  // (api/routes/rates/detail.py /detail/pca-yield-curve).  ``tenors``
  // is the dual-view comma-joined string form; empty = full playbook
  // universe.
  // FM5 — control hints for the generic builder / Ask-handoff seeding
  // (moved off the retired modelMetadata block; same content —
  // paramHintFor reads the spec first).
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

  defaultParams: {
    curve_family: 'UST',
    tenors: '',
    lookback_days: '1825',
    n_components: '3',
    change_frequency: 'daily',
  },
};
