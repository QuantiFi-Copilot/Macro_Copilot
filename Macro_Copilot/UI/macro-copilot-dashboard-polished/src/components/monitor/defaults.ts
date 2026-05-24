// ============================================================================
// Default layouts — Monitor (Home), Rates Agent, FX Agent
// ----------------------------------------------------------------------------
// The first time a user lands on a widget surface, they get a sensible
// default layout so the page isn't empty.  After that, their custom
// layout takes over (persisted in localStorage by `useWidgetLayout`).
//
// Defaults differ by surface:
//   - Monitor: cross-asset summary, headline-only.  Yield snapshot +
//     scanner + cross-market + curve shapes + regime + a single yield
//     level.  Pre-aggregated widgets only — no parameterized
//     placeholders that would confuse a first-time user.
//   - Rates Agent: rates-deep.  Same backbone + a UST 2s10s spread
//     chart, a BTP-Bund cross-market spread, and individual yield
//     levels for the four major curves.
//   - FX Agent: spot snapshot + scanner + carry monitor.  Pre-
//     aggregated only in V1; parameterized FX widgets (custom pair,
//     custom tenor) land alongside the next FX tools.
//
// "Reset to default" inside the Customize menu writes one of these back.
// ============================================================================

import type { LayoutState, WidgetInstance } from './registry';
import { LAYOUT_VERSION, buildWidgetInstance } from './registry';

/** Builds a layout from a list of (typeId, optional params) tuples.
 *  Drops any tuples whose type isn't in the registry — defensive
 *  against catalog renames between deploys. */
function buildLayout(
  entries: Array<readonly [string, Partial<WidgetInstance>?]>,
): LayoutState {
  const widgets: WidgetInstance[] = [];
  for (const [typeId, overrides] of entries) {
    const inst = buildWidgetInstance(typeId, overrides);
    if (inst) widgets.push(inst);
  }
  return { version: LAYOUT_VERSION, widgets };
}

export function defaultMonitorLayout(): LayoutState {
  return buildLayout([
    ['yield_snapshot'],
    ['scanner'],
    ['cross_market_spreads'],
    ['curve_spreads'],
    ['curve_classifier'],
    [
      'yield_level',
      {
        size: 'small',
        params: { curve_family: 'UST', tenor: '10Y', lookback_days: '252' },
      },
    ],
  ]);
}

export function defaultRatesAgentLayout(): LayoutState {
  return buildLayout([
    ['yield_snapshot'],
    ['scanner'],
    ['cross_market_spreads'],
    ['curve_spreads'],
    ['curve_classifier'],
    [
      'spread_chart',
      {
        size: 'medium',
        params: {
          curve_family: 'UST',
          short_tenor: '2Y',
          long_tenor: '10Y',
          lookback_days: '252',
        },
      },
    ],
    [
      'cross_market_spread',
      {
        size: 'medium',
        params: {
          curve_family_1: 'IT_BTP',
          curve_family_2: 'DE_BUND',
          tenor: '10Y',
          lookback_days: '252',
        },
      },
    ],
    [
      'yield_level',
      {
        size: 'small',
        params: { curve_family: 'UST', tenor: '10Y', lookback_days: '252' },
      },
    ],
    [
      'yield_level',
      {
        size: 'small',
        params: { curve_family: 'DE_BUND', tenor: '10Y', lookback_days: '252' },
      },
    ],
    [
      'yield_level',
      {
        size: 'small',
        params: { curve_family: 'JGB', tenor: '10Y', lookback_days: '252' },
      },
    ],
    [
      'yield_level',
      {
        size: 'small',
        params: { curve_family: 'IT_BTP', tenor: '10Y', lookback_days: '252' },
      },
    ],
  ]);
}

export function defaultFxAgentLayout(): LayoutState {
  return buildLayout([
    ['fx_spot_snapshot'],
    ['fx_scanner'],
    [
      'fx_carry',
      {
        params: {
          tenor: '1M',
          rank_by: 'carry_signed',
          top_n: 6,
          lookback_days: '365',
        },
      },
    ],
    [
      'fx_forward_curve',
      {
        params: { pair: 'EURUSD', lookback_days: '365' },
      },
    ],
  ]);
}
