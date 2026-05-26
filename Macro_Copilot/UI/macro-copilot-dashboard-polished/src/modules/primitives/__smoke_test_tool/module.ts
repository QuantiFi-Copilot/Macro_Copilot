// ============================================================================
// __smoke_test_tool/module.ts — Stage 5 acceptance-test fixture.
// ----------------------------------------------------------------------------
// This module ISN'T a real backend tool — its purpose is to be a
// canary for the Stage 5 module-first dispatch architecture.  It
// ships every surface kind so the acceptance test can assert each
// dispatch path (BuildShell, VirtualPrimitiveCanvas, ConversationCanvas,
// Monitor catalog walker, persisted-artifact preview walker) walks
// to the module shelf without any central-routing edits.
//
// The tool name starts with ``__`` so:
//   * The backend parity check ignores it (tool names with a ``__``
//     prefix are excluded from the cross-side comparison).
//   * The Library / catalog UIs ignore it (filter applied at the
//     catalogue level).
//   * Real users never see it.
//
// Adding more smoke modules in future Stage N+ work: keep the ``__``
// prefix convention, never reference a real backend tool name.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';
import PreviewWidget from './surfaces/PreviewWidget';
import AskCard from './surfaces/AskCard';
import { SmokeMonitorWidget } from './surfaces/monitor/SmokeWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: '__smoke_test_tool',
  tiers: [
    // ``paused`` is the right runtime semantic for a fixture: backend
    // doesn't ship the tool, frontend exists for the test only.
    // ``deferred`` would force "no capability tiers" per doctrine;
    // ``paused`` allows the capability claims the smoke test needs.
    'paused',
    'custom_build_surface',
    'custom_preview_widget',
    'monitor_surface',
    'ask_surface',
  ],
  displayName: 'Smoke Test (Stage 5 fixture)',
  category: 'snapshots', // any valid category — never user-visible
  oneLineSummary:
    'Stage 5 acceptance-test fixture.  Ships every surface kind so the test bundle can assert module-first dispatch reaches each surface without central-routing edits.',
  surfaces: {
    build: BuildSurface,
    preview: PreviewWidget,
    ask: AskCard,
  },
  monitorWidgets: [
    {
      id: '__smoke_widget',
      label: 'Smoke (Stage 5 fixture)',
      description:
        'Stage 5 acceptance-test fixture.  Confirms the Monitor catalog walker reaches this module.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small'],
      parameterized: false,
      component: SmokeMonitorWidget,
    },
  ],
  // ``paused`` requires ``unsupportedReason`` per the FM6 invariant.
  unsupportedReason: {
    label: 'Smoke Test fixture',
    reason: 'Stage 5 acceptance-test fixture — not a real backend tool.',
    whatWorksNow:
      'Nothing user-facing.  This module exists for the structural acceptance test only.',
  },
};
