// ============================================================================
// src/modules/primitives/calculate_otr_ofr_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Stage 6 — first new-feature cash-bond primitive through the
// module-first dispatch architecture.  OTR-OFR is the desk-standard
// rich-cheap / liquidity-premium signal — claims ``monitor_surface``
// for the bento card, generic Build builder for the typed-detail
// chart path.  No bespoke Ask card today (chat result framing
// adds no value over the default research card here).
//
// Parameterised Monitor widget — the user picks curve + tenor at
// add-time via the catalog modal's config form.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  CURVE_OPTIONS,
  TENOR_OPTIONS,
} from '@/lib/monitorParamOptions';
import { OtrOfrSpreadWidget } from './surfaces/monitor/OtrOfrSpreadWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_otr_ofr_spread_tool',
  tiers: ['generic_runnable', 'monitor_surface'],
  displayName: 'OTR-OFR Spread',
  category: 'curve_shape',
  oneLineSummary:
    'Basis-point yield spread between the on-the-run (OTR) bond and the first-off-the-run (OFR) bond for one (country, tenor) sovereign cash-bond slot — the desk-standard rich-cheap / liquidity-premium signal — plus its 252-trading-day rolling z-score and full chartable time series.',
  workspaceLabel: 'OTR-OFR spread chart + history',
  monitorWidgets: [
    {
      id: 'otr_ofr_spread',
      label: 'OTR-OFR Spread',
      description:
        'On-the-run vs first-off-the-run cash-bond spread in bps with rolling-z signal.  Pick curve + tenor.',
      category: 'data',
      defaultSize: 'medium',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Curve',
          defaultValue: 'UST',
          options: CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: TENOR_OPTIONS,
        },
      ],
      component: OtrOfrSpreadWidget,
    },
  ],
};
