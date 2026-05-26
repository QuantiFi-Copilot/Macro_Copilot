// ============================================================================
// src/modules/primitives/calculate_nfp_surprise_tool/module.ts
// ----------------------------------------------------------------------------
// Stage 6 — second new-feature primitive through the module-first
// dispatch architecture (Stage 5 shipped CPI Surprise as the first).
// NFP mirrors the CPI pattern: bespoke Monitor card + bespoke Ask
// card, generic Build builder.  Zero edits to BuildShell,
// VirtualPrimitiveCanvas, ConversationCanvas, monitor/registry,
// or any central routing required.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import AskCard from './surfaces/AskCard';
import { NfpSurpriseWidget } from './surfaces/monitor/NfpSurpriseWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_nfp_surprise_tool',
  tiers: ['generic_runnable', 'monitor_surface', 'ask_surface'],
  displayName: 'NFP Surprise',
  category: 'economic_release_surprises',
  oneLineSummary:
    "Per-release US nonfarm-payrolls (NFP) surprise series (actual − consensus_median, in thousands of jobs) plus a rolling z-score over a window of N releases.",
  workspaceLabel: 'NFP surprise — release timeline + rolling z',
  surfaces: {
    ask: AskCard,
  },
  monitorWidgets: [
    {
      id: 'nfp_surprise_latest',
      label: 'NFP Surprise',
      description:
        'Latest US nonfarm-payrolls release — actual vs consensus in thousands, surprise direction, rolling-z signal.',
      category: 'analysis',
      defaultSize: 'medium',
      allowedSizes: ['medium', 'wide'],
      parameterized: false,
      component: NfpSurpriseWidget,
    },
  ],
};
