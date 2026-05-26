// ============================================================================
// src/modules/primitives/calculate_cpi_surprise_tool/module.ts
// ----------------------------------------------------------------------------
// Stage 5 reference implementation — the first new-feature primitive
// shipped through the module-first dispatch architecture.  No edits
// to ``BuildShell``, ``VirtualPrimitiveCanvas``, ``ConversationCanvas``,
// ``monitor/registry.ts``, ``WidgetRenderer.tsx``, or ``toolNames.ts``
// were required to land this module's bespoke Monitor widget + Ask
// card — Stage 5's dispatch layer walks to this module's surfaces
// shelf automatically.
//
// The module ships:
//   * ``generic_runnable`` runtime tier — backend has the primitive
//     in ``_PRIMITIVE_SPECS``; the generic builder is the Build entry
//     when no custom Build surface is declared.
//   * ``monitor_surface`` (Stage 5) — bespoke Monitor card
//     (``surfaces/monitor/CpiSurpriseWidget.tsx``) registered via
//     ``MODULE.monitorWidgets[]``.  Stage 4d's catalog walker
//     discovers it without any edit to monitor/registry.ts.
//   * ``ask_surface`` (Stage 5) — bespoke assistant card
//     (``surfaces/AskCard.tsx``) registered via ``MODULE.surfaces.ask``.
//     Stage 5's ``ConversationCanvas.resolveAssistantCard`` reaches it
//     without any edit to ConversationCanvas.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import AskCard from './surfaces/AskCard';
import { CpiSurpriseWidget } from './surfaces/monitor/CpiSurpriseWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cpi_surprise_tool',
  tiers: ['generic_runnable', 'monitor_surface', 'ask_surface'],
  displayName: 'CPI Surprise',
  category: 'economic_release_surprises',
  oneLineSummary:
    "Per-release CPI surprise series (actual − consensus_median, in percentage points of YoY CPI) for one country's headline CPI YoY print, plus a rolling z-score over a window of N releases.",
  workspaceLabel: 'CPI surprise — release timeline + rolling z',
  surfaces: {
    ask: AskCard,
  },
  monitorWidgets: [
    {
      id: 'cpi_surprise_latest',
      label: 'CPI Surprise',
      description:
        'Latest CPI release per country — actual vs consensus, surprise in percentage points, rolling-z signal.',
      category: 'analysis',
      defaultSize: 'medium',
      allowedSizes: ['medium', 'wide'],
      parameterized: false,
      component: CpiSurpriseWidget,
    },
  ],
};
