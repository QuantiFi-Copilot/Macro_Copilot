// ============================================================================
// src/modules/primitives/calculate_yield_change_attribution_pca_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view migration (rendering_density.md §1) — attribution leaves
// the legacy rich-model BuilderCanvas route and ships its own
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
// ``modelAdapter`` + ``surfaces.preview`` + ``previewArtifactTypes``
// are KEPT EXACTLY AS-IS — the persisted-artifact RichModelWidget path
// reads them (attribution registers under BOTH Series AND Panel);
// reconciling that path onto the grammar is a separate workstream.
//
// The PM-facing Interpretation / Sign-convention copy moved from
// modelMetadata.interpretationCards onto the spec's top-level
// ``interpretationCards`` field (sourced from the shared per-tool
// constant so the Extended surface renders the same copy).
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// The legacy spec computed a default date window via a module-eval
// IIFE — that moved into the surfaces' render-time ``defaultWindow()``
// helper so this file stays a pure value.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import type { ModelAdapter } from '@/components/build/widgets/shared/persistedModelAdapters';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import PreviewWidget from './surfaces/PreviewWidget';
import { ATTRIBUTION_INTERPRETATION_CARDS } from './surfaces/yieldChangeAttributionShared';

const MODEL_ADAPTER: ModelAdapter = {
  toolName: 'calculate_yield_change_attribution_pca_tool',
  displayName: 'Yield-change attribution',
  // Backend output_class has NO time_series field — the substrate's
  // Series bridge would crash on lift today.  We surface this honestly
  // rather than render an empty body.
  hasTimeSeriesOutput: false,
  expectedArtifactType: 'Series',
  persistedRole: {
    headline: 'Attribution · snapshot decomposition (not persistable today)',
    description:
      'This tool emits a pure-snapshot output (component-level contribution / residual breakdown).  Today there is no time_series field to lift as a workspace artifact, so persistence isn’t supported end-to-end.',
  },
  detailUnavailable: [
    'Per-component contribution waterfall',
    'Residual + diagnostic flags',
    'Tenor coverage list',
  ],
  builderHint:
    'Open the attribution builder to view the live decomposition.  A future PR may extend the substrate to persist a snapshot row.',
};

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_yield_change_attribution_pca_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * custom_preview_widget — persisted-artifact RichModelWidget card
  tiers: ['generic_runnable', 'custom_build_surface', 'custom_preview_widget'],

  // FM5 — display metadata
  displayName: 'Yield-Change Attribution · PCA',
  category: 'model_fits',
  oneLineSummary:
    'Decompose a sovereign yield change at a given tenor over a window into per-PCA-component contributions in bps.  Loadings come from an inline PCA fit or a caller-supplied pasted payload.',

  // the module-first dual-view dispatch instead of the legacy
  // BuilderCanvas redirect.

  // PM-facing interpretation copy retained from the retired
  // modelMetadata block (THESIS Q3) — the Extended surface renders the
  // same cards in its methodology zone via the shared constant.
  interpretationCards: ATTRIBUTION_INTERPRETATION_CARDS,

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

  workspaceLabel: 'PCA-based attribution decomposition',

  // FM5e — KEPT UNCHANGED.  Attribution registers under BOTH ``Series``
  // and ``Panel`` because either artifact type can materialise
  // depending on what the bridge lifts.  ``Series`` is the default;
  // ``Panel`` is the addition.
  previewArtifactTypes: ['Panel'],

  // FM5 — static defaults for the GET-bridged Input subset
  // (api/routes/rates/detail.py /detail/yield-change-attribution).
  // The default change window (~trailing quarter) is render-time
  // state in the surfaces (defaultWindow() in
  // surfaces/yieldChangeAttributionShared.ts), NOT a spec value —
  // FM7 forbids the legacy module-eval date computation.
  // FM5 — control hints for the generic builder / Ask-handoff seeding
  // (moved off the retired modelMetadata block; same content —
  // paramHintFor reads the spec first).
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

  defaultParams: {
    curve_family: 'UST',
    target_tenor: '10Y',
    n_components: '3',
    change_frequency: 'daily',
  },
};
